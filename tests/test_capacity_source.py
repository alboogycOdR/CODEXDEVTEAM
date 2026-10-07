import json
from pathlib import Path
import queue
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codexdevteam_kernel.capacity_source import (
    CodexAppServerCapacitySource,
    _shared_codex_quota_remaining,
    _read_response,
)
from codexdevteam_kernel.identity import WorkerIdentity
from codexdevteam_kernel.dispatch import CapacityObservation
from codexdevteam_kernel.host_config import WindowsHostConfig
from codexdevteam_kernel.registry import WorkerDefinition, WorkerRegistry


def rate_limits(*, allowed=True, used=20, secondary_used=None, buckets=None,
                model_slug=None):
    bucket = {"limitId": "codex", "normalModelSlug": model_slug,
              "primary": {"usedPercent": used, "windowDurationMins": 300,
                          "resetsAt": 1_800_000_000}}
    if secondary_used is not None:
        bucket["secondary"] = {"usedPercent": secondary_used,
                               "windowDurationMins": 10080,
                               "resetsAt": 1_800_000_000}
    return {"ordinaryUsageAllowed": allowed,
            "rateLimitsByLimitId": buckets if buckets is not None else {"codex": bucket}}


def worker(unit_id, runtime="codex", model="gpt-configured"):
    return WorkerDefinition(WorkerIdentity(unit_id, "implementation", "advanced",
                                           runtime, model))


_DEFAULT_RESPONSE = object()


class FakeAppServer:
    """Small JSONL peer that responds only after each request is written."""

    def __init__(self, *, account_response=_DEFAULT_RESPONSE, init_error=False):
        self.received = []
        self.lines = queue.Queue()
        self.returncode = None
        self.account_response = (rate_limits() if account_response is _DEFAULT_RESPONSE
                                 else account_response)
        self.init_error = init_error
        self.stdin = self
        self.stdout = self

    def write(self, line):
        message = json.loads(line)
        self.received.append(message)
        if message.get("method") == "initialize":
            result = {"error": {"message": "private detail"}} if self.init_error else {"result": {}}
            self.lines.put(json.dumps({"id": message["id"], **result}) + "\n")
        elif message.get("method") == "account/rateLimits/read" and self.account_response is not None:
            self.lines.put(json.dumps({"id": message["id"], "result": self.account_response}) + "\n")

    def flush(self):
        pass

    def close(self):
        self.lines.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.lines.get()
        if line is None:
            raise StopIteration
        return line

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 0


class CodexAppServerCapacityTests(unittest.TestCase):
    def test_host_config_defaults_to_live_source_and_allows_explicit_snapshot_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            values = {
                "protocol_version": 1,
                "state_db": ".codexdevteam/state/state.sqlite",
                "registry": ".codexdevteam/framework/registry.json",
                "verification_config": ".codexdevteam/framework/verification.json",
                "capacity_snapshot": ".codexdevteam/control/capacity.json",
                "worktree_root": "../{project_name}-codexdevteam-worktrees",
                "control_root": ".codexdevteam/control",
                "logs_root": ".codexdevteam/logs",
                "system_id": "test-system", "instance_id": "test-instance",
            }
            config = WindowsHostConfig.from_dict(root, values)
            self.assertEqual(config.capacity_source, "codex_app_server")
            manual = WindowsHostConfig.from_dict(root, {**values,
                                                        "capacity_source": "snapshot"})
            self.assertEqual(manual.to_dict()["capacity_source"], "snapshot")
            with self.assertRaisesRegex(ValueError, "capacity_source"):
                WindowsHostConfig.from_dict(root, {**values, "capacity_source": []})

    def test_uses_single_shared_bucket_for_each_configured_codex_worker(self):
        source = CodexAppServerCapacitySource(("codex",))
        with patch.object(source, "_read_rate_limits", return_value=rate_limits(
                used=10, secondary_used=35)):
            observations = source.observe((worker("maker-a", model="model-a"),
                                           worker("maker-b", model="model-b")), now=1000)
        self.assertEqual(set(observations), {"maker-a", "maker-b"})
        for observation in observations.values():
            self.assertTrue(observation.available)
            self.assertEqual(observation.free_slots, 1)
            self.assertEqual(observation.quota_remaining, 65)
            self.assertEqual(observation.observed_at, 1000)
            self.assertTrue(observation.eligible(now=1001))

    def test_host_source_selects_live_adapter_using_bound_codex_executable(self):
        from codexdevteam_kernel.capacity_source import load_host_capacity
        worker_def = worker("maker")
        registry = WorkerRegistry(defined={"maker": worker_def}, active=("maker",))
        config = SimpleNamespace(capacity_source="codex_app_server", project_root=Path.cwd())
        expected = {"maker": CapacityObservation(True, 1, 1000, quota_remaining=80)}
        with patch.object(CodexAppServerCapacitySource, "observe", return_value=expected) as observe:
            result = load_host_capacity(config, registry, command_prefix=("codex.exe",))
        self.assertEqual(result, expected)
        observe.assert_called_once_with((worker_def,))

    def test_runtime_capacity_is_queried_again_for_each_cycle_refresh(self):
        from codexdevteam_kernel.host_runtime import HostRuntimeBindings, load_runtime_capacity
        worker_def = worker("maker")
        registry = WorkerRegistry(defined={"maker": worker_def}, active=("maker",))
        bindings = HostRuntimeBindings(
            registry=registry,
            adapters={"codex": SimpleNamespace(executable="codex.exe")},
            gate_commands={}, protected_paths=(), environment_allowlist=(),
            task_class_policy=None, ignored_paths_allowlist=(),
        )
        config = SimpleNamespace(capacity_source="codex_app_server")
        first = {"maker": CapacityObservation(True, 1, 1000, quota_remaining=80)}
        refreshed = {"maker": CapacityObservation(True, 1, 1001, quota_remaining=65)}
        with patch("codexdevteam_kernel.host_runtime._load_host_capacity",
                   side_effect=(first, refreshed)) as source:
            self.assertEqual(load_runtime_capacity(config, bindings), first)
            self.assertEqual(load_runtime_capacity(config, bindings), refreshed)
        self.assertEqual(source.call_count, 2)

    def test_explicit_snapshot_mode_does_not_launch_provider_adapter(self):
        from codexdevteam_kernel.capacity_source import load_host_capacity
        worker_def = worker("maker")
        registry = WorkerRegistry(defined={"maker": worker_def}, active=("maker",))
        expected = {"maker": CapacityObservation(True, 1, 1000)}
        config = SimpleNamespace(capacity_source="snapshot",
                                  load_capacity_observations=lambda: expected)
        with patch.object(CodexAppServerCapacitySource, "observe") as observe:
            self.assertEqual(load_host_capacity(config, registry, command_prefix=()), expected)
        observe.assert_not_called()

    def test_backend_denial_produces_unavailable_capacity(self):
        source = CodexAppServerCapacitySource(("codex",))
        with patch.object(source, "_read_rate_limits", return_value=rate_limits(allowed=False)):
            observation = source.observe((worker("maker"),), now=1000)["maker"]
        self.assertFalse(observation.available)
        self.assertEqual(observation.free_slots, 0)
        self.assertEqual(observation.quota_remaining, 0)
        self.assertFalse(observation.eligible(now=1001))

    def test_exhausted_window_blocks_even_when_other_window_has_quota(self):
        source = CodexAppServerCapacitySource(("codex",))
        with patch.object(source, "_read_rate_limits", return_value=rate_limits(
                used=100, secondary_used=10)):
            observation = source.observe((worker("maker"),), now=1000)["maker"]
        self.assertFalse(observation.available)
        self.assertEqual(observation.quota_remaining, 0)

    def test_rejects_ambiguous_model_specific_or_unknown_buckets(self):
        base = rate_limits()
        with self.assertRaisesRegex(ValueError, "model-specific or unknown"):
            _shared_codex_quota_remaining({**base, "rateLimitsByLimitId": {
                "codex": base["rateLimitsByLimitId"]["codex"],
                "special-model": {"limitId": "special-model"},
            }})
        with self.assertRaisesRegex(ValueError, "model-specific"):
            _shared_codex_quota_remaining(rate_limits(model_slug="gpt-special"))

    def test_rejects_missing_permission_bucket_and_malformed_windows(self):
        with self.assertRaisesRegex(ValueError, "permission"):
            _shared_codex_quota_remaining({"ordinaryUsageAllowed": None})
        with self.assertRaisesRegex(ValueError, "shared Codex bucket"):
            _shared_codex_quota_remaining({"ordinaryUsageAllowed": True,
                                           "rateLimitsByLimitId": {}})
        with self.assertRaisesRegex(ValueError, "invalid usage percentage"):
            _shared_codex_quota_remaining(rate_limits(used=101))

    def test_does_not_apply_codex_account_limit_to_other_runtime(self):
        source = CodexAppServerCapacitySource(("codex",))
        with patch.object(source, "_read_rate_limits") as read:
            with self.assertRaisesRegex(ValueError, "non-Codex"):
                source.observe((worker("claude-worker", runtime="claude"),), now=1000)
        read.assert_not_called()

    def test_app_server_request_is_bounded_and_contains_only_read_protocol(self):
        source = CodexAppServerCapacitySource(("codex.exe",), timeout_seconds=7)
        process = FakeAppServer()
        with patch("codexdevteam_kernel.capacity_source.subprocess.Popen",
                   return_value=process) as run:
            source._read_rate_limits()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["codex.exe", "app-server", "--listen", "stdio://"])
        self.assertEqual(source.timeout_seconds, 7)
        messages = process.received
        self.assertEqual([item["method"] for item in messages], [
            "initialize", "initialized", "account/rateLimits/read"])
        self.assertTrue(messages[0]["params"]["capabilities"]["experimentalApi"])
        self.assertTrue(messages[2]["params"]["excludeResetCreditDetails"])
        self.assertNotIn("jsonrpc", messages[0])

    def test_app_server_errors_fail_closed_without_echoing_server_detail(self):
        source = CodexAppServerCapacitySource(("codex",))
        with patch("codexdevteam_kernel.capacity_source.subprocess.Popen",
                   return_value=FakeAppServer(account_response=[])):
            with self.assertRaisesRegex(ValueError, "could not read account capacity") as error:
                source._read_rate_limits()
        self.assertNotIn("private detail", str(error.exception))
        with patch("codexdevteam_kernel.capacity_source.subprocess.Popen",
                   return_value=FakeAppServer(init_error=True)):
            with self.assertRaisesRegex(ValueError, "initialize handshake failed"):
                source._read_rate_limits()

    def test_app_server_times_out_if_account_response_never_arrives(self):
        source = CodexAppServerCapacitySource(("codex",), timeout_seconds=0.05)
        with patch("codexdevteam_kernel.capacity_source.subprocess.Popen",
                   return_value=FakeAppServer(account_response=None)):
            with self.assertRaisesRegex(ValueError, "did not return an account capacity response"):
                source._read_rate_limits()

    def test_app_server_notification_stream_has_a_total_size_bound(self):
        output = queue.Queue()
        notification = json.dumps({"method": "status/changed",
                                   "padding": "x" * 600_000}) + "\n"
        self.assertLess(len(notification.encode("utf-8")), 1_048_576)
        output.put(notification)
        output.put(notification)
        output.put(json.dumps({"id": 2, "result": {}}) + "\n")
        with self.assertRaisesRegex(ValueError, "size limit"):
            _read_response(output, time.monotonic() + 1, expected_id=2, methods=set())


if __name__ == "__main__":
    unittest.main()
