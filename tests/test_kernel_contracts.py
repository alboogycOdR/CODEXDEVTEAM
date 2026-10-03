import unittest
import json
from unittest.mock import patch
import tempfile
from pathlib import Path
import subprocess
import sqlite3
import sys
import os
import hashlib
import time
import io
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from codexdevteam_kernel import (
    TaskState,
    WorkerIdentity,
    allowed_transition,
    decide_write,
    validate_grant,
    LeaseError,
    PlanProjectionPending,
    StateStore,
    TaskRecord,
    TerritoryPolicy,
    validate_task_set,
    GitWorktreeManager,
    WorktreeError,
    find_secrets,
    parse_plan_markdown,
    WorkerRegistry,
    ControlMessage,
    validate_control,
    GateRunner,
    ReviewVerdict,
    validate_review,
    assign_task,
    eligible_workers,
    StagnationSample,
    is_stagnant,
    remedial_kind,
    stale_signal,
    update_streak,
    grant_within_owned,
    OnboardingMode,
    inspect_project,
    plan_framework_sync,
    apply_framework_sync,
    SyncConflict,
    SyncAction,
    SyncPlan,
    three_way_merge_json,
    CapacityObservation,
    DispatchError,
    Supervisor,
    SupervisorPolicy,
    PlanWriteConflict,
    patch_plan_task_state,
    InstallationConflict,
    install_fresh_project,
    install_devdepartment_sidecar,
    upgrade_codexdevteam_project,
    TestRunCache,
    TestRunError,
    CodexExecAdapter,
    InvocationRequest,
    InvocationResult,
    request_for_worker,
    EvidenceMemory,
    decide_publication,
    plan_archive,
    append_archive_blocks,
    read_archived_block,
    ArchiveConflict,
    plan_notes_rotation,
    append_notes_rotation,
)
from codexdevteam_kernel.review import gate_artifact_findings


class PublicationPolicyTests(unittest.TestCase):
    def test_every_policy_allows_each_explicit_publication_boundary(self):
        for event in ("bookkeeping", "merge", "park"):
            with self.subTest(event=event):
                decision = decide_publication({"enabled": True, "push_policy": "every"}, {},
                                              event=event, now=100)
                self.assertTrue(decision.publish)
                self.assertEqual(decision.next_window_start, 100)

    def test_batch_policy_persists_window_and_publishes_at_boundary(self):
        config = {"enabled": True, "push_policy": "batch", "push_batch_minutes": 30}
        first = decide_publication(config, {}, event="bookkeeping", now=100)
        self.assertFalse(first.publish)
        self.assertEqual(first.next_window_start, 100)
        early = decide_publication(config, {"window_start": first.next_window_start},
                                   event="bookkeeping", now=1899)
        self.assertFalse(early.publish)
        self.assertEqual(early.next_window_start, 100)
        boundary = decide_publication(config, {"window_start": early.next_window_start},
                                      event="bookkeeping", now=1900)
        self.assertTrue(boundary.publish)
        self.assertEqual(boundary.next_window_start, 1900)

    def test_batch_clock_rollback_resets_window_and_boundaries_bypass_batch(self):
        config = {"enabled": True, "push_policy": "batch", "push_batch_minutes": 30}
        reset = decide_publication(config, {"window_start": 200},
                                   event="bookkeeping", now=100)
        self.assertFalse(reset.publish)
        self.assertEqual(reset.next_window_start, 100)
        for event in ("merge", "park"):
            with self.subTest(event=event):
                self.assertTrue(decide_publication(
                    config, {"window_start": 200}, event=event, now=100).publish)

    def test_merge_only_and_legacy_local_only_policy(self):
        config = {"enabled": True, "push_policy": "merge_only"}
        self.assertFalse(decide_publication(config, {}, event="bookkeeping", now=100).publish)
        self.assertTrue(decide_publication(config, {}, event="merge", now=100).publish)
        legacy = decide_publication({}, {"window_start": 50}, event="park",
                                    now=100, only_if_configured=True)
        self.assertFalse(legacy.publish)
        self.assertEqual(legacy.next_window_start, 50)

    def test_publication_requires_explicit_enablement_independent_of_head_or_event(self):
        for event in ("bookkeeping", "merge", "park"):
            with self.subTest(event=event):
                decision = decide_publication({"push_policy": "every"}, {},
                                              event=event, now=100)
                self.assertFalse(decision.publish)
                self.assertEqual(decision.reason,
                                 "publication is disabled by configuration")

    def test_invalid_publication_inputs_fail_closed(self):
        for config, state, event, now in (
            ({"enabled": True, "push_policy": "immediate"}, {}, "park", 100),
            ({"enabled": True, "push_policy": "batch", "push_batch_minutes": float("nan")}, {},
             "park", 100),
            ({"enabled": True, "push_policy": "every"}, {"window_start": True}, "park", 100),
            ({"enabled": True, "push_policy": "every"}, {}, "unknown", 100),
            ({"enabled": True, "push_policy": "every"}, {}, "park", float("inf")),
            ({"enabled": "yes", "push_policy": "every"}, {}, "park", 100),
            ({"enabled": True}, {}, "park", 100),
        ):
            with self.subTest(config=config, event=event, now=now):
                with self.assertRaises(ValueError):
                    decide_publication(config, state, event=event, now=now)


class IdentityTests(unittest.TestCase):
    def test_dimensions_are_independent_values(self):
        worker = WorkerIdentity("worker-a", "implementation", "standard", "codex", "configured-model")
        self.assertEqual((worker.role, worker.capability_floor, worker.runtime, worker.model),
                         ("implementation", "standard", "codex", "configured-model"))

    def test_empty_identity_dimension_is_rejected(self):
        with self.assertRaises(ValueError):
            WorkerIdentity("worker-a", "implementation", "standard", "codex", " ")


class RuntimeAdapterTests(unittest.TestCase):
    def test_process_identity_fingerprint_detects_pid_reuse(self):
        from codexdevteam_kernel.process_identity import (
            ProcessIdentity, ProcessObservation, capture_process_identity,
            observe_process_identity, process_identity_matches,
        )

        identity = capture_process_identity(os.getpid())
        self.assertTrue(process_identity_matches(identity))
        self.assertEqual(observe_process_identity(identity), ProcessObservation.MATCHES)
        reused_pid = ProcessIdentity(identity.pid, identity.start_token + ":different",
                                     identity.process_group_id)
        self.assertFalse(process_identity_matches(reused_pid))
        self.assertEqual(observe_process_identity(reused_pid), ProcessObservation.PID_REUSED)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            capture_process_identity(0)

    @patch("codexdevteam_kernel.process_identity.capture_process_identity",
           side_effect=FileNotFoundError)
    def test_process_identity_observation_distinguishes_absence(self, _capture):
        from codexdevteam_kernel.process_identity import ProcessIdentity, ProcessObservation
        from codexdevteam_kernel.process_identity import observe_process_identity

        self.assertEqual(observe_process_identity(ProcessIdentity(42, "boot:1", 42)),
                         ProcessObservation.ABSENT)

    @patch("codexdevteam_kernel.process_identity.capture_process_identity",
           side_effect=PermissionError)
    def test_process_identity_observation_fails_closed_when_unverifiable(self, _capture):
        from codexdevteam_kernel.process_identity import ProcessIdentity, ProcessObservation
        from codexdevteam_kernel.process_identity import observe_process_identity

        self.assertEqual(observe_process_identity(ProcessIdentity(42, "boot:1", 42)),
                         ProcessObservation.UNVERIFIABLE)

    def test_linux_pidfd_reaper_kills_only_the_recorded_group(self):
        if (os.name != "posix" or not hasattr(os, "pidfd_open")
                or not hasattr(__import__("signal"), "pidfd_send_signal")
                or not Path("/proc/sys/kernel/random/boot_id").is_file()):
            self.skipTest("Linux pidfd process-group reaping is unavailable")
        from codexdevteam_kernel.process_identity import capture_process_identity
        from codexdevteam_kernel.process_reaper import ProcessReapStatus, reap_process_group

        child_code = ("import subprocess,sys,time; "
                      "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
                      "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(30)")
        process = subprocess.Popen([sys.executable, "-c", child_code], start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            identity = capture_process_identity(process.pid)
            result = reap_process_group(identity, grace_seconds=0.05, timeout_seconds=3)
            self.assertEqual(result.status, ProcessReapStatus.GROUP_TERMINATED)
            self.assertTrue(result.verified)
            process.wait(timeout=3)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def test_linux_pidfd_reaper_refuses_pid_reuse_without_signaling(self):
        if (os.name != "posix" or not hasattr(os, "pidfd_open")
                or not hasattr(__import__("signal"), "pidfd_send_signal")
                or not Path("/proc/sys/kernel/random/boot_id").is_file()):
            self.skipTest("Linux pidfd process-group reaping is unavailable")
        from codexdevteam_kernel.process_identity import ProcessIdentity, capture_process_identity
        from codexdevteam_kernel.process_reaper import ProcessReapStatus, reap_process_group

        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   start_new_session=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            identity = capture_process_identity(process.pid)
            reused = ProcessIdentity(identity.pid, identity.start_token + ":different",
                                      identity.process_group_id)
            result = reap_process_group(reused)
            self.assertEqual(result.status, ProcessReapStatus.IDENTITY_MISMATCH)
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=3)

    def test_reaper_refuses_the_supervisor_process_group(self):
        from unittest.mock import Mock
        from codexdevteam_kernel.process_identity import ProcessIdentity
        from codexdevteam_kernel.process_reaper import ProcessReapStatus, reap_process_group

        process_group_id = os.getpid() + 100_000
        with patch("codexdevteam_kernel.process_reaper._linux_pidfd_supported",
                   return_value=True), \
             patch("codexdevteam_kernel.process_reaper.os.getpgrp",
                   return_value=process_group_id, create=True), \
             patch("codexdevteam_kernel.process_reaper.os.pidfd_open", new=Mock(),
                   create=True) as open_pidfd:
            result = reap_process_group(
                ProcessIdentity(process_group_id, "fixture-start", process_group_id))
        self.assertEqual(result.status, ProcessReapStatus.SELF_GROUP_REFUSED)
        open_pidfd.assert_not_called()

    @staticmethod
    def process(stdout="", stderr="", returncode=0):
        from unittest.mock import Mock
        process = Mock()
        process.pid = 123
        process.returncode = returncode
        process.communicate.return_value = (stdout, stderr)
        return process

    def request(self, **overrides):
        values = {"invocation_id": "invoke-1", "task_id": "TASK-90", "purpose": "checker",
                  "identity": WorkerIdentity("reviewer", "reviewer", "high", "codex", "configured-model"),
                  "prompt": "Review the task result", "working_directory": str(Path.cwd()),
                  "timeout_seconds": 12, "writable": False, "allowed_environment": (),
                  "output_limit_chars": 12, "review_sha": "a" * 40,
                  "gate_fingerprint": "b" * 64, "reasoning_effort": "high"}
        values.update(overrides)
        return InvocationRequest(**values)

    @patch("codexdevteam_kernel.runtime.capture_process_identity")
    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    @patch("codexdevteam_kernel.windows_jobs.WindowsJob.create")
    def test_maker_runtime_reports_os_identity_immediately_after_spawn(self, create_job,
                                                                      popen, capture):
        from codexdevteam_kernel.process_identity import ProcessIdentity
        from unittest.mock import Mock

        process = self.process(stdout="maker complete")
        popen.return_value = process
        job = Mock()
        job.name = "Global\\CODEXDEVTEAM-" + "a" * 32
        job.active_process_count.return_value = 0
        job.close.return_value = True
        create_job.return_value = job
        capture.return_value = ProcessIdentity(123, "fixture:started", 123)
        identities = []
        request = self.request(
            purpose="maker", task_id="TASK-91",
            identity=WorkerIdentity("builder", "implementation", "standard",
                                    "codex", "configured-model"),
            writable=True, review_sha=None, gate_fingerprint=None,
            on_process_start=identities.append,
        )
        result = CodexExecAdapter("codex-test").invoke(request)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.quiescence_proof is not None, os.name == "nt")
        if result.quiescence_proof is not None:
            self.assertTrue(result.quiescence_proof.verified)
        self.assertEqual(identities, [capture.return_value])
        self.assertEqual(identities[0].containment_ref,
                         job.name if os.name == "nt" else None)
        capture.assert_called_once_with(process.pid)

    def test_windows_job_contains_child_after_maker_exits(self):
        if os.name != "nt":
            self.skipTest("Windows Job Object integration test")
        from codexdevteam_kernel.runtime import _run_invocation_process

        identities = []
        request = self.request(
            purpose="maker", task_id="TASK-WINDOWS-JOB",
            identity=WorkerIdentity("builder", "implementation", "standard",
                                    "codex", "configured-model"),
            writable=True, review_sha=None, gate_fingerprint=None,
            timeout_seconds=15, on_process_start=identities.append,
        )
        child_code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid, flush=True)"
        result = _run_invocation_process(
            [sys.executable, "-c", child_code], "run maker", Path.cwd(),
            os.environ.copy(), request,
        )
        self.assertEqual(result[0], 0)
        self.assertEqual(result[3], "windows_job_object")
        self.assertTrue(result[4])
        self.assertEqual(len(result), 7)
        # The child is deliberately left alive; cleanup kills it and the proof
        # must reflect the post-cleanup zero count, not the initial observation.
        self.assertIsNotNone(result[6])
        self.assertTrue(result[6].verified)
        self.assertEqual(result[6].active_after_exit, 0)
        self.assertEqual(len(identities), 1)
        self.assertRegex(identities[0].containment_ref or "",
                         r"^Global\\CODEXDEVTEAM-[0-9a-f]{32}$")
        from codexdevteam_kernel.windows_jobs import reap_named_windows_job
        recovered = reap_named_windows_job(identities[0].containment_ref,
                                           pid=identities[0].pid)
        self.assertTrue(recovered.whole_tree_verified)

    def test_windows_successor_reacquires_and_terminates_live_job_tree(self):
        if os.name != "nt":
            self.skipTest("Windows Job Object recovery integration test")
        from codexdevteam_kernel.process_identity import capture_process_identity
        from codexdevteam_kernel.windows_jobs import WindowsJob, reap_named_windows_job

        job = WindowsJob.create()
        process = None
        try:
            child_code = (
                "import subprocess,sys; "
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL); print('maker-finished', flush=True)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", child_code], cwd=Path.cwd(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", shell=False,
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x4),
            )
            job.assign(process)
            identity = capture_process_identity(process.pid)
            identity = __import__("dataclasses").replace(
                identity, containment_ref=job.name)
            job.resume(process)
            stdout, _stderr = process.communicate(timeout=10)
            self.assertEqual(stdout.strip(), "maker-finished")
            self.assertGreater(job.active_process_count(), 0)

            recovered = reap_named_windows_job(
                identity.containment_ref, pid=identity.pid, timeout_seconds=5)
            self.assertTrue(recovered.whole_tree_verified)
            self.assertEqual(recovered.status.value, "terminated")
            self.assertEqual(job.active_process_count(), 0)
        finally:
            if job.active_process_count():
                job.terminate_and_verify(timeout_seconds=5)
            job.close()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    @patch("codexdevteam_kernel.runtime._terminate_process_tree",
           return_value=("posix_process_group", False, None))
    @patch("codexdevteam_kernel.runtime.capture_process_identity")
    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    @patch("codexdevteam_kernel.windows_jobs.WindowsJob.create")
    def test_maker_identity_persistence_failure_holds_liveness_without_verified_kill(
            self, create_job, popen, capture, terminate):
        from codexdevteam_kernel.process_identity import ProcessIdentity
        from unittest.mock import Mock

        process = self.process()
        popen.return_value = process
        job = Mock()
        job.name = "Global\\CODEXDEVTEAM-" + "b" * 32
        create_job.return_value = job
        capture.return_value = ProcessIdentity(123, "fixture:started", 123)
        request = self.request(
            purpose="maker", task_id="TASK-92",
            identity=WorkerIdentity("builder", "implementation", "standard",
                                    "codex", "configured-model"),
            writable=True, review_sha=None, gate_fingerprint=None,
            on_process_start=lambda _identity: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )
        result = CodexExecAdapter("codex-test").invoke(request)
        self.assertEqual(result.status, "termination_unverified")
        self.assertFalse(result.process_tree_cancel_verified)
        terminate.assert_called_once_with(process)

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_codex_adapter_uses_configured_model_read_only_and_bounded_output(self, run):
        process = self.process(stdout='{"type":"turn.completed"}')

        def launch(argv, **kwargs):
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text("answer longer than limit", encoding="utf-8")
            return process

        run.side_effect = launch
        result = CodexExecAdapter("codex-test").invoke(self.request())
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIn("configured-model", argv)
        self.assertIn("--output-last-message", argv)
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertIn("read-only", argv)
        self.assertEqual(argv[-1], "-")
        self.assertTrue(process.communicate.call_args.kwargs["input"].startswith(
            "Review the task result"))
        self.assertIn("CODEXDEVTEAM REVIEW BINDING",
                      process.communicate.call_args.kwargs["input"])
        self.assertFalse(kwargs["shell"])
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.stdout) + len(result.stderr), 12)
        self.assertEqual(result.status, "succeeded")

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_checker_uses_plain_last_message_and_keeps_usage_from_jsonl(self, popen):
        process = self.process(stdout=(
            '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":4,'
            '"input_tokens_details":{"cached_tokens":3}}}\n'
        ))

        def launch(argv, **kwargs):
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text('{"decision":"approved"}', encoding="utf-8")
            return process

        popen.side_effect = launch
        result = CodexExecAdapter("codex-test").invoke(self.request(output_limit_chars=1000))
        self.assertEqual(result.stdout, '{"decision":"approved"}')
        self.assertEqual((result.input_tokens, result.output_tokens, result.cached_input_tokens),
                         (12, 4, 3))
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(Path(popen.call_args.kwargs["env"]["TMPDIR"]).exists())

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_checker_fails_closed_when_codex_produces_no_last_message(self, popen):
        popen.return_value = self.process(stdout='{"type":"turn.completed"}')
        result = CodexExecAdapter("codex-test").invoke(self.request(output_limit_chars=1000))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("required final checker message", result.stderr)

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_codex_adapter_redacts_secret_like_output_and_filters_environment(self, run):
        run.return_value = self.process(stdout="token=" + "sk-" + "A" * 24)
        with patch.dict(os.environ, {"CODEXDEVTEAM_RUNTIME_SECRET": "do-not-inherit"}):
            result = CodexExecAdapter().invoke(self.request(output_limit_chars=1000))
        self.assertNotIn("sk-", result.stdout)
        self.assertNotIn("CODEXDEVTEAM_RUNTIME_SECRET", run.call_args.kwargs["env"])

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_codex_invocation_uses_invocation_specific_temp_directory_and_cleans_it(self, run):
        run.return_value = self.process()
        with patch.dict(os.environ, {"TEMP": "shared-temp", "TMP": "shared-temp",
                                     "TMPDIR": "shared-temp"}):
            CodexExecAdapter("codex-test").invoke(self.request())
        environment = run.call_args.kwargs["env"]
        isolated = environment["TMPDIR"]
        self.assertNotEqual(isolated, "shared-temp")
        self.assertEqual(environment["TEMP"], isolated)
        self.assertEqual(environment["TMP"], isolated)
        self.assertFalse(Path(isolated).exists())

    def test_checker_cannot_request_write_access(self):
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.request(writable=True)

    @patch("codexdevteam_kernel.runtime._terminate_process_tree",
           return_value=("posix_process_group", True, None))
    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_codex_adapter_cancels_runtime_after_head_lease_loss(self, popen, terminate):
        from threading import Event
        process = self.process()
        popen.return_value = process
        cancelled = Event()
        cancelled.set()
        result = CodexExecAdapter("codex-test").invoke(
            self.request(cancel_event=cancelled, output_limit_chars=1000))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.process_tree_cancel_method, "posix_process_group")
        self.assertTrue(result.process_tree_cancel_verified)
        self.assertIn("HEAD lease was lost", result.stderr)
        terminate.assert_called_once_with(process)
        process.communicate.assert_called_once_with(timeout=5)
        self.assertFalse(Path(popen.call_args.kwargs["env"]["TMPDIR"]).exists())

    @patch("codexdevteam_kernel.runtime._terminate_process_tree",
           return_value=("posix_process_group", False, None))
    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_adapter_marks_unverified_cancellation_without_claiming_termination(
            self, popen, _terminate):
        from threading import Event
        process = self.process()
        popen.return_value = process
        cancelled = Event()
        cancelled.set()
        result = CodexExecAdapter("codex-test").invoke(
            self.request(cancel_event=cancelled, output_limit_chars=1000))
        self.assertEqual(result.status, "termination_unverified")
        self.assertEqual(result.process_tree_cancel_method, "posix_process_group")
        self.assertFalse(result.process_tree_cancel_verified)

    @patch("codexdevteam_kernel.runtime._terminate_process_tree",
           return_value=("posix_process_group", True, None))
    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_verified_maker_cancellation_writes_signed_receipt_outside_provider_environment(
            self, popen, _terminate):
        import hmac
        from threading import Event
        process = self.process()
        popen.return_value = process
        identity = WorkerIdentity("builder", "implementation", "standard",
                                  "codex", "builder-model")
        cancelled = Event()
        cancelled.set()
        token = "one-use-secret-token-12345678901234567890"
        with tempfile.TemporaryDirectory() as temp:
            request = self.request(
                purpose="maker", identity=identity, writable=True,
                cancel_event=cancelled, cancellation_receipt_dir=temp,
                cancellation_token=token, output_limit_chars=1000)
            result = CodexExecAdapter("codex-test").invoke(request)
            receipt_files = list(Path(temp).glob("*.json"))
            self.assertEqual(len(receipt_files), 1)
            envelope = json.loads(receipt_files[0].read_text(encoding="utf-8"))
            self.assertNotIn(token, receipt_files[0].read_text(encoding="utf-8"))
            payload = envelope["payload"]
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            expected = hmac.new(token.encode(), canonical, hashlib.sha256).hexdigest()
            self.assertEqual(envelope["signature"], expected)
        self.assertTrue(result.process_tree_cancel_verified)
        environment = popen.call_args.kwargs["env"]
        self.assertNotIn(token, environment.values())
        self.assertNotIn("cancellation_token", repr(request))

    @patch("codexdevteam_kernel.runtime._terminate_process_tree",
           return_value=("posix_process_group", True, None))
    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    @patch("codexdevteam_kernel.runtime.time.monotonic", side_effect=(10.0, 11.0))
    def test_invocation_timeout_terminates_process_tree(self, _clock, popen, terminate):
        from codexdevteam_kernel.runtime import _run_invocation_process
        process = self.process()
        popen.return_value = process
        request = self.request(timeout_seconds=0.5)
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_invocation_process(["codex"], "prompt", Path.cwd(), {}, request)
        terminate.assert_called_once_with(process)
        process.communicate.assert_called_once_with(timeout=5)

    def test_real_invocation_process_is_reaped_after_cancellation(self):
        import time
        from threading import Event, Thread
        from codexdevteam_kernel.runtime import _InvocationCancelled, _run_invocation_process
        cancelled = Event()
        request = self.request(timeout_seconds=8, cancel_event=cancelled)
        stopper = Thread(target=lambda: (time.sleep(0.15), cancelled.set()))
        stopper.start()
        started = time.monotonic()
        try:
            with self.assertRaises(_InvocationCancelled):
                _run_invocation_process(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    "prompt", Path.cwd(), os.environ.copy(), request)
        finally:
            stopper.join()
        self.assertLess(time.monotonic() - started, 5)

    @unittest.skipIf(os.name == "nt", "POSIX process groups are not available")
    def test_cancellation_kills_descendant_that_ignores_termination(self):
        import time
        from threading import Event, Thread
        from codexdevteam_kernel.runtime import _InvocationCancelled, _run_invocation_process
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "child.pid"
            child = ("import os,signal,sys,time; "
                     "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                     "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)")
            parent = ("import subprocess,sys,time; "
                      "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
                      "time.sleep(30)")
            cancelled = Event()
            request = self.request(timeout_seconds=8, cancel_event=cancelled)

            def cancel_when_child_starts():
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and not pid_file.exists():
                    time.sleep(0.01)
                cancelled.set()

            stopper = Thread(target=cancel_when_child_starts)
            stopper.start()
            try:
                with self.assertRaises(_InvocationCancelled):
                    _run_invocation_process(
                        [sys.executable, "-c", parent, child, str(pid_file)],
                        "prompt", Path.cwd(), os.environ.copy(), request)
            finally:
                stopper.join()
            child_pid = int(pid_file.read_text(encoding="ascii"))
            proc_stat = Path(f"/proc/{child_pid}/stat")
            for _ in range(100):
                if not proc_stat.exists() or proc_stat.read_text().split(") ", 1)[1][0] == "Z":
                    break
                time.sleep(0.01)
            else:
                self.fail("terminated invocation left a live descendant process")

    @unittest.skipUnless(os.name == "nt", "Windows task-tree termination is Windows-specific")
    def test_windows_cancellation_kills_descendant_and_records_taskkill_evidence(self):
        import ctypes
        import time
        from ctypes import wintypes
        from threading import Event, Thread
        from codexdevteam_kernel.runtime import _InvocationCancelled, _run_invocation_process
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "child.pid"
            child = ("import os,sys,time; "
                     "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)")
            parent = ("import subprocess,sys,time; "
                      "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
                      "time.sleep(30)")
            cancelled = Event()
            request = self.request(timeout_seconds=8, cancel_event=cancelled)

            def cancel_when_child_starts():
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not pid_file.exists():
                    time.sleep(0.01)
                cancelled.set()

            stopper = Thread(target=cancel_when_child_starts)
            stopper.start()
            try:
                with self.assertRaises(_InvocationCancelled) as cancelled_result:
                    _run_invocation_process(
                        [sys.executable, "-c", parent, child, str(pid_file)],
                        "prompt", Path.cwd(), os.environ.copy(), request)
            finally:
                stopper.join()
            evidence = cancelled_result.exception
            self.assertEqual(evidence.method, "windows_taskkill_tree")
            self.assertTrue(evidence.verified)
            self.assertEqual(evidence.exit_code, 0)
            child_pid = int(pid_file.read_text(encoding="ascii"))
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(wintypes.DWORD)]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            active = True
            for _ in range(200):
                handle = kernel32.OpenProcess(0x1000, False, child_pid)
                if not handle:
                    active = False
                    break
                exit_code = ctypes.c_ulong()
                try:
                    active = (not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                              or exit_code.value == 259)
                finally:
                    kernel32.CloseHandle(handle)
                if not active:
                    break
                time.sleep(0.01)
            self.assertFalse(active, "taskkill left the invocation descendant running")

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_maker_runtime_receives_trusted_identity_and_task_hook_context(self, run):
        run.return_value = self.process(stdout="done")
        with tempfile.NamedTemporaryFile() as state_db:
            identity = WorkerIdentity("builder-real", "implementation", "standard",
                                      "codex", "configured-builder-model")
            request = self.request(purpose="maker", identity=identity, writable=True,
                                   state_db_path=state_db.name)
            CodexExecAdapter("codex-test").invoke(request)
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["CODEXDEVTEAM_WORKER_ID"], "builder-real")
        self.assertEqual(environment["CODEXDEVTEAM_TASK_ID"], "TASK-90")
        self.assertEqual(environment["CODEXDEVTEAM_RUNTIME"], "codex")
        self.assertEqual(environment["CODEXDEVTEAM_MODEL"], "configured-builder-model")

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_maker_runtime_creates_and_injects_worktree_control_outbox(self, run):
        run.return_value = self.process()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_db = root / "state.sqlite"
            state_db.write_bytes(b"sqlite fixture")
            outbox = root / ".codexdevteam" / "control" / "outbox"
            identity = WorkerIdentity("builder-real", "implementation", "standard",
                                      "codex", "configured-builder-model")
            request = self.request(purpose="maker", identity=identity, writable=True,
                                   working_directory=str(root), state_db_path=str(state_db),
                                   control_outbox_path=str(outbox))
            CodexExecAdapter("codex-test").invoke(request)
            environment = run.call_args.kwargs["env"]
            self.assertEqual(Path(environment["CODEXDEVTEAM_CONTROL_OUTBOX"]).resolve(strict=True),
                             outbox.resolve(strict=True))
            self.assertTrue(outbox.is_dir())

    @patch("codexdevteam_kernel.runtime.subprocess.Popen")
    def test_codex_adapter_extracts_usage_from_completed_turn_events(self, run):
        output = "\n".join((
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 120, "output_tokens": 40,
                "input_tokens_details": {"cached_tokens": 30}}}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 80, "output_tokens": 20,
                "cached_input_tokens": 10}}),
        ))
        run.return_value = self.process(stdout=output)
        result = CodexExecAdapter("codex-test").invoke(self.request())
        self.assertEqual(result.role, "reviewer")
        self.assertEqual(result.input_tokens, 200)
        self.assertEqual(result.output_tokens, 60)
        self.assertEqual(result.cached_input_tokens, 40)
        self.assertIsNone(result.reported_cost_usd)


class RegistryTests(unittest.TestCase):
    def config(self):
        return {
            "protocol_version": 1,
            "head_candidate": "control-unit",
            "active": ["control-unit", "builder-1"],
            "defined": {
                "control-unit": {"role": "planner", "capability_floor": "frontier",
                                  "runtime": "codex", "model": "configured-head-model"},
                "builder-1": {"role": "implementation", "capability_floor": "standard",
                              "runtime": "codex", "model": "configured-build-model"},
            },
        }

    def test_registry_keeps_identity_dimensions_and_head_lease_separate(self):
        registry = WorkerRegistry.from_dict(self.config())
        candidate = registry.candidate_head()
        self.assertEqual(candidate.identity.runtime, "codex")
        self.assertEqual(candidate.identity.role, "planner")
        self.assertEqual(candidate.identity.model, "configured-head-model")
        # Configuration nominates a candidate; only StateStore can grant authority.
        self.assertIsNone(getattr(candidate, "lease", None))

    def test_registry_fails_closed_for_unknown_or_unverified_strict_worker(self):
        registry = WorkerRegistry.from_dict(self.config())
        with self.assertRaisesRegex(ValueError, "unknown worker"):
            registry.resolve("missing")
        config = self.config()
        config["defined"]["builder-1"]["control_mode"] = "strict"
        with self.assertRaisesRegex(ValueError, "live-verification receipt"):
            WorkerRegistry.from_dict(config)

    def test_strict_worker_receipt_must_match_its_configured_runtime_and_model(self):
        config = self.config()
        worker = config["defined"]["builder-1"]
        worker["control_mode"] = "strict"
        worker["strict_verification"] = {"status": "passed", "runtime": "codex",
                                         "model": "configured-build-model",
                                         "verified_at": "2026-10-01T00:00:00Z",
                                         "evidence_ref": "live-check-123",
                                         "verified_capabilities": [
                                             "control_protocol", "structured_edit_firewall",
                                             "task_worktree_isolation",
                                             "post_run_territory_gate"]}
        self.assertEqual(WorkerRegistry.from_dict(config).resolve("builder-1").control_mode, "strict")
        worker["strict_verification"]["runtime"] = "claude"
        with self.assertRaisesRegex(ValueError, "must match"):
            WorkerRegistry.from_dict(config)

    def test_strict_worker_requires_live_enforcement_capability_evidence(self):
        config = self.config()
        config["defined"]["builder-1"].update({
            "control_mode": "strict",
            "strict_verification": {"status": "passed", "runtime": "codex",
                                    "model": "configured-build-model",
                                    "verified_at": "2026-10-01T00:00:00Z",
                                    "evidence_ref": "live-check-456",
                                    "verified_capabilities": ["control_protocol"]},
        })
        with self.assertRaisesRegex(ValueError, "structured_edit_firewall"):
            WorkerRegistry.from_dict(config)

    def test_strict_receipt_requires_well_formed_timestamp_reference_and_capabilities(self):
        base = {
            "status": "passed", "runtime": "codex", "model": "configured-build-model",
            "verified_at": "2026-10-01T00:00:00Z", "evidence_ref": "live-check-456",
            "verified_capabilities": ["control_protocol", "structured_edit_firewall",
                                      "task_worktree_isolation", "post_run_territory_gate"],
        }
        invalid_receipts = (
            ({**base, "verified_at": "yesterday"}, "ISO 8601"),
            ({**base, "verified_at": "2026-10-01T00:00:00"}, "include a timezone"),
            ({**base, "verified_at": "2999-10-01T00:00:00Z"}, "future"),
            ({**base, "evidence_ref": 123}, "requires verified_at and evidence_ref"),
            ({**base, "verified_capabilities": base["verified_capabilities"] +
              ["control_protocol"]}, "lacks required live capabilities"),
        )
        for receipt, message in invalid_receipts:
            with self.subTest(receipt=receipt):
                config = self.config()
                config["defined"]["builder-1"].update({
                    "control_mode": "strict", "strict_verification": receipt,
                })
                with self.assertRaisesRegex(ValueError, message):
                    WorkerRegistry.from_dict(config)

    def test_registry_rejects_active_undefined_workers(self):
        config = self.config()
        config["active"].append("not-defined")
        with self.assertRaisesRegex(ValueError, "undefined"):
            WorkerRegistry.from_dict(config)

    def test_role_policy_configures_model_order_and_reasoning_effort(self):
        config = self.config()
        config["role_policies"] = {"implementation": {
            "model_priority": ["configured-build-model", "fallback-model"],
            "reasoning_effort": "high"}}
        registry = WorkerRegistry.from_dict(config)
        self.assertEqual(registry.policy_for_role("implementation").reasoning_effort, "high")
        config["role_policies"]["implementation"]["reasoning_effort"] = "wild"
        with self.assertRaisesRegex(ValueError, "reasoning effort"):
            WorkerRegistry.from_dict(config)

    def test_runtime_request_uses_role_policy_effort_and_worker_model(self):
        config = self.config()
        config["role_policies"] = {"implementation": {
            "model_priority": ["configured-build-model"], "reasoning_effort": "xhigh"}}
        registry = WorkerRegistry.from_dict(config)
        request = request_for_worker(registry, "builder-1", invocation_id="invoke-policy",
                                     task_id="TASK-1", purpose="maker", prompt="implement",
                                     working_directory=str(Path.cwd()), writable=True)
        self.assertEqual(request.identity.model, "configured-build-model")
        self.assertEqual(request.reasoning_effort, "xhigh")


class DispatchTests(unittest.TestCase):
    def test_fast_tier_hints_can_only_restrict_independent_capacity(self):
        from codexdevteam_kernel import apply_fast_tier_restrictions
        capacity = {
            "unavailable": CapacityObservation(False, 0, observed_at=100),
            "available": CapacityObservation(True, 2, observed_at=100),
        }
        unchanged = apply_fast_tier_restrictions(capacity, {"unavailable": "ok"})
        self.assertFalse(unchanged["unavailable"].available)
        self.assertEqual(unchanged["unavailable"].free_slots, 0)
        restricted = apply_fast_tier_restrictions(capacity, {"available": "quota"})
        self.assertEqual(restricted["available"].quota_remaining, 0)
        self.assertTrue(restricted["available"].available)
        with self.assertRaisesRegex(DispatchError, "unknown worker"):
            apply_fast_tier_restrictions(capacity, {"missing": "auth"})

    def test_only_active_workers_are_eligible_and_assignment_snapshots_identity(self):
        data = {"protocol_version": 1, "active": ["builder", "preferred"], "head_candidate": None,
                "role_policies": {"implementation": {
                    "model_priority": ["preferred-model", "configured-model"],
                    "reasoning_effort": "high"}},
                "defined": {"builder": {"role": "implementation", "capability_floor": "standard",
                                          "runtime": "codex", "model": "configured-model"},
                            "preferred": {"role": "implementation", "capability_floor": "standard",
                                          "runtime": "other", "model": "preferred-model"},
                            "inactive": {"role": "implementation", "capability_floor": "standard",
                                          "runtime": "other", "model": "inactive-model"}}}
        registry = WorkerRegistry.from_dict(data)
        self.assertEqual([item.identity.unit_id for item in eligible_workers(registry)],
                         ["builder", "preferred"])
        self.assertEqual([item.identity.unit_id for item in eligible_workers(
            registry, role="implementation")], ["preferred", "builder"])
        task = TaskRecord("TASK-19", "Assign me", TaskState.PENDING, None, "medium", ("src/**",))
        assigned = assign_task(task, registry.resolve("builder"))
        self.assertEqual(assigned.assigned_worker, "builder")
        self.assertEqual(assigned.maker_identity,
                         {"unit_id": "builder", "runtime": "codex", "model": "configured-model"})

    def test_capacity_observations_filter_stale_unavailable_full_and_quota_exhausted_workers(self):
        units = ("ready", "busy", "exhausted", "stale")
        data = {"protocol_version": 1, "active": list(units), "head_candidate": None,
                "defined": {unit: {"role": "implementation", "capability_floor": "standard",
                                   "runtime": "codex", "model": f"model-{unit}"}
                            for unit in units}}
        registry = WorkerRegistry.from_dict(data)
        capacity = {
            "ready": CapacityObservation(True, 1, observed_at=95, quota_remaining=1),
            "busy": CapacityObservation(True, 0, observed_at=95),
            "exhausted": CapacityObservation(True, 2, observed_at=95, quota_remaining=0),
            "stale": CapacityObservation(True, 2, observed_at=1, stale_after_seconds=10),
        }
        self.assertEqual([worker.identity.unit_id for worker in eligible_workers(
            registry, capacity=capacity, now=100)], ["ready"])
        self.assertEqual([worker.identity.unit_id for worker in eligible_workers(
            registry, now=100)], list(units))

    def test_capacity_observation_cooldown_and_invalid_values_fail_closed(self):
        snapshot = CapacityObservation(True, 1, observed_at=95, cooldown_until=101)
        self.assertFalse(snapshot.eligible(now=100))
        self.assertTrue(snapshot.eligible(now=101))
        with self.assertRaisesRegex(ValueError, "free_slots"):
            CapacityObservation(True, -1, observed_at=100)
        with self.assertRaisesRegex(ValueError, "finite numbers"):
            CapacityObservation(True, 1, observed_at=float("nan"))

    def test_worker_readiness_reports_lifecycle_dimensions_independently(self):
        from codexdevteam_kernel.dispatch import worker_readiness
        registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["ready", "full"], "head_candidate": None,
            "defined": {unit: {"role": "implementation", "capability_floor": "standard",
                               "runtime": "codex", "model": f"model-{unit}"}
                        for unit in ("ready", "full", "inactive")},
        })
        capacity = {"ready": CapacityObservation(True, 1, observed_at=95),
                    "full": CapacityObservation(True, 0, observed_at=95)}
        active_task = TaskRecord("TASK-19", "Working", TaskState.IN_PROGRESS,
                                 "ready", "medium", ("src/**",))
        records = {item.worker_id: item for item in worker_readiness(
            registry, (active_task,), capacity=capacity, now=100)}
        self.assertTrue(records["ready"].defined and records["ready"].active)
        self.assertEqual(records["ready"].availability, "available")
        self.assertTrue(records["ready"].eligible)
        self.assertEqual(records["ready"].assigned_tasks, ("TASK-19",))
        self.assertFalse(records["full"].eligible)
        self.assertEqual(records["full"].availability, "unavailable")
        self.assertFalse(records["inactive"].active)
        self.assertEqual(records["inactive"].availability, "unknown")

    def test_capability_floor_uses_explicit_order_and_fails_on_unknown_policy(self):
        data = {"protocol_version": 1, "active": ["basic", "advanced"],
                "capability_order": ["standard", "advanced", "expert"],
                "head_candidate": None, "defined": {
                    "basic": {"role": "implementation", "capability_floor": "standard",
                              "runtime": "codex", "model": "basic-model"},
                    "advanced": {"role": "implementation", "capability_floor": "expert",
                                 "runtime": "codex", "model": "expert-model"}}}
        registry = WorkerRegistry.from_dict(data)
        selected = eligible_workers(registry, required_capability_floor="advanced")
        self.assertEqual([worker.identity.unit_id for worker in selected], ["advanced"])
        with self.assertRaisesRegex(DispatchError, "absent"):
            eligible_workers(registry, required_capability_floor="unknown",
                             capability_order=registry.capability_order)

    def test_task_class_policy_maps_named_classes_and_rejects_unconfigured_classes(self):
        from codexdevteam_kernel import TaskClassPolicy
        policy = TaskClassPolicy({"mechanical": "standard", "critical": "expert"})
        self.assertEqual(TaskClassPolicy.from_dict(policy.to_dict()), policy)
        self.assertEqual(policy.floor_for("mechanical", fallback="advanced"), "standard")
        self.assertEqual(policy.floor_for(None, fallback="advanced"), "advanced")
        with self.assertRaisesRegex(DispatchError, "no configured routing rule"):
            policy.floor_for("novel")


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "supervisor.sqlite")
        self.lease = self.store.acquire_head("CODEXDEVTEAM", "head", now=100)
        receipt = {"status": "passed", "runtime": "codex", "model": "builder-model",
                   "verified_at": "2026-10-01T00:00:00Z", "evidence_ref": "live-check",
                   "verified_capabilities": ["control_protocol", "structured_edit_firewall",
                                             "task_worktree_isolation",
                                             "post_run_territory_gate"]}
        self.registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["builder"], "head_candidate": None,
            "capability_order": ["standard", "advanced"],
            "defined": {"builder": {"role": "implementation", "capability_floor": "advanced",
                                     "runtime": "codex", "model": "builder-model",
                                     "control_mode": "strict", "strict_verification": receipt}}})
        self.supervisor = Supervisor(self.store, self.registry, SupervisorPolicy(
            required_capability_floor="standard"))

    def tearDown(self):
        self.temp.cleanup()

    def test_requeue_requires_latest_changes_requested_review(self):
        task = TaskRecord("TASK-REQUEUE", "Repair reviewed task", TaskState.IN_PROGRESS,
                          "builder", "medium", ("src/**",), maker_identity={
                              "unit_id": "builder", "runtime": "codex", "model": "builder-model"})
        self.store.seed_task(self.lease, task, event_id="seed-requeue", now=101)
        self.store.set_supervisor_mode(self.lease, "running", event_id="run-requeue", now=102)
        with self.assertRaisesRegex(ValueError, "latest task review"):
            self.supervisor.requeue_changes_requested_task(
                self.lease, task.task_id, event_id="requeue-without-review", now=103)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.IN_PROGRESS)

    def test_staged_historical_dependency_allows_dispatch_without_faking_done_task(self):
        task = TaskRecord("TASK-AFTER-HANDOVER", "Continue translated work",
                          TaskState.PENDING, None, "medium", ("src/after.py",),
                          depends_on=("TASK-SOURCE-DONE",))
        self.store.import_handover_tasks(
            self.lease, (task,), ("TASK-SOURCE-DONE",),
            source_plan_sha256="a" * 64, mapping_sha256="b" * 64,
            event_id="stage-historical-dependency", now=101,
        )
        self.assertIsNone(self.store.get_task("TASK-SOURCE-DONE"))
        self.store.set_supervisor_mode(self.lease, "running",
                                       event_id="run-after-handover", now=102)
        cycle = self.supervisor.run_dispatch_cycle(
            self.lease, cycle_id="cycle-after-handover",
            capacity={"builder": CapacityObservation(True, 1, observed_at=102)}, now=103,
        )
        self.assertEqual(cycle.assignments, (task.task_id,))

    def test_handover_staging_refuses_running_target_supervisor(self):
        task = TaskRecord("TASK-STAGED", "Stay inactive", TaskState.PENDING,
                          None, "medium", ("src/staged.py",))
        self.store.set_supervisor_mode(self.lease, "running", event_id="run-before-stage", now=101)
        with self.assertRaisesRegex(LeaseError, "supervisor to be parked"):
            self.store.import_handover_tasks(
                self.lease, (task,), (), source_plan_sha256="a" * 64,
                mapping_sha256="b" * 64, event_id="stage-while-running", now=102,
            )
        self.assertEqual(self.store.list_tasks(), ())

    def prepare_claimed_maker(self, task_id):
        project = Path(self.temp.name) / f"project-{task_id}"
        project.mkdir()
        for args in (("init",), ("config", "user.email", "test@example.invalid"),
                     ("config", "user.name", "Kernel Test")):
            subprocess.run(["git", "-C", str(project), *args], check=True,
                           capture_output=True)
        (project / "README.md").write_text("fixture\n", encoding="utf-8")
        (project / ".gitignore").write_text(".codexdevteam/control/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(project), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-m", "base"], check=True,
                       capture_output=True)
        base = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        task = TaskRecord(task_id, "Run a maker", TaskState.CLAIMED, "builder",
                          "medium", ("src/**",), maker_identity={
                              "unit_id": "builder", "runtime": "codex",
                              "model": "builder-model"})
        self.store.seed_task(self.lease, task, event_id=f"seed:{task_id}", now=101)
        self.store.set_supervisor_mode(self.lease, "running", event_id=f"run:{task_id}", now=102)
        return project, base, task

    def test_task_classes_apply_capability_floors_during_atomic_dispatch(self):
        from codexdevteam_kernel import TaskClassPolicy
        receipt = lambda model: {
            "status": "passed", "runtime": "codex", "model": model,
            "verified_at": "2026-10-01T00:00:00Z", "evidence_ref": "live-check:" + model,
            "verified_capabilities": ["control_protocol", "structured_edit_firewall",
                                      "task_worktree_isolation", "post_run_territory_gate"],
        }
        registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["standard", "expert"], "head_candidate": None,
            "capability_order": ["standard", "advanced", "expert"],
            "defined": {
                "standard": {"role": "implementation", "capability_floor": "standard",
                             "runtime": "codex", "model": "standard-model", "control_mode": "strict",
                             "strict_verification": receipt("standard-model")},
                "expert": {"role": "implementation", "capability_floor": "expert",
                           "runtime": "codex", "model": "expert-model", "control_mode": "strict",
                           "strict_verification": receipt("expert-model")},
            },
        })
        self.store.seed_task(self.lease, TaskRecord(
            "TASK-CLASS-1", "Mechanical task", TaskState.PENDING, None, "high",
            ("src/mechanical/**",), task_class="mechanical"),
            event_id="seed-mechanical-class", now=101)
        self.store.seed_task(self.lease, TaskRecord(
            "TASK-CLASS-2", "Critical task", TaskState.PENDING, None, "high",
            ("src/critical/**",), task_class="critical"),
            event_id="seed-critical-class", now=101.1)
        self.store.set_supervisor_mode(self.lease, "running", event_id="run-task-classes", now=102)
        policy = SupervisorPolicy(
            require_strict=True, require_capacity_observation=True,
            task_class_policy=TaskClassPolicy({"mechanical": "standard", "critical": "expert"}),
        )
        supervisor = Supervisor(self.store, registry, policy)
        capacity = {unit: CapacityObservation(True, 1, observed_at=102)
                    for unit in ("standard", "expert")}
        result = supervisor.run_dispatch_cycle(
            self.lease, cycle_id="task-class-cycle", capacity=capacity, now=103)
        self.assertEqual(result.assignments, ("TASK-CLASS-1", "TASK-CLASS-2"))
        self.assertEqual(self.store.get_task("TASK-CLASS-1").maker_identity["unit_id"], "standard")
        self.assertEqual(self.store.get_task("TASK-CLASS-2").maker_identity["unit_id"], "expert")

    def test_fast_tier_quota_hint_restricts_dispatch_without_creating_capacity(self):
        from codexdevteam_kernel import FastTierRunner

        task = TaskRecord("TASK-FAST-HINT", "Capacity gated", TaskState.PENDING,
                          None, "high", ("src/fast-hint/**",))
        self.store.seed_task(self.lease, task, event_id="seed-fast-hint", now=101)
        self.store.set_supervisor_mode(self.lease, "running", event_id="run-fast-hint", now=102)
        fast_registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["triage"], "head_candidate": None,
            "defined": {"triage": {"role": "fast", "capability_floor": "standard",
                                    "runtime": "fixture", "model": "configured-fast"}},
        })

        class Adapter:
            def invoke(self, request):
                output = json.dumps({"kind": "quota", "evidence_line": "429 quota exhausted"})
                return InvocationResult(
                    request.invocation_id, None, "triage", request.identity.unit_id,
                    request.identity.runtime, request.identity.model, "succeeded", 0, 0.1,
                    output, "", False, hashlib.sha256(output.encode()).hexdigest(),
                    102.5, 102.6, role=request.identity.role,
                )

        result = self.supervisor.run_dispatch_cycle(
            self.lease, cycle_id="fast-hint-cycle",
            capacity={"builder": CapacityObservation(True, 1, observed_at=102)}, now=103,
            fast_tier_runner=FastTierRunner(self.store, fast_registry),
            fast_tier_worker_id="triage", fast_tier_logs={"builder": "429 quota exhausted"},
            fast_tier_adapters={"fixture": Adapter()},
            fast_tier_working_directory=self.temp.name,
        )
        self.assertEqual(result.assignments, ())
        self.assertIn("TASK-FAST-HINT", result.deferred)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.PENDING)
        self.assertTrue(any(event["payload"].get("type") == "tier.classified"
                            for event in self.store.events()))

    def test_classified_task_fails_closed_without_class_policy(self):
        self.store.seed_task(self.lease, TaskRecord(
            "TASK-CLASS-3", "Class needs policy", TaskState.PENDING, None, "high",
            ("src/classified/**",), task_class="novel"),
            event_id="seed-unmapped-task-class", now=101)
        self.store.set_supervisor_mode(self.lease, "running", event_id="run-unmapped-class", now=102)
        capacity = {"builder": CapacityObservation(True, 1, observed_at=102)}
        with self.assertRaisesRegex(DispatchError, "no task-class policy"):
            self.supervisor.run_dispatch_cycle(
                self.lease, cycle_id="unmapped-class-cycle", capacity=capacity, now=103)
        self.assertEqual(self.store.get_task("TASK-CLASS-3").state, TaskState.PENDING)

    class FakeInvocationAdapter:
        def __init__(self, *, raises=False, snapshot_sidecars=False):
            self.requests = []
            self.raises = raises
            self.snapshot_sidecars = snapshot_sidecars

        def invoke(self, request):
            self.requests.append(request)
            if request.state_db_path:
                if not Path(request.state_db_path).is_file():
                    raise RuntimeError("task database snapshot is missing")
                if self.snapshot_sidecars:
                    for suffix in ("-journal", "-wal", "-shm"):
                        Path(request.state_db_path + suffix).write_text("fixture", encoding="ascii")
            if self.raises:
                raise RuntimeError("fixture adapter error with private details")
            return InvocationResult(
                request.invocation_id, request.task_id, request.purpose,
                request.identity.unit_id, request.identity.runtime, request.identity.model,
                "succeeded", 0, 1.0, "completed", "", False,
                hashlib.sha256(b"completed").hexdigest(), 102.0, 103.0,
            )

    def test_bounded_dispatch_and_launch_cycle_claims_only_after_input_validation(self):
        project = Path(self.temp.name) / "bounded-project"
        project.mkdir()
        for args in (("init",), ("config", "user.email", "test@example.invalid"),
                     ("config", "user.name", "Kernel Test")):
            subprocess.run(["git", "-C", str(project), *args], check=True,
                           capture_output=True)
        (project / "README.md").write_text("fixture\n", encoding="utf-8")
        (project / "PLAN.md").write_text(
            "# Project plan\n\n### TASK-COORDINATED\n"
            "**Title:** Bounded launch\n**Status:** pending\n**Assigned_To:** —\n"
            "**Priority:** high\n**Owned_Paths:** src/coordinated.py\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(project), "add", "README.md", "PLAN.md"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-m", "base"], check=True,
                       capture_output=True)
        base = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        task = TaskRecord("TASK-COORDINATED", "Bounded launch", TaskState.PENDING,
                          None, "high", ("src/coordinated.py",))
        self.store.seed_task(self.lease, task, event_id="seed-coordinated", now=101)
        self.store.set_supervisor_mode(self.lease, "running", event_id="run-coordinated", now=102)
        capacity = {"builder": CapacityObservation(True, 1, observed_at=103)}
        worktrees = GitWorktreeManager(project, Path(self.temp.name) / "managed-coordinated")
        adapter = self.FakeInvocationAdapter()

        with self.assertRaisesRegex(ValueError, "cover all pending tasks"):
            self.supervisor.run_dispatch_and_launch_cycle(
                self.lease, cycle_id="coordinated-cycle-missing-input",
                capacity=capacity, task_prompts={}, invocation_ids={}, base_ref=base,
                worktrees=worktrees, adapters={"codex": adapter}, now=103,
            )
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.PENDING)
        result = self.supervisor.run_dispatch_and_launch_cycle(
            self.lease, cycle_id="coordinated-cycle", capacity=capacity,
            task_prompts={task.task_id: "implement the task"},
            invocation_ids={task.task_id: "maker:coordinated"}, base_ref=base,
            worktrees=worktrees, adapters={"codex": adapter}, max_tasks=1,
            project_root=project, now=104,
        )
        self.assertEqual(result.dispatch.assignments, (task.task_id,))
        self.assertEqual(len(result.makers), 1)
        self.assertEqual(result.makers[0].invocation.status, "succeeded")
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.IN_PROGRESS)
        self.assertIn("**Status:** in_progress", (project / "PLAN.md").read_text(encoding="utf-8"))
        self.assertTrue(Path(result.makers[0].worktree_path).is_dir())

    def test_continuous_supervisor_stops_at_cycle_limit_and_stop_event(self):
        from threading import Event

        project = Path(self.temp.name) / "continuous-project"
        project.mkdir()
        subprocess.run(["git", "-C", str(project), "init"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email",
                        "test@example.invalid"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Kernel Test"],
                       check=True, capture_output=True)
        (project / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-m", "base"], check=True,
                       capture_output=True)
        base = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        self.store.set_supervisor_mode(self.lease, "running",
                                       event_id="run-continuous-supervisor", now=102)
        worktrees = GitWorktreeManager(project, Path(self.temp.name) / "managed-continuous")
        capacity = {"builder": CapacityObservation(True, 1, observed_at=103)}
        inputs = lambda tick: {
            "capacity": capacity, "task_prompts": {}, "invocation_ids": {},
            "base_ref": base, "worktrees": worktrees, "adapters": {}, "now": 102 + tick,
        }
        cycles = []
        limited = self.supervisor.run_continuous(
            self.lease, cycle_inputs=inputs, cycle_id_prefix=lambda tick: "continuous",
            stop_event=Event(), interval_seconds=0.001, max_cycles=2,
            on_cycle=cycles.append,
        )
        self.assertEqual(limited.cycles_completed, 2)
        self.assertEqual(limited.stop_reason, "max_cycles")
        self.assertEqual(len(cycles), 2)

        stop = Event()
        stopped = self.supervisor.run_continuous(
            self.lease, cycle_inputs=inputs, cycle_id_prefix=lambda tick: "stoppable",
            stop_event=stop, interval_seconds=0.001, max_cycles=3,
            on_cycle=lambda result: stop.set(),
        )
        self.assertEqual(stopped.cycles_completed, 1)
        self.assertEqual(stopped.stop_reason, "stop_requested")

    def test_continuous_runner_renews_head_lease_during_idle_interval(self):
        from threading import Event
        from unittest.mock import patch
        from codexdevteam_kernel.supervisor import (SupervisorCycleResult,
                                                    SupervisorLaunchCycleResult)

        now = time.time()
        lease = self.store.acquire_head(
            "CODEXDEVTEAM", "continuous-keepalive", now=now,
            ttl_seconds=0.15, takeover_confirmed=True,
        )
        self.store.set_supervisor_mode(
            lease, "running", event_id="continuous-keepalive-running", now=now,
        )
        inputs = {
            "capacity": {}, "task_prompts": {}, "invocation_ids": {},
            "base_ref": "unused", "worktrees": object(), "adapters": {},
        }

        def empty_cycle(active_lease, *, cycle_id, **_kwargs):
            return SupervisorLaunchCycleResult(
                SupervisorCycleResult(cycle_id, "running", (), {}), (),
            )

        with patch.object(self.supervisor, "run_dispatch_and_launch_cycle",
                          side_effect=empty_cycle):
            result = self.supervisor.run_continuous(
                lease, cycle_inputs=lambda _tick: inputs,
                cycle_id_prefix=lambda _tick: "keepalive",
                stop_event=Event(), interval_seconds=0.3, lease_ttl_seconds=0.15,
                max_cycles=2,
            )
        self.assertEqual(result.cycles_completed, 2)
        renewed = self.store.renew_head(lease, ttl_seconds=1, now=time.time())
        self.assertGreater(renewed.expires_at, time.time())

    def test_escalation_notifier_uses_durable_retryable_outbox(self):
        self.store.record_escalation(
            self.lease, escalation_key="TASK-NOTIFY:health", task_id="TASK-NOTIFY",
            severity="high", message="task stopped making progress",
            event_id="escalate-notify", now=102,
        )

        class FlakyNotifier:
            def __init__(self):
                self.calls = []

            def notify(self, notification):
                self.calls.append(notification)
                if len(self.calls) == 1:
                    raise RuntimeError("secret provider response")
                return True

        notifier = FlakyNotifier()
        first = self.supervisor.deliver_escalation_notifications(
            self.lease, notifier, now=103, retry_after_seconds=5)
        self.assertEqual(len(first.retryable), 1)
        self.assertEqual(self.store.pending_escalation_notifications()[0]["attempt_count"], 1)
        early = self.supervisor.deliver_escalation_notifications(
            self.lease, notifier, now=107)
        self.assertEqual(early.claimed, ())
        second = self.supervisor.deliver_escalation_notifications(
            self.lease, notifier, now=108)
        self.assertEqual(second.delivered, first.retryable)
        self.assertEqual(notifier.calls[0]["notification_id"],
                         notifier.calls[1]["notification_id"])
        self.assertFalse(any("secret provider response" in str(event)
                             for event in self.store.events()))
        self.assertEqual(self.store.pending_escalation_notifications(), ())

    def test_dispatch_cycle_consumes_task_health_and_opens_circuit_breaker(self):
        task = TaskRecord("TASK-HEALTH-CYCLE", "Stagnant task", TaskState.IN_PROGRESS,
                          "builder", "medium", ("src/health-cycle.py",))
        self.store.seed_task(self.lease, task, event_id="seed-health-cycle", now=101)
        self.store.set_supervisor_mode(self.lease, "running", event_id="run-health-cycle", now=102)
        capacity = {"builder": CapacityObservation(True, 1, observed_at=102)}
        policy = {"no_progress_ticks": 1, "denial_ticks": 2, "max_stagnation_resets": 1}
        first = self.supervisor.run_dispatch_cycle(
            self.lease, cycle_id="health-cycle-1", capacity=capacity,
            task_health_samples={task.task_id: StagnationSample(False, 0)},
            stagnation_policy=policy, now=103,
        )
        self.assertEqual(first.health_actions[task.task_id]["action"], "redispatch")
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.IN_PROGRESS)
        second = self.supervisor.run_dispatch_cycle(
            self.lease, cycle_id="health-cycle-2", capacity=capacity,
            task_health_samples={task.task_id: StagnationSample(False, 0)},
            stagnation_policy=policy, now=104,
        )
        self.assertEqual(second.health_actions[task.task_id]["action"], "escalate")
        notice_id = second.health_actions[task.task_id]["notification_id"]
        self.assertIsInstance(notice_id, str)
        self.assertEqual(self.store.pending_escalation_notifications()[0]["notification_id"],
                         notice_id)

    def test_continuous_runner_delivers_cycle_escalations_before_maker_launches(self):
        from threading import Event

        task = TaskRecord("TASK-CONTINUOUS-NOTIFY", "Escalated continuous task",
                          TaskState.IN_PROGRESS, "builder", "medium",
                          ("src/continuous-notify.py",))
        self.store.seed_task(self.lease, task,
                             event_id="seed-continuous-notify", now=101)
        self.store.set_supervisor_mode(
            self.lease, "running", event_id="run-continuous-notify", now=102)

        class RecordingNotifier:
            def __init__(self):
                self.notifications = []

            def notify(self, notification):
                self.notifications.append(dict(notification))
                return len(self.notifications) > 1

        notifier = RecordingNotifier()
        project = Path(self.temp.name) / "continuous-notify-project"
        project.mkdir()
        subprocess.run(["git", "-C", str(project), "init"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email",
                        "test@example.invalid"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Kernel Test"],
                       check=True, capture_output=True)
        (project / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-m", "base"], check=True,
                       capture_output=True)
        base = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        worktrees = GitWorktreeManager(project, Path(self.temp.name) / "managed-notify")
        inputs = lambda tick: {
            "capacity": {"builder": CapacityObservation(True, 1, observed_at=103)},
            "task_prompts": {}, "invocation_ids": {}, "base_ref": base,
            "worktrees": worktrees, "adapters": {}, "now": 103 + tick * 3,
            "task_health_samples": {task.task_id: StagnationSample(False, 0)},
            "stagnation_policy": {"no_progress_ticks": 1,
                                  "max_stagnation_resets": 0},
        }
        result = self.supervisor.run_continuous(
            self.lease, cycle_inputs=inputs,
            cycle_id_prefix=lambda tick: "continuous-notify",
            stop_event=Event(), interval_seconds=0.001, max_cycles=2,
            notifier=notifier, notification_retry_after_seconds=2,
        )
        self.assertEqual(result.cycles_completed, 2)
        self.assertEqual(len(notifier.notifications), 2)
        self.assertEqual(notifier.notifications[0]["notification_id"],
                         notifier.notifications[1]["notification_id"])
        self.assertEqual(result.last_cycle.notification_result.delivered,
                         (notifier.notifications[0]["notification_id"],))
        self.assertEqual(len(self.store.pending_escalation_notifications()), 0)

    def test_stagnation_does_not_propose_second_maker_while_invocation_is_running(self):
        _, _, task = self.prepare_claimed_maker("TASK-HEALTH-LIVE")
        self.assertTrue(self.store.start_task_invocation(
            self.lease, task.task_id, "maker:health-live", now=103))
        action = self.store.evaluate_stagnation(
            self.lease, task.task_id, StagnationSample(False, 0),
            event_id="health-live-1", policy={"no_progress_ticks": 1}, now=104)
        self.assertEqual(action["action"], "hold_running")
        self.assertEqual(action["reset_count"], 0)
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "running")

    def test_running_maker_process_identity_is_lease_fenced_and_idempotent(self):
        from codexdevteam_kernel.process_identity import ProcessIdentity

        _, _, task = self.prepare_claimed_maker("TASK-MAKER-PROCESS")
        invocation_id = "maker:durable-process"
        self.assertTrue(self.store.start_task_invocation(
            self.lease, task.task_id, invocation_id, now=103))
        identity = ProcessIdentity(43210, "fixture-boot:12345", 43210)
        self.assertTrue(self.store.record_task_invocation_process(
            self.lease, task.task_id, invocation_id, identity, now=104))
        self.assertFalse(self.store.record_task_invocation_process(
            self.lease, task.task_id, invocation_id, identity, now=105))
        liveness = self.store.task_invocation_liveness(task_id=task.task_id)[0]
        self.assertEqual((liveness["process_pid"], liveness["process_start_token"],
                          liveness["process_group_id"]),
                         (43210, "fixture-boot:12345", 43210))
        with self.assertRaisesRegex(LeaseError, "cannot be replaced"):
            self.store.record_task_invocation_process(
                self.lease, task.task_id, invocation_id,
                ProcessIdentity(43211, "fixture-boot:12346", 43211), now=106)

    def test_dispatch_cycle_escalates_stale_invocation_without_redispatch(self):
        _, _, task = self.prepare_claimed_maker("TASK-STALE-INVOCATION")
        self.assertTrue(self.store.start_task_invocation(
            self.lease, task.task_id, "maker:stale", now=103))
        self.lease = self.store.renew_head(self.lease, ttl_seconds=1000, now=104)
        supervisor = Supervisor(self.store, self.registry, SupervisorPolicy(
            required_capability_floor="standard", invocation_stale_after_seconds=10))
        capacity = {"builder": CapacityObservation(True, 1, observed_at=110)}
        fresh = supervisor.run_dispatch_cycle(
            self.lease, cycle_id="stale-invocation-fresh", capacity=capacity, now=110)
        self.assertNotIn("invocation:maker:stale", fresh.health_actions)
        capacity = {"builder": CapacityObservation(True, 1, observed_at=200)}
        stale = supervisor.run_dispatch_cycle(
            self.lease, cycle_id="stale-invocation-old", capacity=capacity, now=200)
        action = stale.health_actions["invocation:maker:stale"]
        self.assertEqual(action["action"], "escalate_stale_invocation")
        self.assertEqual(action["task_id"], task.task_id)
        self.assertEqual(action["process_observation"], "identity_not_recorded")
        observation_event = next(
            event for event in self.store.events()
            if event["event_id"] == action["process_observation_event_id"]
        )
        self.assertEqual(observation_event["payload"], {
            "type": "task.invocation_process_observed",
            "task_id": task.task_id,
            "invocation_id": "maker:stale",
            "process_pid": None,
            "process_group_id": None,
            "identity_fingerprint_sha256": None,
            "observation": "identity_not_recorded",
            "diagnostic_only": True,
        })
        self.assertIsInstance(action["notification_id"], str)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.IN_PROGRESS)
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "running")
        self.assertEqual(len(self.store.pending_escalation_notifications()), 1)

    def test_stale_process_observation_is_lease_fenced_and_auditable(self):
        from unittest.mock import patch
        from codexdevteam_kernel.process_identity import ProcessIdentity, ProcessObservation

        _, _, task = self.prepare_claimed_maker("TASK-STALE-PROCESS-EVIDENCE")
        invocation_id = "maker:stale-process-evidence"
        self.assertTrue(self.store.start_task_invocation(
            self.lease, task.task_id, invocation_id, now=103))
        identity = ProcessIdentity(43210, "fixture-boot:12345", 43210)
        self.assertTrue(self.store.record_task_invocation_process(
            self.lease, task.task_id, invocation_id, identity, now=104))
        self.lease = self.store.renew_head(self.lease, ttl_seconds=1000, now=105)
        supervisor = Supervisor(self.store, self.registry, SupervisorPolicy(
            required_capability_floor="standard", invocation_stale_after_seconds=10))
        capacity = {"builder": CapacityObservation(True, 1, observed_at=200)}
        with patch("codexdevteam_kernel.supervisor.observe_process_identity",
                   return_value=ProcessObservation.PID_REUSED):
            stale = supervisor.run_dispatch_cycle(
                self.lease, cycle_id="stale-process-evidence", capacity=capacity, now=200)
        action = stale.health_actions[f"invocation:{invocation_id}"]
        self.assertEqual(action["process_observation"], "pid_reused")
        event = next(item for item in self.store.events()
                     if item["event_id"] == action["process_observation_event_id"])
        self.assertTrue(event["payload"]["diagnostic_only"])
        self.assertEqual(event["payload"]["identity_fingerprint_sha256"],
                         hashlib.sha256(identity.start_token.encode()).hexdigest())
        with patch("codexdevteam_kernel.supervisor.observe_process_identity",
                   return_value=ProcessObservation.PID_REUSED):
            supervisor.run_dispatch_cycle(
                self.lease, cycle_id="stale-process-evidence", capacity=capacity, now=200)
        matching_event_count = sum(
            item["payload"].get("type") == "task.invocation_process_observed"
            and item["payload"].get("invocation_id") == invocation_id
            and item["payload"].get("observation") == "pid_reused"
            for item in self.store.events()
        )
        self.assertEqual(matching_event_count, 1)
        with patch("codexdevteam_kernel.supervisor.observe_process_identity",
                   return_value=ProcessObservation.MATCHES):
            supervisor.run_dispatch_cycle(
                self.lease, cycle_id="stale-process-alive", capacity=capacity, now=201)
        observations = [item["payload"]["observation"] for item in self.store.events()
                        if item["payload"].get("type") == "task.invocation_process_observed"
                        and item["payload"].get("invocation_id") == invocation_id]
        self.assertEqual(observations, ["pid_reused", "matches"])
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "running")

    def test_unverified_process_tree_cancellation_is_persisted_and_keeps_maker_live(self):
        task = TaskRecord("TASK-UNVERIFIED-CANCEL", "Keep unsafe process held",
                          TaskState.CLAIMED, "builder", "medium", ("src/cancel.py",),
                          maker_identity={"unit_id": "builder", "runtime": "codex",
                                          "model": "builder-model"})
        self.store.seed_task(self.lease, task, event_id="seed-unverified-cancel", now=101)
        self.store.set_supervisor_mode(self.lease, "running",
                                       event_id="run-unverified-cancel", now=102)
        self.assertTrue(self.store.start_task_invocation(
            self.lease, task.task_id, "maker:unverified-cancel", now=103))
        result = InvocationResult(
            "maker:unverified-cancel", task.task_id, "maker", "builder", "codex",
            "builder-model", "termination_unverified", None, 1.0, "", "kill uncertain",
            False, hashlib.sha256(b"kill uncertain").hexdigest(), 103, 104,
            role="implementation", process_tree_cancel_method="posix_process_group",
            process_tree_cancel_verified=False,
        )
        self.assertTrue(self.store.record_invocation(self.lease, result, now=104))
        liveness = self.store.task_invocation_liveness(task_id=task.task_id)
        self.assertEqual(liveness[0]["state"], "running")
        health = self.store.evaluate_stagnation(
            self.lease, task.task_id, StagnationSample(False, 0),
            event_id="health-unverified-cancel", policy={"no_progress_ticks": 1}, now=105)
        self.assertEqual(health["action"], "hold_running")
        db = sqlite3.connect(self.store.path)
        try:
            receipt = db.execute(
                "SELECT status, process_tree_cancel_method, process_tree_cancel_verified, "
                "process_tree_cancel_exit_code FROM invocation_receipts "
                "WHERE invocation_id='maker:unverified-cancel'").fetchone()
        finally:
            db.close()
        self.assertEqual(receipt, ("termination_unverified", "posix_process_group", 0, None))

    def test_new_head_recovers_signed_cancellation_after_lease_loss_before_health_decision(self):
        from codexdevteam_kernel.termination import write_cancellation_receipt
        task = TaskRecord("TASK-LEASE-LOSS-CANCEL", "Recover killed maker",
                          TaskState.CLAIMED, "builder", "medium", ("src/lease-loss.py",),
                          maker_identity={"unit_id": "builder", "runtime": "codex",
                                          "model": "builder-model"})
        token = "durable-cancel-token-123456789012345678901234567890"
        self.store.seed_task(self.lease, task, event_id="seed-lease-loss-cancel", now=101)
        self.store.set_supervisor_mode(self.lease, "running",
                                       event_id="run-lease-loss-cancel", now=102)
        self.assertTrue(self.store.start_task_invocation(
            self.lease, task.task_id, "maker:lease-loss", cancellation_token=token, now=103))
        receipt_path = write_cancellation_receipt(
            self.store.cancellation_receipt_directory,
            token=token, invocation_id="maker:lease-loss", task_id=task.task_id,
            method="posix_process_group", exit_code=None, observed_at=104,
        )
        successor = self.store.acquire_head(
            "CODEXDEVTEAM", "successor", now=self.lease.expires_at + 1,
            takeover_confirmed=True,
        )
        cycle = self.supervisor.run_dispatch_cycle(
            successor, cycle_id="cycle-recover-cancellation",
            capacity={"builder": CapacityObservation(True, 1,
                                                       observed_at=self.lease.expires_at + 1)},
            task_health_samples={task.task_id: StagnationSample(False, 0)},
            stagnation_policy={"no_progress_ticks": 1}, now=self.lease.expires_at + 1,
        )
        self.assertEqual(cycle.health_actions[task.task_id]["action"], "redispatch")
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "completed")
        self.assertFalse(receipt_path.exists())
        db = sqlite3.connect(self.store.path)
        try:
            evidence = db.execute(
                "SELECT method, evidence_source FROM invocation_cancellation_receipts "
                "WHERE invocation_id='maker:lease-loss'").fetchone()
        finally:
            db.close()
        self.assertEqual(evidence, ("posix_process_group", "adapter_sidecar"))
        event = next(event["payload"] for event in self.store.events()
                     if event["payload"].get("type") == "runtime.cancellation_verified")
        self.assertNotIn(token, json.dumps(event))

    def test_modified_cancellation_sidecar_cannot_release_maker_liveness(self):
        from codexdevteam_kernel.termination import write_cancellation_receipt
        task = TaskRecord("TASK-TAMPERED-CANCEL", "Keep tampered receipt held",
                          TaskState.CLAIMED, "builder", "medium", ("src/tampered.py",),
                          maker_identity={"unit_id": "builder", "runtime": "codex",
                                          "model": "builder-model"})
        token = "tampered-cancel-token-123456789012345678901234567890"
        self.store.seed_task(self.lease, task, event_id="seed-tampered-cancel", now=101)
        self.store.set_supervisor_mode(self.lease, "running",
                                       event_id="run-tampered-cancel", now=102)
        self.store.start_task_invocation(
            self.lease, task.task_id, "maker:tampered", cancellation_token=token, now=103)
        path = write_cancellation_receipt(
            self.store.cancellation_receipt_directory,
            token=token, invocation_id="maker:tampered", task_id=task.task_id,
            method="posix_process_group", exit_code=None, observed_at=104,
        )
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["task_id"] = "TASK-FORGED"
        path.write_text(json.dumps(envelope), encoding="utf-8")
        self.assertEqual(self.store.recover_invocation_cancellation_receipts(
            self.lease, now=105), ())
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "running")

    def test_dispatch_cycle_runs_opt_in_plan_archival_on_interval(self):
        project = Path(self.temp.name) / "scheduled-archive-project"
        project.mkdir()
        plan = project / "PLAN.md"
        plan.write_text(
            "# Project plan\n\n"
            "### TASK-ARCHIVE-A\n**Title:** Wave A completed task\n**Status:** done\n"
            "**Assigned_To:** builder\n**Priority:** medium\n"
            "**Owned_Paths:** src/old.py\n**Updated_At:** 2026-04-01T00:00:00Z\n\n"
            "### TASK-PLAN-CURRENT\n**Title:** Wave B current task\n**Status:** pending\n"
            "**Assigned_To:** —\n**Priority:** medium\n"
            "**Owned_Paths:** src/current.py\n**Updated_At:** 2026-09-01T00:00:00Z\n",
            encoding="utf-8",
        )
        archived = TaskRecord("TASK-ARCHIVE-A", "Wave A completed task", TaskState.DONE,
                              "builder", "medium", ("src/old.py",))
        self.store.seed_task(self.lease, archived, event_id="seed-scheduled-archive", now=101)
        self.store.set_supervisor_mode(
            self.lease, "running", event_id="run-scheduled-archive", now=102)
        supervisor = Supervisor(self.store, self.registry, SupervisorPolicy(
            require_capacity_observation=False, plan_archive_interval_seconds=20))

        first = supervisor.run_dispatch_cycle(
            self.lease, cycle_id="archive-schedule-first", capacity=None,
            project_root=project, now=103)
        self.assertEqual(first.archived_task_ids, (archived.task_id,))
        self.assertIn("**Archived:** plan/archive/2026-04.md", plan.read_text(encoding="utf-8"))
        self.assertEqual(self.store.maintenance_completed_at("plan_archive"), 103)

        skipped = supervisor.run_dispatch_cycle(
            self.lease, cycle_id="archive-schedule-wait", capacity=None,
            project_root=project, now=110)
        self.assertEqual(skipped.archived_task_ids, ())
        self.assertEqual(self.store.maintenance_completed_at("plan_archive"), 103)

        due = supervisor.run_dispatch_cycle(
            self.lease, cycle_id="archive-schedule-due", capacity=None,
            project_root=project, now=124)
        self.assertEqual(due.archived_task_ids, ())
        self.assertEqual(self.store.maintenance_completed_at("plan_archive"), 124)

    def test_supervisor_launches_claimed_maker_once_in_task_worktree(self):
        project, base, task = self.prepare_claimed_maker("TASK-100")
        manager = GitWorktreeManager(project, Path(self.temp.name) / "managed-100")
        adapter = self.FakeInvocationAdapter(snapshot_sidecars=True)
        memory = EvidenceMemory(Path(self.temp.name) / "memory.sqlite")
        memory.add_fact(fact_id="fact-task-100", kind="hot_file", scope="project",
                        subject="src/main.py", statement="Run the focused module check first.",
                        evidence=("review:RV-100",), origin_project="CODEXDEVTEAM", now=100)
        injection = memory.retrieve_for_task(
            event_id="injection-task-100", task_id=task.task_id,
            project="CODEXDEVTEAM", owned_paths=task.owned_paths, now=101)
        result = self.supervisor.invoke_claimed_task(
            self.lease, task.task_id, invocation_id="maker:100", prompt="implement",
            base_ref=base, worktrees=manager, adapters={"codex": adapter},
            memory_injection=injection, now=104)
        self.assertTrue(Path(result.worktree_path).is_dir())
        self.assertEqual(result.invocation.status, "succeeded")
        self.assertEqual(result.host_commit.status, "refused")
        self.assertEqual(result.host_commit.reasons[0].code, "QUIESCENCE_UNPROVEN")
        self.assertEqual(adapter.requests[0].working_directory, result.worktree_path)
        self.assertNotEqual(adapter.requests[0].state_db_path, str(self.store.path.resolve()))
        snapshot = Path(adapter.requests[0].state_db_path)
        self.assertFalse(snapshot.exists())
        for suffix in ("-journal", "-wal", "-shm"):
            self.assertFalse(Path(str(snapshot) + suffix).exists())
        self.assertEqual(list(snapshot.parent.glob(".codexdevteam-state-*.sqlite")), [])
        self.assertIn("Run the focused module check first.", adapter.requests[0].prompt)
        self.assertIn("review:RV-100", adapter.requests[0].prompt)
        maker_event = next(event["payload"] for event in self.store.events()
                           if event["payload"].get("type") == "runtime.invoked"
                           and event["payload"].get("purpose") == "maker")
        self.assertEqual(maker_event["memory_injection_event_id"], injection.event_id)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.IN_PROGRESS)
        liveness = self.store.task_invocation_liveness(task_id=task.task_id)
        self.assertEqual(len(liveness), 1)
        self.assertEqual(liveness[0]["invocation_id"], "maker:100")
        self.assertEqual(liveness[0]["state"], "completed")
        self.assertEqual(sum(event["payload"].get("type") == "runtime.invoked"
                             for event in self.store.events()), 1)
        with self.assertRaisesRegex(ValueError, "only a claimed task"):
            self.supervisor.invoke_claimed_task(
                self.lease, task.task_id, invocation_id="maker:100-replay", prompt="again",
                base_ref=base, worktrees=manager, adapters={"codex": adapter}, now=105)
        self.assertEqual(len(adapter.requests), 1)

    @unittest.skipUnless(os.name == "nt", "Windows host-commit integration")
    def test_supervisor_host_commits_only_after_windows_job_quiescence_proof(self):
        from dataclasses import replace
        from codexdevteam_kernel.host_commit import QuiescenceProof

        project, base, task = self.prepare_claimed_maker("TASK-100-HOST-COMMIT")
        manager = GitWorktreeManager(project, Path(self.temp.name) / "managed-host-commit")
        delegate = self.FakeInvocationAdapter()

        class QuiescentMaker:
            def invoke(self, request):
                result = delegate.invoke(request)
                output = Path(request.working_directory) / "src" / "host-committed.py"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("value = 1\n", encoding="utf-8")
                return replace(result, quiescence_proof=QuiescenceProof(
                    "windows", "windows_job_object", True, 0,
                    "Windows Job Object fixture proof"))

        cycle = self.supervisor.invoke_claimed_task(
            self.lease, task.task_id, invocation_id="maker:host-commit",
            prompt="implement", base_ref=base, worktrees=manager,
            adapters={"codex": QuiescentMaker()}, now=104)
        self.assertEqual(cycle.host_commit.status, "committed",
                         (cycle.host_commit.reasons, cycle.host_commit.paths))
        self.assertEqual(cycle.host_commit.paths, ("src/host-committed.py",))
        commit_event = next(event["payload"] for event in self.store.events()
                            if event["payload"].get("type") == "host_commit.completed")
        self.assertEqual(commit_event["sha"], cycle.host_commit.sha)
        self.assertEqual(commit_event["invocation_id"], "maker:host-commit")
        self.assertEqual(cycle.host_commit.sha,
                         subprocess.run(["git", "-C", str(project), "rev-parse",
                                         f"refs/heads/codexdevteam/{task.task_id}"],
                                        capture_output=True, text=True, check=True).stdout.strip())
        self.assertEqual(subprocess.run(
            ["git", "-C", cycle.worktree_path, "status", "--porcelain"],
            capture_output=True, text=True, check=True).stdout, "")
        gate_task = self.store.get_task(task.task_id)
        gate = GateRunner(project, Path(self.temp.name) / "host-commit-gate").run(
            gate_task, cycle.worktree_path, expected_sha=cycle.host_commit.sha,
            base_ref=base,
            commands={name: [sys.executable, "-c", "pass"]
                      for name in ("build", "typecheck", "test_full")},
            active_tasks=tuple(self.store.list_tasks()),
        )
        self.assertEqual(gate.status, "passed",
                         {name: check.summary for name, check in gate.checks.items()})
        self.assertEqual(gate.sha, cycle.host_commit.sha)

    def test_supervisor_keeps_control_pending_when_host_commit_refuses(self):
        from codexdevteam_kernel.control_queue import submit_control
        from codexdevteam_kernel.gate import GateResult

        project, base, task = self.prepare_claimed_maker("TASK-100-COMMIT-REFUSED")
        manager = GitWorktreeManager(project, Path(self.temp.name) / "managed-commit-refused")
        delegate = self.FakeInvocationAdapter()

        class ReportingMaker:
            def invoke(self, request):
                result = delegate.invoke(request)
                submit_control(
                    Path(request.control_outbox_path), task_id=task.task_id,
                    worker_id="builder", event_id="uncommitted-report",
                    requested_state="needs_review",
                )
                output = Path(request.working_directory) / "src" / "uncommitted.py"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("value = 2\n", encoding="utf-8")
                return result

        cycle = self.supervisor.invoke_claimed_task(
            self.lease, task.task_id, invocation_id="maker:commit-refused",
            prompt="implement", base_ref=base, worktrees=manager,
            adapters={"codex": ReportingMaker()}, now=104)
        self.assertEqual(cycle.host_commit.status, "refused")
        self.assertEqual(cycle.control_applied, ())
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.IN_PROGRESS)
        outbox = Path(cycle.worktree_path) / ".codexdevteam" / "control" / "outbox"
        self.assertTrue((outbox / "uncommitted-report.json").is_file())
        fabricated = GateResult(task.task_id, base, "passed", {}, "f" * 64, "fixture")
        with self.assertRaisesRegex(ValueError, "exact SHA published by a successful host commit"):
            self.supervisor.finalize_maker_gate(
                self.lease, cycle, fabricated, attempt_event_id="forbidden-gate", now=105)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object host-commit integration")
    def test_deferred_maker_control_drains_only_after_head_gate_registration(self):
        from dataclasses import replace
        from codexdevteam_kernel.control_queue import submit_control
        from codexdevteam_kernel.host_commit import QuiescenceProof
        project, base, task = self.prepare_claimed_maker("TASK-103")
        manager = GitWorktreeManager(project, Path(self.temp.name) / "managed-103")

        class CommittingMaker:
            def invoke(inner_self, request):
                result = self.FakeInvocationAdapter().invoke(request)
                output = Path(request.working_directory) / "src" / "change.py"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("answer = 42\n", encoding="utf-8")
                submit_control(
                    Path(request.control_outbox_path), task_id=task.task_id,
                    worker_id="builder", event_id="a-maker-progress",
                    progress_note="Implementation is ready for gate review.",
                )
                return replace(result, quiescence_proof=QuiescenceProof(
                    "windows", "windows_job_object", True, 0, "fixture"))

        cycle = self.supervisor.invoke_claimed_task(
            self.lease, task.task_id, invocation_id="maker:103", prompt="implement",
            base_ref=base, worktrees=manager, adapters={"codex": CommittingMaker()},
            defer_control_drain=True, now=104)
        self.assertEqual(cycle.host_commit.status, "committed",
                         (cycle.host_commit.reasons, cycle.host_commit.paths))
        outbox = Path(cycle.worktree_path) / ".codexdevteam" / "control" / "outbox"
        sha = cycle.host_commit.sha
        gate = GateRunner(project, Path(self.temp.name) / "gate-artifacts-103").run(
            self.store.get_task(task.task_id), cycle.worktree_path,
            expected_sha=sha, base_ref=base,
            commands={name: [sys.executable, "-c", "pass"]
                      for name in ("build", "typecheck", "test_full")},
            active_tasks=tuple(self.store.list_tasks()),
        )
        self.assertEqual(gate.status, "passed",
                         {name: check.summary for name, check in gate.checks.items()})
        submit_control(
            outbox, task_id=task.task_id, worker_id="builder", event_id="maker-needs-review",
            requested_state="needs_review", test_evidence=(gate.test_run_result.evidence_ref,),
            head_sha=sha,
        )
        finalized = self.supervisor.finalize_maker_gate(
            self.lease, cycle, gate, attempt_event_id="gate-maker-103", now=105)
        self.assertEqual(finalized.control_applied,
                         ("a-maker-progress", "maker-needs-review"))
        self.assertEqual(finalized.control_rejected, ())
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.NEEDS_REVIEW)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object host-commit integration")
    def test_host_gate_moves_task_to_review_without_maker_sha_report(self):
        from dataclasses import replace
        from codexdevteam_kernel.host_commit import QuiescenceProof

        project, base, task = self.prepare_claimed_maker("TASK-105-GATE-REVIEW")
        manager = GitWorktreeManager(project, Path(self.temp.name) / "managed-105-gate-review")

        class CommittingMaker:
            def invoke(inner_self, request):
                result = self.FakeInvocationAdapter().invoke(request)
                output = Path(request.working_directory) / "src" / "change.py"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("answer = 43\\n", encoding="utf-8")
                return replace(result, quiescence_proof=QuiescenceProof(
                    "windows", "windows_job_object", True, 0, "fixture"))

        cycle = self.supervisor.invoke_claimed_task(
            self.lease, task.task_id, invocation_id="maker:105-gate-review",
            prompt="implement", base_ref=base, worktrees=manager,
            adapters={"codex": CommittingMaker()}, defer_control_drain=True, now=104)
        self.assertEqual(cycle.host_commit.status, "committed", cycle.host_commit.reasons)
        gate = GateRunner(project, Path(self.temp.name) / "gate-artifacts-105-review").run(
            self.store.get_task(task.task_id), cycle.worktree_path,
            expected_sha=cycle.host_commit.sha, base_ref=base,
            commands={name: [sys.executable, "-c", "pass"]
                      for name in ("build", "typecheck", "test_full")},
            active_tasks=tuple(self.store.list_tasks()),
        )
        self.assertEqual(gate.status, "passed",
                         {name: check.summary for name, check in gate.checks.items()})

        self.supervisor.finalize_maker_gate(
            self.lease, cycle, gate, attempt_event_id="gate-maker-105-review", now=105)

        reviewed = self.store.get_task(task.task_id)
        self.assertEqual(reviewed.state, TaskState.NEEDS_REVIEW)
        self.assertIn(gate.test_run_result.evidence_ref, reviewed.test_evidence)

    def test_park_wins_atomically_before_maker_runtime_launch(self):
        project, base, task = self.prepare_claimed_maker("TASK-101")
        manager = GitWorktreeManager(project, Path(self.temp.name) / "managed-101")
        adapter = self.FakeInvocationAdapter()
        start = self.store.start_task_invocation

        def park_before_start(*args, **kwargs):
            self.store.set_supervisor_mode(self.lease, "parked", event_id="park-before-start",
                                           reason="operator stop", now=103)
            return start(*args, **kwargs)

        with patch.object(self.store, "start_task_invocation", side_effect=park_before_start):
            with self.assertRaisesRegex(LeaseError, "supervisor running mode"):
                self.supervisor.invoke_claimed_task(
                    self.lease, task.task_id, invocation_id="maker:101", prompt="implement",
                    base_ref=base, worktrees=manager, adapters={"codex": adapter}, now=104)
        self.assertEqual(adapter.requests, [])
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.CLAIMED)

    def test_runtime_adapter_exception_is_redacted_and_recorded_as_launch_failure(self):
        project, base, task = self.prepare_claimed_maker("TASK-102")
        manager = GitWorktreeManager(project, Path(self.temp.name) / "managed-102")
        adapter = self.FakeInvocationAdapter(raises=True, snapshot_sidecars=True)
        result = self.supervisor.invoke_claimed_task(
            self.lease, task.task_id, invocation_id="maker:102", prompt="implement",
            base_ref=base, worktrees=manager, adapters={"codex": adapter}, now=104)
        self.assertEqual(result.invocation.status, "launch_failed")
        self.assertNotIn("private details", result.invocation.stderr)
        snapshot = Path(adapter.requests[0].state_db_path)
        self.assertFalse(snapshot.exists())
        self.assertFalse(any(Path(str(snapshot) + suffix).exists()
                             for suffix in ("-journal", "-wal", "-shm")))
        event = next(event for event in self.store.events()
                     if event["payload"].get("type") == "runtime.invoked")
        self.assertEqual(event["payload"]["status"], "launch_failed")

    def test_dispatch_cycle_recovers_pending_plan_projection_before_parked_return(self):
        root = Path(self.temp.name) / "projection-project"
        root.mkdir()
        plan = root / "PLAN.md"
        plan.write_text(
            "### TASK-RECOVER\n**Title:** Recover projection\n**Status:** pending\n"
            "**Assigned_To:** builder\n**Priority:** medium\n"
            "**Owned_Paths:** src/recover.py\n",
            encoding="utf-8",
        )
        task = TaskRecord("TASK-RECOVER", "Recover projection", TaskState.PENDING,
                          "builder", "medium", ("src/recover.py",))
        self.store.seed_task(self.lease, task, event_id="seed-recover-projection", now=101)
        expected = hashlib.sha256(plan.read_bytes()).hexdigest()
        with patch("codexdevteam_kernel.state._atomic_replace_plan",
                   side_effect=OSError("interrupted projection")):
            with self.assertRaises(PlanProjectionPending):
                self.store.transition_task_with_plan(
                    self.lease, task.task_id, TaskState.CLAIMED,
                    event_id="claim-recover-projection",
                    expected_state=TaskState.PENDING, project_root=root,
                    expected_plan_sha256=expected, now=102,
                )
        parked = self.supervisor.run_dispatch_cycle(
            self.lease, cycle_id="recover-before-parked-return", capacity={}, now=103)
        self.assertEqual(parked.mode, "parked")
        self.assertIn("**Status:** claimed", plan.read_text(encoding="utf-8"))
        self.assertEqual(self.store.pending_plan_projections(), ())

    def test_supervisor_is_parked_by_default_then_dispatches_one_dependency_ready_task(self):
        ready = TaskRecord("TASK-60", "Ready work", TaskState.PENDING, None,
                           "high", ("src/**",))
        blocked = TaskRecord("TASK-61", "Wait for dependency", TaskState.PENDING, None,
                             "medium", ("docs/**",), depends_on=("TASK-62",))
        dependency = TaskRecord("TASK-62", "Unfinished prerequisite", TaskState.PENDING,
                                None, "low", ("tests/**",))
        for task in (ready, blocked, dependency):
            self.store.seed_task(self.lease, task, event_id=f"seed-{task.task_id}", now=101)
        capacity = {"builder": CapacityObservation(True, 1, observed_at=101)}
        parked = self.supervisor.run_dispatch_cycle(self.lease, cycle_id="cycle-parked",
                                                    capacity=capacity, now=102)
        self.assertEqual(parked.mode, "parked")
        self.assertEqual(parked.assignments, ())
        self.assertTrue(self.store.set_supervisor_mode(self.lease, "running",
                                                       event_id="supervisor-resumed", now=102))
        active = self.supervisor.run_dispatch_cycle(self.lease, cycle_id="cycle-active",
                                                    capacity=capacity, now=103)
        self.assertEqual(active.assignments, ("TASK-60",))
        self.assertIn("TASK-61", active.deferred)
        self.assertEqual(self.store.get_task("TASK-60").state, TaskState.CLAIMED)
        self.assertEqual(self.store.get_task("TASK-60").maker_identity["unit_id"], "builder")
        self.assertEqual(active.deferred["TASK-62"], "no eligible unoccupied worker is available")
        self.assertEqual(self.store.events()[-1]["payload"]["type"], "task.assigned")

    def test_supervisor_requires_fresh_capacity_and_park_wins_at_assignment(self):
        task = TaskRecord("TASK-63", "Bounded dispatch", TaskState.PENDING, None,
                          "high", ("src/**",))
        self.store.seed_task(self.lease, task, event_id="seed-bounded", now=101)
        self.store.set_supervisor_mode(self.lease, "running", event_id="resume-bounded", now=102)
        with self.assertRaisesRegex(ValueError, "requires capacity"):
            self.supervisor.run_dispatch_cycle(self.lease, cycle_id="no-capacity",
                                               capacity=None, now=103)
        self.store.set_supervisor_mode(self.lease, "parked", reason="pause test",
                                       event_id="park-bounded", now=104)
        with self.assertRaisesRegex(LeaseError, "running mode"):
            self.store.assign_pending_task(self.lease, task.task_id, "builder", self.registry,
                                           event_id="late-dispatch", require_strict=True,
                                           require_supervisor_running=True, now=105)


class HandoverTranslationTests(unittest.TestCase):
    def setUp(self):
        from codexdevteam_kernel import handover_map_template, plan_handover, stage_handover
        self.plan_handover = plan_handover
        self.handover_map_template = handover_map_template
        self.stage_handover = stage_handover
        self.registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": [], "head_candidate": None, "defined": {},
        })

    @staticmethod
    def source_plan():
        return (
            "# Project plan\n\n"
            "### TASK-OLD-DONE\n**Title:** Completed upstream\n**Status:** done\n"
            "**Assigned_To:** old-builder\n**Priority:** medium\n"
            "**Owned_Paths:** src/upstream.py\n\n"
            "### TASK-OLD-OPEN\n**Title:** Continue work\n**Status:** in_progress\n"
            "**Assigned_To:** old-builder\n**Priority:** high\n"
            "**Owned_Paths:** src/continuation.py\n"
            "**Description:** legacy descriptive context\n"
            "**Progress_Notes:** in-flight notes to review\n"
            "**Updated_At:** 2026-10-01T10:00:00Z\n"
            "**Depends_On:** TASK-OLD-DONE\n"
        )

    def make_mapping(self, source, open_item=None):
        return {
            "protocol_version": 1,
            "source_plan_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "tasks": {
                "TASK-OLD-DONE": {"disposition": "historical"},
                "TASK-OLD-OPEN": open_item or {
                    "disposition": "open", "state": "pending",
                    "assigned_worker": None, "protected_grants": [],
                },
            },
        }

    def test_handover_preview_is_hash_bound_does_not_carry_active_or_review_authority(self):
        source = self.source_plan()
        preview = self.plan_handover(source.encode("utf-8"), self.make_mapping(source),
                                     registry=self.registry)
        self.assertEqual(tuple(task.task_id for task in preview.tasks), ("TASK-OLD-OPEN",))
        self.assertEqual(preview.tasks[0].state, TaskState.PENDING)
        self.assertIsNone(preview.tasks[0].assigned_worker)
        self.assertIsNone(preview.tasks[0].maker_identity)
        self.assertEqual(preview.historical_task_ids, ("TASK-OLD-DONE",))
        self.assertIn("TASK-OLD-DONE", preview.tasks[0].depends_on)
        lost_fields = dict(preview.unmapped_source_fields)["TASK-OLD-OPEN"]
        self.assertEqual(lost_fields, ("Description", "Progress_Notes", "Updated_At"))
        preview_json = json.dumps(preview.to_dict())
        self.assertNotIn("legacy descriptive context", preview_json)
        self.assertNotIn("in-flight notes to review", preview_json)
        self.assertFalse(preview.to_dict()["write_performed"])
        self.assertFalse(preview.to_dict()["activation_authorized"])

    def test_handover_preview_refuses_stale_incomplete_and_active_state_mappings(self):
        source = self.source_plan()
        mapping = self.make_mapping(source)
        mapping["source_plan_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.plan_handover(source, mapping, registry=self.registry)
        mapping = self.make_mapping(source)
        del mapping["tasks"]["TASK-OLD-OPEN"]
        with self.assertRaisesRegex(ValueError, "coverage is incomplete"):
            self.plan_handover(source, mapping, registry=self.registry)
        mapping = self.make_mapping(source, {
            "disposition": "open", "state": "needs_review",
            "assigned_worker": None, "protected_grants": [],
        })
        with self.assertRaisesRegex(ValueError, "pending or blocked"):
            self.plan_handover(source, mapping, registry=self.registry)

    def test_handover_map_template_is_hash_bound_but_cannot_be_applied_unreviewed(self):
        source = self.source_plan()
        draft = self.handover_map_template(source.encode("utf-8"))
        self.assertEqual(draft["protocol_version"], 2)
        self.assertEqual(draft["source_plan_sha256"],
                         hashlib.sha256(source.encode("utf-8")).hexdigest())
        self.assertEqual(draft["tasks"]["TASK-OLD-DONE"], {"disposition": "historical"})
        self.assertEqual(draft["tasks"]["TASK-OLD-OPEN"]["disposition"], "")
        with self.assertRaisesRegex(ValueError, "complete open-task mapping"):
            self.plan_handover(source, draft, registry=self.registry)

    def test_duplicate_unmodeled_source_fields_are_reported_without_values(self):
        source = self.source_plan().replace(
            "**Owned_Paths:** src/upstream.py\n",
            "**Owned_Paths:** src/upstream.py\n**Artifacts:** —\n"
            "**Artifacts:** - reports/legacy.txt\n",
        )
        mapping = self.handover_map_template(source)
        mapping["tasks"]["TASK-OLD-OPEN"] = {
            "disposition": "open", "state": "pending",
            "assigned_worker": None, "protected_grants": [],
        }
        mapping["source_field_dispositions"] = {
            task_id: {name: "exclude" for name in fields}
            for task_id, fields in mapping["source_field_dispositions"].items()
        }
        mapping["source_field_dispositions"]["TASK-OLD-DONE"]["Artifacts"] = "preserve"
        preview = self.plan_handover(source, mapping, registry=self.registry)
        self.assertEqual(dict(preview.source_field_conflicts)["TASK-OLD-DONE"], ("Artifacts",))
        self.assertEqual(preview.context_field_values,
                         (("TASK-OLD-DONE", "Artifacts", 1, "—"),
                          ("TASK-OLD-DONE", "Artifacts", 2, "- reports/legacy.txt")))
        serialized = json.dumps(preview.to_dict())
        self.assertIn("Artifacts", serialized)
        self.assertNotIn("reports/legacy.txt", serialized)

    def test_handover_preview_refuses_unverified_target_worker_and_overlapping_territory(self):
        source = self.source_plan()
        unverified = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["builder"], "head_candidate": None,
            "defined": {"builder": {"role": "implementation", "capability_floor": "standard",
                                     "runtime": "codex", "model": "configured-model",
                                     "control_mode": "legacy"}},
        })
        mapped = self.make_mapping(source, {
            "disposition": "open", "state": "pending",
            "assigned_worker": "builder", "protected_grants": [],
        })
        with self.assertRaisesRegex(ValueError, "not strict-verified"):
            self.plan_handover(source, mapped, registry=unverified)
        overlap_source = source.replace("**Status:** done", "**Status:** pending")
        overlap_source = overlap_source.replace("src/continuation.py", "src/**")
        overlap = self.make_mapping(overlap_source)
        overlap["tasks"]["TASK-OLD-DONE"] = {
            "disposition": "open", "state": "pending",
            "assigned_worker": None, "protected_grants": [],
        }
        with self.assertRaisesRegex(ValueError, "active territory overlaps"):
            self.plan_handover(overlap_source, overlap, registry=self.registry)

    def test_handover_staging_is_hash_bound_atomic_and_does_not_authorize_activation(self):
        from codexdevteam_kernel import StateStore

        source = self.source_plan()
        for line in (
            "**Description:** legacy descriptive context\n",
            "**Progress_Notes:** in-flight notes to review\n",
            "**Updated_At:** 2026-10-01T10:00:00Z\n",
        ):
            source = source.replace(line, "")
        mapping = self.make_mapping(source)
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "target-state.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "staging-head", now=100)
            preview = self.stage_handover(
                source.encode("utf-8"), mapping, registry=self.registry, store=store,
                lease=lease, event_id="stage-handover-1", now=101,
            )
            self.assertEqual(tuple(task.task_id for task in store.list_tasks()),
                             ("TASK-OLD-OPEN",))
            self.assertIsNone(store.get_task("TASK-OLD-DONE"))
            self.assertEqual(store.historical_task_ids(), ("TASK-OLD-DONE",))
            self.assertIn("TASK-OLD-DONE", store.completed_dependency_ids())
            staged_event = next(event for event in store.events()
                                if event["event_id"] == "stage-handover-1")
            self.assertEqual(staged_event["payload"]["source_plan_sha256"],
                             preview.source_plan_sha256)
            self.assertFalse(staged_event["payload"]["activation_authorized"])
            self.assertFalse(store.import_handover_tasks(
                lease, preview.tasks, preview.historical_task_ids,
                source_plan_sha256=preview.source_plan_sha256,
                mapping_sha256=preview.mapping_sha256,
                event_id="stage-handover-1", now=102,
            ))
            with self.assertRaisesRegex(LeaseError, "empty target"):
                store.import_handover_tasks(
                    lease, preview.tasks, preview.historical_task_ids,
                    source_plan_sha256=preview.source_plan_sha256,
                    mapping_sha256=preview.mapping_sha256,
                    event_id="stage-handover-2", now=103,
                )

    def test_handover_staging_refuses_undisposed_legacy_fields_without_writes(self):
        from codexdevteam_kernel import StateStore

        source = self.source_plan()
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "target-state.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "staging-head", now=100)
            with self.assertRaisesRegex(ValueError, "explicit disposition"):
                self.stage_handover(source, self.make_mapping(source), registry=self.registry,
                                    store=store, lease=lease, event_id="refused-stage", now=101)
            self.assertEqual(store.list_tasks(), ())
            self.assertEqual(store.events(), [])

    def test_handover_v2_preserves_selected_fields_excludes_explicit_drops_and_redacts_preview(self):
        from codexdevteam_kernel import StateStore

        source = self.source_plan()
        draft = self.handover_map_template(source)
        draft["tasks"]["TASK-OLD-OPEN"] = {
            "disposition": "open", "state": "pending",
            "assigned_worker": None, "protected_grants": [],
        }
        draft["source_field_dispositions"]["TASK-OLD-OPEN"] = {
            "Description": "preserve", "Progress_Notes": "preserve",
            "Updated_At": "exclude",
        }
        preview = self.plan_handover(source, draft, registry=self.registry)
        serialized = json.dumps(preview.to_dict())
        self.assertIn('"Progress_Notes": "preserve"', serialized)
        self.assertNotIn("legacy descriptive context", serialized)
        self.assertNotIn("in-flight notes to review", serialized)
        self.assertEqual(preview.context_field_values, (
            ("TASK-OLD-OPEN", "Description", 1, "legacy descriptive context"),
            ("TASK-OLD-OPEN", "Progress_Notes", 1, "in-flight notes to review"),
        ))
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "context-stage", now=100)
            self.stage_handover(source, draft, registry=self.registry, store=store,
                                lease=lease, event_id="stage-context", now=101)
            stored = store.handover_context_fields("TASK-OLD-OPEN")
            self.assertEqual([(row["field_name"], row["field_value"]) for row in stored], [
                ("Description", "legacy descriptive context"),
                ("Progress_Notes", "in-flight notes to review"),
            ])
            self.assertEqual(len(stored), 2)
            event = next(item["payload"] for item in store.events()
                         if item["event_id"] == "stage-context")
            self.assertEqual(event["context_field_count"], 2)
            self.assertNotIn("legacy descriptive context", json.dumps(event))
            db = sqlite3.connect(store.path)
            try:
                db.execute("UPDATE handover_context_fields SET field_value='tampered' "
                           "WHERE field_name='Description'")
                db.commit()
            finally:
                db.close()
            with self.assertRaisesRegex(LeaseError, "provenance verification"):
                store.handover_context_fields("TASK-OLD-OPEN")

    def test_handover_v2_refuses_secret_like_preserved_context_without_partial_import(self):
        from codexdevteam_kernel import StateStore

        secret = "sk-" + "A" * 24
        source = self.source_plan().replace("legacy descriptive context", secret)
        draft = self.handover_map_template(source)
        draft["tasks"]["TASK-OLD-OPEN"] = {
            "disposition": "open", "state": "pending",
            "assigned_worker": None, "protected_grants": [],
        }
        draft["source_field_dispositions"]["TASK-OLD-OPEN"]["Description"] = "preserve"
        draft["source_field_dispositions"]["TASK-OLD-OPEN"]["Progress_Notes"] = "exclude"
        draft["source_field_dispositions"]["TASK-OLD-OPEN"]["Updated_At"] = "exclude"
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "context-secret", now=100)
            with self.assertRaisesRegex(ValueError, "secret-like content") as caught:
                self.stage_handover(source, draft, registry=self.registry, store=store,
                                    lease=lease, event_id="stage-secret", now=101)
            self.assertNotIn(secret, str(caught.exception))
            self.assertEqual(store.list_tasks(), ())
            self.assertEqual(store.handover_context_fields(), ())

    def test_handover_plan_cli_is_read_only_and_emits_reviewable_preview(self):
        from codexdevteam_kernel.onboarding_cli import main

        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "incumbent"
            project.mkdir()
            (project / ".devteam").mkdir()
            source = (
                "# Project plan\n\n### TASK-CLI-OPEN\n**Title:** Continue safely\n"
                "**Status:** in_progress\n**Assigned_To:** legacy-builder\n"
                "**Priority:** medium\n**Owned_Paths:** src/cli-open.py\n"
            )
            plan_path = project / "PLAN.md"
            plan_path.write_text(source, encoding="utf-8")
            mapping_path = Path(temp) / "handover-map.json"
            mapping_path.write_text(json.dumps({
                "protocol_version": 1,
                "source_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                "tasks": {"TASK-CLI-OPEN": {
                    "disposition": "open", "state": "pending",
                    "assigned_worker": None, "protected_grants": [],
                }},
            }), encoding="utf-8")
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(json.dumps({
                "protocol_version": 1, "active": [], "head_candidate": None, "defined": {},
            }), encoding="utf-8")
            output = io.StringIO()
            with patch("sys.argv", [
                "codexdevteam", "handover-plan", "--project", str(project),
                "--mapping", str(mapping_path), "--registry", str(registry_path),
            ]), contextlib.redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertIn('"activation_authorized": false', output.getvalue())
            self.assertIn('"state": "pending"', output.getvalue())
            with patch("sys.argv", [
                "codexdevteam", "handover-map-template", "--project", str(project),
            ]), contextlib.redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertIn('"disposition": ""', output.getvalue())
            self.assertEqual(plan_path.read_text(encoding="utf-8"), source)
            self.assertFalse((project / ".codexdevteam").exists())

    def test_handover_stage_cli_imports_only_to_separate_inactive_state(self):
        from codexdevteam_kernel.installer import install_devdepartment_sidecar
        from codexdevteam_kernel.installer_resources import devdepartment_sidecar_files
        from codexdevteam_kernel.onboarding_cli import main
        from codexdevteam_kernel.state import StateStore

        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "incumbent"
            project.mkdir()
            (project / ".devteam").mkdir()
            source = (
                "# Project plan\n\n### TASK-STAGE-CLI\n**Title:** Mapped work\n"
                "**Status:** in_progress\n**Assigned_To:** old-worker\n"
                "**Priority:** medium\n**Owned_Paths:** src/mapped.py\n"
                "**Description:** retain this operator-approved context\n"
            )
            plan_path = project / "PLAN.md"
            plan_path.write_text(source, encoding="utf-8")
            install_devdepartment_sidecar(project, devdepartment_sidecar_files())
            mapping_path = Path(temp) / "mapping.json"
            mapping = self.handover_map_template(plan_path.read_bytes())
            mapping["tasks"]["TASK-STAGE-CLI"] = {
                "disposition": "open", "state": "pending",
                "assigned_worker": None, "protected_grants": [],
            }
            mapping["source_field_dispositions"]["TASK-STAGE-CLI"]["Description"] = "preserve"
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(json.dumps({
                "protocol_version": 1, "active": [], "head_candidate": None, "defined": {},
            }), encoding="utf-8")
            state_path = Path(temp) / "target-state.sqlite"
            output = io.StringIO()
            with patch("sys.argv", [
                "codexdevteam", "handover-stage", "--project", str(project),
                "--mapping", str(mapping_path), "--registry", str(registry_path),
                "--state-db", str(state_path),
            ]), contextlib.redirect_stdout(output):
                self.assertEqual(main(), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["staged_task_ids"], ["TASK-STAGE-CLI"])
            self.assertFalse(report["source_process_fenced"])
            self.assertFalse(report["activation_authorized"])
            self.assertEqual(plan_path.read_text(encoding="utf-8"), source)
            marker = json.loads((project / ".codexdevteam" / "installation.json").read_text())
            self.assertFalse(marker["activated"])
            self.assertEqual(marker["active_head"], None)
            self.assertEqual([task.task_id for task in StateStore(state_path).list_tasks()],
                             ["TASK-STAGE-CLI"])
            self.assertEqual(StateStore(state_path).handover_context_fields()[0]["field_value"],
                             "retain this operator-approved context")


class CompatibilityGateTests(unittest.TestCase):
    def test_only_exact_wave_e_metadata_allows_inactive_sidecar_mode(self):
        from codexdevteam_kernel import (CompatibilityLevel, DEVDEPARTMENT_WAVE_E_SHA,
                                         assess_compatibility)
        baseline = {"system": "DEVDEPARTMENT", "revision": DEVDEPARTMENT_WAVE_E_SHA,
                    "sync_manifest_version": 1, "sync_role": "pack", "plan_version": "6.31",
                    "framework_version": None}
        decision = assess_compatibility(baseline)
        self.assertEqual(decision.level, CompatibilityLevel.SIDECAR_ONLY)
        self.assertFalse(decision.task_state_interoperable)
        self.assertFalse(decision.activation_allowed)
        changed = assess_compatibility({**baseline, "revision": "other-revision"})
        self.assertEqual(changed.level, CompatibilityLevel.REFUSED)

    def test_native_schema_gate_does_not_imply_activation_and_rejects_skew(self):
        from codexdevteam_kernel import (CompatibilityLevel, assess_compatibility,
                                         CONTROL_VERSION, PROTOCOL_VERSION, REGISTRY_VERSION)
        native = {"system": "CODEXDEVTEAM", "task_protocol_version": PROTOCOL_VERSION,
                  "registry_version": REGISTRY_VERSION, "control_version": CONTROL_VERSION}
        decision = assess_compatibility(native)
        self.assertEqual(decision.level, CompatibilityLevel.NATIVE)
        self.assertTrue(decision.task_state_interoperable)
        self.assertFalse(decision.activation_allowed)
        self.assertEqual(assess_compatibility({**native, "control_version": 999}).level,
                         CompatibilityLevel.REFUSED)


class FreshProjectPilotTests(unittest.TestCase):
    def test_fresh_install_firewall_control_gate_and_review_vertical_slice(self):
        from codexdevteam_kernel.codex_hook import evaluate_codex_file_event
        from codexdevteam_kernel.control_queue import drain_control_outbox, submit_control
        from codexdevteam_kernel.supervisor import Supervisor

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "fresh-project"
            project.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(project), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()

            git("init")
            git("config", "user.email", "pilot@example.invalid")
            git("config", "user.name", "CODEXDEVTEAM Pilot")
            (project / "README.md").write_text("fresh pilot\n", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "base.py").write_text("BASE = True\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-m", "fresh project baseline")
            base_sha = git("rev-parse", "HEAD")

            env = os.environ.copy()
            source_root = str(Path(__file__).parents[1] / "src")
            env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
            installed = subprocess.run(
                [sys.executable, "-m", "codexdevteam_kernel.onboarding_cli", "init",
                 "--project", str(project)], env=env, capture_output=True, text=True, check=True)
            self.assertIn("No HEAD lease was acquired", installed.stdout)
            marker = json.loads((project / ".codexdevteam" / "installation.json").read_text())
            self.assertIsNone(marker["active_head"])
            self.assertFalse(marker["activated"])
            self.assertEqual(inspect_project(project).mode, OnboardingMode.CODEXDEVTEAM_UPGRADE)
            git("add", ".codexdevteam")
            git("commit", "-m", "install inactive CODEXDEVTEAM defaults")
            base_sha = git("rev-parse", "HEAD")
            exclude = project / ".git" / "info" / "exclude"
            exclude.write_text(exclude.read_text(encoding="utf-8")
                               + "\n/PLAN.md\n/.codexdevteam/project/*.sqlite*\n"
                                 "/.codexdevteam/project/control-outbox/\n", encoding="utf-8")
            plan = project / "PLAN.md"
            plan.write_text(
                "### TASK-PILOT\n**Title:** Update one owned module\n**Status:** claimed\n"
                "**Assigned_To:** builder\n**Priority:** medium\n**Owned_Paths:** src/**\n",
                encoding="utf-8",
            )

            database_path = project / ".codexdevteam" / "project" / "pilot-state.sqlite"
            store = StateStore(database_path)
            lease = store.acquire_head("CODEXDEVTEAM", "pilot-harness", ttl_seconds=1000, now=100)
            task = TaskRecord(
                "TASK-PILOT", "Update one owned module", TaskState.CLAIMED, "builder",
                "medium", ("src/**",), maker_identity={
                    "unit_id": "builder", "runtime": "codex", "model": "configured-builder"})
            store.seed_task(lease, task, event_id="pilot-seed", now=101)
            hook_env = {
                "CODEXDEVTEAM_WORKER_ID": "builder", "CODEXDEVTEAM_TASK_ID": task.task_id,
                "CODEXDEVTEAM_STATE_DB": str(database_path),
                "CODEXDEVTEAM_WORKER_ROLE": "implementation",
                "CODEXDEVTEAM_CAPABILITY_FLOOR": "standard",
                "CODEXDEVTEAM_RUNTIME": "codex", "CODEXDEVTEAM_MODEL": "configured-builder",
            }
            def hook(path):
                patch = ("*** Begin Patch\n*** Add File: " + path
                         + "\n+PILOT = True\n*** End Patch")
                return evaluate_codex_file_event({
                    "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                    "tool_input": {"command": patch}, "cwd": str(project)}, hook_env)

            self.assertTrue(hook("src/owned.py").allowed)
            self.assertFalse(hook("docs/outside.md").allowed)
            outbox = project / ".codexdevteam" / "project" / "control-outbox"
            submit_control(outbox, task_id=task.task_id, worker_id="builder",
                           requested_state="in_progress", progress_note="Pilot work started",
                           event_id="pilot-progress")
            self.assertEqual(drain_control_outbox(
                store, lease, outbox, project_root=project, now=102)["applied"],
                             ("pilot-progress",))
            self.assertIn("**Status:** in_progress", plan.read_text(encoding="utf-8"))

            (project / "src" / "owned.py").write_text("PILOT = True\n", encoding="utf-8")
            git("add", "src/owned.py")
            git("commit", "-m", "pilot owned edit")
            sha = git("rev-parse", "HEAD")
            test_result = GateRunner(project, Path(temporary) / "gate-artifacts").run_test_command(
                task, [sys.executable, "-c", "print('pilot test passed')"],
                worktree=project, expected_sha=sha, name="test_full")
            evidence_ref = store.register_test_run(
                lease, task.task_id, test_result, event_id="pilot-test-receipt", now=103)
            submit_control(outbox, task_id=task.task_id, worker_id="builder",
                           requested_state="needs_review", test_evidence=(evidence_ref,),
                           head_sha=sha, event_id="pilot-needs-review")
            self.assertEqual(drain_control_outbox(
                store, lease, outbox, project_root=project, now=104)["applied"],
                             ("pilot-needs-review",))
            self.assertIn("**Status:** needs_review", plan.read_text(encoding="utf-8"))

            gate = GateRunner(project, Path(temporary) / "gate-artifacts").run(
                store.get_task(task.task_id), project, expected_sha=sha, base_ref=base_sha,
                commands={name: [sys.executable, "-c", "pass"]
                          for name in ("build", "typecheck", "test_full")},
                active_tasks=tuple(store.list_tasks()))
            self.assertEqual(gate.status, "passed")
            empty_registry = WorkerRegistry.from_dict({
                "protocol_version": 1, "active": [], "head_candidate": None, "defined": {}})
            Supervisor(store, empty_registry).record_gate_outcome(
                lease, gate, attempt_event_id="pilot-gate", now=105)
            checker = WorkerIdentity("reviewer", "judgment", "advanced",
                                     "fixture-review", "configured-reviewer")
            store.set_supervisor_mode(lease, "running", event_id="pilot-review-mode", now=105.5)
            store.start_checker_invocation(
                lease, task.task_id,
                {"unit_id": checker.unit_id, "runtime": checker.runtime,
                 "model": checker.model},
                sha=sha, gate_fingerprint=gate.fingerprint,
                invocation_id="pilot-checker", now=105.6)
            store.record_invocation(lease, InvocationResult(
                "pilot-checker", task.task_id, "checker", checker.unit_id, checker.runtime,
                checker.model, "succeeded", 0, 0.1, "approved", "", False,
                hashlib.sha256(b"approved").hexdigest(), 106, 107, sha, gate.fingerprint,
                role=checker.role), now=107)
            verdict = ReviewVerdict(task.task_id, sha, gate.fingerprint, checker, "approved",
                                    "Pilot mechanical gate reviewed", ("gate:pilot-artifact",))
            self.assertTrue(store.apply_review(
                lease, verdict, gate, checker_invocation_id="pilot-checker",
                event_id="pilot-review", project_root=project,
                expected_plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(), now=108))
            self.assertEqual(store.get_task(task.task_id).state, TaskState.DONE)
            self.assertIn("**Status:** done", plan.read_text(encoding="utf-8"))
            from codexdevteam_kernel import measure_pilot
            metrics = measure_pilot(store)
            self.assertEqual((metrics.reviewed_tasks, metrics.first_pass_approved,
                              metrics.review_sessions, metrics.gate_attempts,
                              metrics.gate_rejections, metrics.checker_invocations),
                             (1, 1, 1, 1, 0, 1))
            self.assertFalse(metrics.spend_complete)


class OnboardingAndSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_onboarding_modes_are_read_only_and_dual_install_is_conflict(self):
        fresh = inspect_project(self.root)
        self.assertEqual(fresh.mode, OnboardingMode.FRESH)
        self.assertFalse(fresh.activation_allowed)
        (self.root / ".devteam").mkdir()
        self.assertEqual(inspect_project(self.root).mode, OnboardingMode.DEVDEPARTMENT_SIDECAR)
        marker = self.root / ".codexdevteam" / "installation.json"
        marker.parent.mkdir()
        marker.write_text("{}", encoding="utf-8")
        conflict = inspect_project(self.root)
        self.assertEqual(conflict.mode, OnboardingMode.CONFLICT)
        self.assertFalse(conflict.activation_allowed)

    def test_sync_updates_only_clean_managed_files_and_preserves_sidecar(self):
        incumbent = self.root / ".devteam" / "scripts"
        incumbent.mkdir(parents=True)
        protected = incumbent / "supervisor.py"
        protected.write_text("DEVDEPARTMENT owner\n", encoding="utf-8")
        path = ".codexdevteam/framework/engine.py"
        destination = self.root / path
        destination.parent.mkdir(parents=True)
        old = b"old framework\n"
        destination.write_bytes(old)
        new = b"updated framework\n"
        plan = plan_framework_sync(self.root, {path: new},
                                   prior_hashes={path: hashlib.sha256(old).hexdigest()},
                                   mode=OnboardingMode.DEVDEPARTMENT_SIDECAR)
        self.assertEqual(plan.conflicts, ())
        self.assertEqual(apply_framework_sync(plan), (path,))
        self.assertEqual(destination.read_bytes(), new)
        self.assertEqual(protected.read_text(encoding="utf-8"), "DEVDEPARTMENT owner\n")

    def test_sync_conflicts_on_local_edits_and_refuses_project_state_paths(self):
        path = ".codexdevteam/framework/config.py"
        target = self.root / path
        target.parent.mkdir(parents=True)
        target.write_text("local edit", encoding="utf-8")
        plan = plan_framework_sync(self.root, {path: "new version"},
                                   prior_hashes={path: "0" * 64}, mode=OnboardingMode.CODEXDEVTEAM_UPGRADE)
        self.assertTrue(plan.conflicts)
        with self.assertRaises(SyncConflict):
            apply_framework_sync(plan)
        state_plan = plan_framework_sync(self.root,
                                         {".codexdevteam/project/PLAN.md": "project data"},
                                         mode=OnboardingMode.FRESH)
        self.assertTrue(state_plan.conflicts)
        malicious = SyncPlan(str(self.root), OnboardingMode.FRESH,
                             (SyncAction("../outside.txt", "create", None, b"bad"),), ())
        with self.assertRaises(SyncConflict):
            apply_framework_sync(malicious)

    def test_fresh_installer_is_atomic_inactive_and_refuses_incumbents(self):
        destination = install_fresh_project(
            self.root, {".codexdevteam/framework/README.md": "managed framework\n"})
        marker = json.loads((destination / "installation.json").read_text(encoding="utf-8"))
        self.assertIsNone(marker["active_head"])
        self.assertFalse(marker["activated"])
        self.assertTrue((destination / "framework" / "README.md").is_file())
        with self.assertRaisesRegex(InstallationConflict, "fresh mode"):
            install_fresh_project(self.root, {".codexdevteam/framework/next.txt": "x"})

    def test_fresh_installer_refuses_invalid_layout_without_leaving_staging(self):
        with self.assertRaisesRegex(SyncConflict, "paths below"):
            install_fresh_project(self.root, {"PLAN.md": "must not write"})
        self.assertFalse((self.root / ".codexdevteam").exists())
        self.assertEqual(list(self.root.glob(".codexdevteam-install-*")), [])

    def test_devdepartment_sidecar_installer_preserves_incumbent_and_remains_inactive(self):
        incumbent = self.root / ".devteam" / "scripts"
        incumbent.mkdir(parents=True)
        protected = incumbent / "supervisor.py"
        protected.write_text("incumbent supervisor", encoding="utf-8")
        destination = install_devdepartment_sidecar(
            self.root,
            {".codexdevteam/framework/sidecar/head_adapter.json": "{\"mode\":\"compat\"}"})
        marker = json.loads((destination / "installation.json").read_text(encoding="utf-8"))
        self.assertIsNone(marker["active_head"])
        self.assertFalse(marker["activated"])
        self.assertEqual(protected.read_text(encoding="utf-8"), "incumbent supervisor")
        inspection = inspect_project(self.root)
        self.assertEqual(inspection.mode, OnboardingMode.DEVDEPARTMENT_SIDECAR)
        self.assertIn("remains incumbent", inspection.findings[0])
        with self.assertRaisesRegex(InstallationConflict, "already exists"):
            install_devdepartment_sidecar(self.root,
                                          {".codexdevteam/framework/sidecar/extra.json": "{}"})

    def test_init_cli_installs_bundled_codex_candidate_without_activation(self):
        from codexdevteam_kernel.onboarding_cli import main
        output = io.StringIO()
        with patch("sys.argv", ["codexdevteam", "init", "--project", str(self.root)]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(), 0)
        marker = json.loads((self.root / ".codexdevteam" / "installation.json").read_text())
        registry = json.loads((self.root / ".codexdevteam" / "framework" /
                               "registry.template.json").read_text())
        from codexdevteam_kernel import TaskClassPolicy
        routing_path = (self.root / ".codexdevteam" / "framework" /
                        "task-routing.json")
        routing = TaskClassPolicy.from_file(routing_path)
        self.assertIsNone(marker["active_head"])
        self.assertFalse(marker["activated"])
        self.assertEqual(registry["defined"]["codex-head"]["runtime"], "codex")
        self.assertEqual(registry["active"], [])
        self.assertEqual(routing.floor_for("critical"), "frontier")
        self.assertIn("No HEAD lease was acquired", output.getvalue())

    def test_init_cli_installs_only_compatibility_sidecar_for_devdepartment(self):
        from codexdevteam_kernel.onboarding_cli import main
        incumbent = self.root / ".devteam" / "scripts"
        incumbent.mkdir(parents=True)
        supervisor = incumbent / "supervisor.py"
        supervisor.write_text("DEVDEPARTMENT", encoding="utf-8")
        with patch("sys.argv", ["codexdevteam", "init", "--project", str(self.root)]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        marker = json.loads((self.root / ".codexdevteam" / "installation.json").read_text())
        adapter = json.loads((self.root / ".codexdevteam" / "framework" / "sidecar" /
                              "compatibility.json").read_text())
        self.assertEqual(marker["integration_mode"], "devdepartment_sidecar")
        self.assertFalse(marker["activated"])
        self.assertEqual(adapter["incumbent_head"], "DEVDEPARTMENT")
        self.assertTrue(adapter["handover_required"])
        self.assertEqual(supervisor.read_text(encoding="utf-8"), "DEVDEPARTMENT")

    def test_upgrade_updates_hash_clean_framework_files_and_preserves_project_state(self):
        original = {".codexdevteam/framework/engine.json": '{"version":1}\n'}
        updated = {".codexdevteam/framework/engine.json": '{"version":2}\n',
                   ".codexdevteam/framework/new-default.json": '{"enabled":false}\n'}
        install_fresh_project(self.root, original)
        project_state = self.root / ".codexdevteam" / "project" / "PLAN.json"
        project_state.parent.mkdir()
        project_state.write_text('{"task":"keep"}\n', encoding="utf-8")
        marker_path = self.root / ".codexdevteam" / "installation.json"
        marker_before = marker_path.read_bytes()
        changed = upgrade_codexdevteam_project(self.root, updated)
        self.assertEqual(set(changed), set(updated) | {
            ".codexdevteam/framework/.managed-files.json"})
        self.assertEqual(json.loads((self.root / ".codexdevteam/framework/engine.json").read_text()),
                         {"version": 2})
        self.assertEqual(project_state.read_text(encoding="utf-8"), '{"task":"keep"}\n')
        self.assertEqual(marker_path.read_bytes(), marker_before)

    def test_upgrade_refuses_local_edit_without_partial_update(self):
        path = ".codexdevteam/framework/engine.json"
        install_fresh_project(self.root, {path: '{"version":1}\n'})
        target = self.root / path
        target.write_text('{"local":true}\n', encoding="utf-8")
        manifest_path = self.root / ".codexdevteam/framework/.managed-files.json"
        manifest_before = manifest_path.read_bytes()
        with self.assertRaisesRegex(SyncConflict, "local file differs"):
            upgrade_codexdevteam_project(self.root, {path: '{"version":2}\n'})
        self.assertEqual(target.read_text(encoding="utf-8"), '{"local":true}\n')
        self.assertEqual(manifest_path.read_bytes(), manifest_before)

    def test_sidecar_upgrade_preserves_devdepartment_owned_files(self):
        owned = ".codexdevteam/framework/sidecar/compat.json"
        incumbent = self.root / ".devteam/scripts/supervisor.py"
        incumbent.parent.mkdir(parents=True)
        incumbent.write_text("incumbent", encoding="utf-8")
        install_devdepartment_sidecar(self.root, {owned: '{"adapter":1}\n'})
        upgrade_codexdevteam_project(self.root, {owned: '{"adapter":2}\n'})
        self.assertEqual(json.loads((self.root / owned).read_text()), {"adapter": 2})
        self.assertEqual(incumbent.read_text(encoding="utf-8"), "incumbent")

    def test_shared_json_merge_keeps_disjoint_edits_and_reports_same_key_conflict(self):
        base = {"runtime": {"timeout": 60, "model": "model-a"}, "mode": "strict"}
        merged, conflicts = three_way_merge_json(
            base, {"runtime": {"timeout": 90, "model": "model-a"}, "mode": "strict"},
            {"runtime": {"timeout": 60, "model": "model-b"}, "mode": "strict"})
        self.assertEqual(conflicts, ())
        self.assertEqual(merged["runtime"], {"timeout": 90, "model": "model-b"})
        _, conflicts = three_way_merge_json(base,
                                            {"runtime": {"timeout": 90, "model": "model-a"}, "mode": "strict"},
                                            {"runtime": {"timeout": 120, "model": "model-a"}, "mode": "strict"})
        self.assertEqual(conflicts, ("runtime.timeout",))


class HealthPolicyTests(unittest.TestCase):
    def test_stagnation_thresholds_reset_on_progress_and_escalate_after_resets(self):
        sample = StagnationSample(False, 0)
        self.assertEqual(update_streak(2, sample), 3)
        self.assertTrue(is_stagnant(3, sample))
        self.assertEqual(update_streak(8, StagnationSample(True, 99)), 0)
        self.assertFalse(is_stagnant(0, StagnationSample(True, 99)))
        self.assertEqual(remedial_kind(0), "redispatch")
        self.assertEqual(remedial_kind(2), "escalate")

    def test_stale_signal_uses_aware_utc_timestamps(self):
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        fresh = stale_signal(now - timedelta(seconds=5), now=now, stale_after_seconds=10)
        stale = stale_signal(now - timedelta(seconds=11), now=now, stale_after_seconds=10)
        self.assertFalse(fresh.stale)
        self.assertTrue(stale.stale)


class ControlTests(unittest.TestCase):
    def task(self, worker="builder-1", state=TaskState.IN_PROGRESS):
        return TaskRecord("TASK-20", "Implement feature", state, worker, "high", ("src/**",))

    def test_control_message_round_trips_as_typed_data(self):
        message = ControlMessage.from_dict({
            "event_id": "report-1", "task_id": "TASK-20", "worker_id": "builder-1",
            "requested_state": "needs_review", "test_evidence": ["unit suite passed"],
            "head_sha": "a" * 40,
        })
        self.assertEqual(message.requested_state, TaskState.NEEDS_REVIEW)
        self.assertEqual(validate_control(message, self.task()).accepted, True)

    def test_builder_cannot_complete_or_report_for_another_worker(self):
        done = ControlMessage("report-2", "TASK-20", "builder-1", TaskState.DONE)
        wrong_worker = ControlMessage("report-3", "TASK-20", "builder-2")
        self.assertFalse(validate_control(done, self.task()).accepted)
        self.assertFalse(validate_control(wrong_worker, self.task()).accepted)

    def test_needs_review_requires_evidence_and_blocked_requires_reason(self):
        needs_review = ControlMessage("report-4", "TASK-20", "builder-1",
                                      TaskState.NEEDS_REVIEW, head_sha="a" * 40)
        blocked = ControlMessage("report-5", "TASK-20", "builder-1", TaskState.BLOCKED)
        self.assertIn("needs_review requires test evidence",
                      validate_control(needs_review, self.task()).findings)
        self.assertIn("blocked requires a reason",
                      validate_control(blocked, self.task()).findings)

    def test_unknown_transport_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown CONTROL fields"):
            ControlMessage.from_dict({"event_id": "x", "task_id": "TASK-1",
                                      "worker_id": "u", "role": "head"})

    def test_outbox_submission_is_typed_atomic_and_head_applied(self):
        from codexdevteam_kernel.control_queue import drain_control_outbox, submit_control
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outbox = root / "outbox"
            db = StateStore(root / "state.sqlite")
            lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
            task = self.task(state=TaskState.CLAIMED)
            db.seed_task(lease, task, event_id="seed-control-outbox", now=101)
            plan = root / "PLAN.md"
            plan.write_text(
                "### TASK-20\n**Title:** Implement feature\n**Status:** claimed\n"
                "**Assigned_To:** builder-1\n**Priority:** high\n**Owned_Paths:** src/**\n",
                encoding="utf-8",
            )
            path = submit_control(outbox, task_id=task.task_id, worker_id="builder-1",
                                  requested_state="in_progress", event_id="report-outbox")
            self.assertTrue(path.is_file())
            with self.assertRaisesRegex(ValueError, "already exists"):
                submit_control(outbox, task_id=task.task_id, worker_id="builder-1",
                               event_id="report-outbox")
            result = drain_control_outbox(db, lease, outbox, project_root=root, now=102)
            self.assertEqual(result, {"applied": ("report-outbox",), "rejected": ()})
            self.assertEqual(db.get_task(task.task_id).state, TaskState.IN_PROGRESS)
            self.assertIn("**Status:** in_progress", plan.read_text(encoding="utf-8"))
            self.assertTrue((outbox / "applied" / "report-outbox.json").is_file())

    def test_control_transition_queues_and_recovers_plan_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = StateStore(root / "state.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
            task = self.task(state=TaskState.CLAIMED)
            store.seed_task(lease, task, event_id="seed-control-plan", now=101)
            plan = root / "PLAN.md"
            plan.write_text(
                "### TASK-20\n**Title:** Implement feature\n**Status:** claimed\n"
                "**Assigned_To:** builder-1\n**Priority:** high\n**Owned_Paths:** src/**\n",
                encoding="utf-8",
            )
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            message = ControlMessage("control-plan", task.task_id, "builder-1",
                                     TaskState.IN_PROGRESS)
            with patch("codexdevteam_kernel.state._atomic_replace_plan",
                       side_effect=OSError("interrupted CONTROL projection")):
                with self.assertRaises(PlanProjectionPending):
                    store.apply_control(
                        lease, message, project_root=root,
                        expected_plan_sha256=expected, now=102,
                    )
            self.assertEqual(store.get_task(task.task_id).state, TaskState.IN_PROGRESS)
            self.assertEqual(store.pending_plan_projections()[0]["event_id"], "control-plan")
            self.assertEqual(store.apply_pending_plan_projections(lease, now=103),
                             ("control-plan",))
            self.assertIn("**Status:** in_progress", plan.read_text(encoding="utf-8"))
            self.assertEqual(store.pending_plan_projections(), ())

    def test_outbox_quarantines_invalid_report_without_applying_state(self):
        from codexdevteam_kernel.control_queue import drain_control_outbox, submit_control
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outbox = root / "outbox"
            db = StateStore(root / "state.sqlite")
            lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
            task = self.task(state=TaskState.CLAIMED)
            db.seed_task(lease, task, event_id="seed-control-invalid", now=101)
            submit_control(outbox, task_id=task.task_id, worker_id="intruder",
                           requested_state="in_progress", event_id="invalid-worker")
            result = drain_control_outbox(db, lease, outbox, now=102)
            self.assertEqual(result, {"applied": (), "rejected": ("invalid-worker",)})
            self.assertEqual(db.get_task(task.task_id).state, TaskState.CLAIMED)
            self.assertTrue((outbox / "rejected" / "invalid-worker.json").is_file())

    def test_expired_head_lease_leaves_control_report_pending(self):
        from codexdevteam_kernel.control_queue import drain_control_outbox, submit_control
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outbox = root / "outbox"
            db = StateStore(root / "state.sqlite")
            lease = db.acquire_head("CODEXDEVTEAM", "head", ttl_seconds=2, now=100)
            task = self.task(state=TaskState.CLAIMED)
            db.seed_task(lease, task, event_id="seed-control-expired", now=101)
            report = submit_control(outbox, task_id=task.task_id, worker_id="builder-1",
                                    requested_state="in_progress", event_id="pending-expired")
            with self.assertRaises(LeaseError):
                drain_control_outbox(db, lease, outbox, now=103)
            self.assertTrue(report.is_file())
            self.assertEqual(db.get_task(task.task_id).state, TaskState.CLAIMED)


class ReviewNormalizationTests(unittest.TestCase):
    def test_strict_json_verdict_uses_ledger_checker_identity(self):
        from codexdevteam_kernel.review import parse_review_verdict
        checker = WorkerIdentity("reviewer", "judgment", "high", "claude", "configured-reviewer")
        output = json.dumps({
            "task_id": "TASK-REVIEW", "sha": "a" * 40,
            "gate_fingerprint": "gate-fingerprint", "decision": "approved",
            "rationale": "The change meets its acceptance criteria.",
            "evidence_refs": ["gate:artifact-1", "diff:path/file.py"],
        })
        verdict = parse_review_verdict(output, checker)
        self.assertEqual(verdict.checker, checker)
        self.assertEqual(verdict.decision, "approved")
        self.assertEqual(verdict.evidence_refs, ("gate:artifact-1", "diff:path/file.py"))

    def test_strict_json_verdict_rejects_provider_attribution_and_malformed_output(self):
        from codexdevteam_kernel.review import parse_review_verdict
        checker = WorkerIdentity("reviewer", "judgment", "high", "claude", "configured-reviewer")
        payload = {"task_id": "TASK-REVIEW", "sha": "a" * 40,
                   "gate_fingerprint": "gate-fingerprint", "decision": "approved",
                   "rationale": "Looks good", "evidence_refs": [], "checker": "untrusted"}
        with self.assertRaisesRegex(ValueError, "exactly the required"):
            parse_review_verdict(json.dumps(payload), checker)
        payload.pop("checker")
        with self.assertRaisesRegex(ValueError, "single JSON object"):
            parse_review_verdict("```json\\n" + json.dumps(payload) + "\\n```", checker)
        payload["evidence_refs"] = ["same", "same"]
        with self.assertRaisesRegex(ValueError, "unique"):
            parse_review_verdict(json.dumps(payload), checker)


class FastTierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "state.sqlite")
        self.lease = self.store.acquire_head("CODEXDEVTEAM", "head", now=100)
        self.registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["fast-worker"], "head_candidate": None,
            "defined": {"fast-worker": {"role": "fast", "capability_floor": "standard",
                                         "runtime": "codex", "model": "configured-fast"}},
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_synthetic_redacted_log_corpus_covers_all_classifier_contract_categories(self):
        from codexdevteam_kernel.fast_tier import _parse_run_classification
        corpus_path = Path(__file__).parent / "fixtures" / "fast_tier_synthetic_corpus.jsonl"
        cases = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual({case["kind"] for case in cases},
                         {"ok", "capacity", "quota", "auth", "crash", "timeout"})
        for case in cases:
            output = {"kind": case["kind"], "evidence_line": case["evidence_line"]}
            if "reset_at" in case:
                output["reset_at"] = case["reset_at"]
            parsed = _parse_run_classification(json.dumps(output), case["log"])
            self.assertIsNotNone(parsed, case["case"])
            self.assertEqual(parsed["kind"], case["kind"])
            self.assertIsNone(_parse_run_classification(json.dumps({
                **output, "evidence_line": "invented evidence"}), case["log"]))

    def test_corpus_evaluator_reports_coverage_kind_and_evidence_accuracy(self):
        from codexdevteam_kernel.fast_tier_eval import evaluate_run_log_corpus
        cases = [
            {"case": "healthy", "kind": "ok", "log": "ready\ndone",
             "evidence_line": "ready"},
            {"case": "quota", "kind": "quota", "log": "quota hit\nretry later",
             "evidence_line": "quota hit", "reset_at": "2026-10-02T13:00:00Z"},
            {"case": "auth", "kind": "auth", "log": "login required",
             "evidence_line": "login required"},
            {"case": "crash", "kind": "crash", "log": "worker exited",
             "evidence_line": "worker exited"},
        ]
        predictions = [
            {"case": "healthy", "kind": "ok", "evidence_line": "ready"},
            {"case": "quota", "kind": "quota", "evidence_line": "retry later",
             "reset_at": "2026-10-02T14:00:00Z"},
            {"case": "auth", "kind": "crash", "evidence_line": "login required"},
        ]
        result = evaluate_run_log_corpus(cases, predictions)
        self.assertEqual(result["case_count"], 4)
        self.assertEqual(result["coverage"], 0.75)
        self.assertEqual(result["kind_accuracy"], 0.5)
        self.assertEqual(result["evidence_accuracy"], 0.5)
        self.assertEqual(result["reset_at_case_count"], 1)
        self.assertEqual(result["reset_at_accuracy"], 0.0)
        self.assertEqual(result["exact_accuracy"], 0.25)
        self.assertEqual(result["selective_exact_accuracy"], 1 / 3)
        self.assertEqual(result["enablement_decision"], "not_provided")

    def test_corpus_evaluator_rejects_duplicate_unknown_and_secret_cases(self):
        from codexdevteam_kernel.fast_tier_eval import evaluate_run_log_corpus
        case = {"case": "healthy", "kind": "ok", "log": "ready",
                "evidence_line": "ready"}
        with self.assertRaisesRegex(ValueError, "unique"):
            evaluate_run_log_corpus([case, case], [])
        with self.assertRaisesRegex(ValueError, "unknown"):
            evaluate_run_log_corpus([case], [{"case": "missing", "kind": None,
                                              "evidence_line": None}])
        with self.assertRaisesRegex(ValueError, "redacted"):
            evaluate_run_log_corpus([{**case, "log": "ghp_123456789012345678901234567890123456"}], [])

    class Adapter:
        def __init__(self, outputs):
            self.outputs = list(outputs)
            self.requests = []

        def invoke(self, request):
            self.requests.append(request)
            output = self.outputs.pop(0)
            return InvocationResult(
                request.invocation_id, None, "triage", request.identity.unit_id,
                request.identity.runtime, request.identity.model, "succeeded", 0, 0.1,
                output, "", False, hashlib.sha256(output.encode()).hexdigest(),
                101.0, 101.1, role=request.identity.role,
            )

    def test_classifier_retries_invalid_schema_once_and_returns_evidence_bound_result(self):
        from codexdevteam_kernel import FastTierRunner
        run_log = "ERROR at 14:02: token budget exhausted"
        valid = json.dumps({"kind": "quota", "reset_at": "2026-10-01T14:05:00Z",
                            "evidence_line": "ERROR at 14:02: token budget exhausted"})
        adapter = self.Adapter(["not JSON", valid])
        result = FastTierRunner(self.store, self.registry).classify_run_log(
            self.lease, job_id="log-check-1", worker_id="fast-worker", run_log=run_log,
            adapters={"codex": adapter}, working_directory=self.root, now=102)
        self.assertFalse(result.escalated)
        self.assertEqual(result.classification["kind"], "quota")
        self.assertEqual(len(result.invocations), 2)
        self.assertTrue(all(not request.writable for request in adapter.requests))
        self.assertEqual(sum(event["payload"].get("type") == "tier.classified"
                             for event in self.store.events()), 1)
        self.assertFalse(any(event["payload"].get("type") == "tier.escalated"
                             for event in self.store.events()))

    def test_second_invalid_output_emits_one_idempotent_escalation(self):
        from codexdevteam_kernel import FastTierRunner
        runner = FastTierRunner(self.store, self.registry)
        adapter = self.Adapter(['{"kind":"quota","evidence_line":"not in log"}'] * 4)
        for _ in range(2):
            result = runner.classify_run_log(
                self.lease, job_id="log-check-2", worker_id="fast-worker",
                run_log="ERROR: quota reached", adapters={"codex": adapter},
                working_directory=self.root, now=102)
            self.assertTrue(result.escalated)
            self.assertIsNone(result.classification)
        events = [event for event in self.store.events()
                  if event["payload"].get("type") == "tier.escalated"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["attempts"], 2)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Kernel Test")
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("base = True\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-b", "task/TASK-40")
        (self.root / "src" / "a.py").write_text("change = True\n", encoding="utf-8")
        self.git("commit", "-am", "task change")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.artifacts = Path(self.temp.name) / "gate-artifacts"
        self.task = TaskRecord("TASK-40", "Gate fixture", TaskState.NEEDS_REVIEW,
                               "worker-a", "medium", ("src/**",))
        class FixtureGateRunner(GateRunner):
            def run(inner_self, task, worktree, **kwargs):
                kwargs.setdefault("active_tasks", (task,))
                return super(FixtureGateRunner, inner_self).run(task, worktree, **kwargs)

        self.runner = FixtureGateRunner(self.root, self.artifacts)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout

    def passing_commands(self, build=None):
        passing = [sys.executable, "-c", "pass"]
        return {"build": build or passing, "typecheck": passing, "test_full": passing}

    def checker_receipt(self, checker, gate):
        return InvocationResult("review-run-1", self.task.task_id, "checker",
                                checker.unit_id, checker.runtime, checker.model,
                                "succeeded", 0, 1.0, "approved", "", False,
                                "a" * 64, 101.65, 101.9, self.sha, gate.fingerprint)

    def test_gate_binds_result_to_sha_scrubs_environment_and_caches(self):
        commands = self.passing_commands([sys.executable, "-c",
                                          "import os; assert 'CODEXDEVTEAM_PRIVATE_TEST_VALUE' not in os.environ"])
        with patch.dict(os.environ, {"CODEXDEVTEAM_PRIVATE_TEST_VALUE": "must-not-leak"}):
            result = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                     commands=commands)
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.checks["test_full"].status, "passed")
        self.assertIsNotNone(result.test_run_result)
        self.assertTrue(result.test_run_result.passed)
        cached = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                 commands=commands)
        self.assertTrue(cached.cached)
        self.assertEqual(cached.test_run_result.evidence_ref,
                         result.test_run_result.evidence_ref)

    def test_head_gate_registration_registers_test_receipt_before_control_review(self):
        from dataclasses import replace
        from codexdevteam_kernel.supervisor import Supervisor

        task = replace(self.task, state=TaskState.IN_PROGRESS)
        store = StateStore(Path(self.temp.name) / "head-gate.sqlite")
        lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
        store.seed_task(lease, task, event_id="seed-head-gate", now=100.5)
        gate = self.runner.run(task, self.root, expected_sha=self.sha, base_ref=self.base,
                               commands=self.passing_commands(), refresh=True)
        supervisor = Supervisor(store, WorkerRegistry.from_dict({
            "protocol_version": 1, "active": [], "head_candidate": None, "defined": {}}))
        supervisor.record_gate_outcome(lease, gate, attempt_event_id="head-gate", now=101)
        message = ControlMessage("gate-backed-review", task.task_id, "worker-a",
                                 TaskState.NEEDS_REVIEW,
                                 test_evidence=(gate.test_run_result.evidence_ref,),
                                 head_sha=self.sha)
        self.assertTrue(store.apply_control(lease, message, now=102))
        self.assertEqual(store.get_task(task.task_id).state, TaskState.NEEDS_REVIEW)

    def test_failed_gate_test_run_is_not_registered_as_passed_evidence(self):
        from dataclasses import replace
        from codexdevteam_kernel.supervisor import Supervisor

        task = replace(self.task, state=TaskState.IN_PROGRESS)
        store = StateStore(Path(self.temp.name) / "failed-head-gate.sqlite")
        lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
        store.seed_task(lease, task, event_id="seed-failed-head-gate", now=100.5)
        commands = self.passing_commands()
        commands["test_full"] = [sys.executable, "-c", "raise SystemExit(1)"]
        gate = self.runner.run(task, self.root, expected_sha=self.sha, base_ref=self.base,
                               commands=commands, refresh=True)
        self.assertEqual(gate.status, "failed")
        self.assertFalse(gate.test_run_result.passed)
        supervisor = Supervisor(store, WorkerRegistry.from_dict({
            "protocol_version": 1, "active": [], "head_candidate": None, "defined": {}}))
        self.assertIsNone(supervisor.record_gate_outcome(
            lease, gate, attempt_event_id="failed-test-gate", now=101))
        self.assertFalse(any(event["payload"].get("type") == "test_run.registered"
                             for event in store.events()))

    def test_gate_records_passing_baseline_checks(self):
        result = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                 commands=self.passing_commands())
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.checks["baseline_analysis"].status, "passed")
        self.assertEqual(result.checks["base_build"].status, "passed")

    def test_per_worktree_test_environment_is_scoped_scrubbed_and_fingerprinted(self):
        command = [sys.executable, "-c",
                   "import os; scope=os.environ['CODEXDEVTEAM_RUN_SCOPE']; "
                   "assert os.environ['TEST_DATABASE_URL'] == "
                   "'postgres://test/TASK-40-worker-a-' + scope"]
        templates = {"TEST_DATABASE_URL": "postgres://test/{task_id}-{unit}-{run_scope}"}
        result = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                 commands=self.passing_commands(command),
                                 per_worktree_environment=templates)
        self.assertEqual(result.status, "passed")
        self.assertNotIn("postgres://test/TASK-40-worker-a-task", " ".join(
            check.summary for check in result.checks.values()))
        changed = self.runner.run(
            self.task, self.root, expected_sha=self.sha, base_ref=self.base,
            commands=self.passing_commands(command),
            per_worktree_environment={"TEST_DATABASE_URL":
                                      "postgres://isolated/{task_id}-{unit}-{run_scope}"})
        self.assertNotEqual(result.fingerprint, changed.fingerprint)

    def test_standalone_and_gate_share_sha_command_environment_test_run_cache(self):
        counter = Path(self.temp.name) / "test-run-count.txt"
        source = ("import os; from pathlib import Path; "
                  f"p=Path({str(counter)!r}); "
                  "assert os.environ['TEST_DATABASE_URL'].endswith('-' + "
                  "os.environ['CODEXDEVTEAM_RUN_SCOPE']); "
                  "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')")
        command = [sys.executable, "-c", source]
        templates = {"TEST_DATABASE_URL": "postgres://test/{task_id}-{worker_id}-{run_scope}"}
        prepared = self.runner.run_test_command(
            self.task, command, worktree=self.root, expected_sha=self.sha,
            per_worktree_environment=templates)
        self.assertTrue(prepared.passed)
        self.assertFalse(prepared.cached)
        reused = self.runner.run_test_command(
            self.task, command, worktree=self.root, expected_sha=self.sha,
            per_worktree_environment=templates)
        self.assertTrue(reused.cached)
        commands = self.passing_commands()
        commands["test_full"] = command
        gate = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                               commands=commands, per_worktree_environment=templates,
                               refresh=True)
        self.assertEqual(gate.status, "passed")
        self.assertTrue(self.runner.run_test_command(
            self.task, command, worktree=self.root, expected_sha=self.sha,
            per_worktree_environment=templates).cached)
        # One standalone execution plus one isolated baseline execution.
        self.assertEqual(counter.read_text(encoding="utf-8"), "2")

    def test_primary_and_linked_worktree_share_test_run_cache(self):
        linked = Path(self.temp.name) / "linked-worktree"
        added = subprocess.run(["git", "-C", str(self.root), "worktree", "add",
                                "--detach", str(linked), self.sha], capture_output=True,
                               text=True, check=False)
        self.assertEqual(added.returncode, 0, added.stderr)
        try:
            primary_runner = GateRunner(self.root)
            linked_runner = GateRunner(linked)
            self.assertEqual(primary_runner.test_run_cache.root,
                             linked_runner.test_run_cache.root)
            command = [sys.executable, "-c", "pass"]
            first = linked_runner.run_test_command(
                self.task, command, worktree=linked, expected_sha=self.sha)
            second = primary_runner.run_test_command(
                self.task, command, worktree=linked, expected_sha=self.sha)
            self.assertFalse(first.cached)
            self.assertTrue(second.cached)
        finally:
            subprocess.run(["git", "-C", str(self.root), "worktree", "remove",
                            str(linked)], capture_output=True, check=False)

    def test_test_run_cache_rejects_wrong_sha_and_dirty_worktree(self):
        cache = TestRunCache(self.artifacts.parent / "standalone-tests")
        with self.assertRaisesRegex(TestRunError, "expected worktree SHA"):
            cache.run("full", [sys.executable, "-c", "pass"], worktree=self.root,
                      expected_sha="0" * 40, environment={}, timeout_seconds=10)
        (self.root / "dirty-after-test-run-check").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(TestRunError, "clean worktree"):
            cache.run("full", [sys.executable, "-c", "pass"], worktree=self.root,
                      expected_sha=self.sha, environment={}, timeout_seconds=10)

    def test_test_run_artifact_fingerprints_argv_without_persisting_it(self):
        cache = TestRunCache(self.artifacts.parent / "redacted-tests")
        secret = "synthetic-token-do-not-persist-91f4a7"
        result = cache.run("full", [sys.executable, "-c", "pass", secret],
                           worktree=self.root, expected_sha=self.sha,
                           environment={}, timeout_seconds=10)
        artifact = Path(result.artifact_path).read_text(encoding="utf-8")
        self.assertNotIn(secret, artifact)
        self.assertNotIn('"command"', artifact)
        self.assertEqual(json.loads(artifact)["command_sha256"],
                         TestRunCache.command_fingerprint(result.command))

    def test_test_run_log_redacts_values_echoed_from_argv(self):
        cache = TestRunCache(self.artifacts.parent / "argv-log-redaction")
        secret = "synthetic-config-secret-730be1"
        result = cache.run("full", [sys.executable, "-c",
                                     "import sys; print(sys.argv[-1])", secret],
                           worktree=self.root, expected_sha=self.sha,
                           environment={}, timeout_seconds=10)
        self.assertTrue(result.passed)
        log = Path(result.log_path).read_text(encoding="utf-8")
        self.assertNotIn(secret, log)
        self.assertIn("[REDACTED_VALUE]", log)

    def test_gate_log_redacts_values_echoed_from_configured_argv(self):
        secret = "synthetic-gate-argv-secret-99cc"
        command = [sys.executable, "-c",
                   "import sys; print(sys.argv[-1])", secret]
        result = self.runner.run(self.task, self.root, expected_sha=self.sha,
                                 base_ref=self.base,
                                 commands=self.passing_commands(command))
        log = Path(result.checks["build"].log_path).read_text(encoding="utf-8")
        self.assertNotIn(secret, log)
        self.assertIn("[REDACTED_VALUE]", log)

    def test_builder_test_cli_returns_safe_sha_bound_evidence_reference(self):
        from codexdevteam_kernel.test_cli import main

        config = Path(self.temp.name) / "gate-config.json"
        config.write_text(json.dumps({"version": 1, "tests": {"test_full": {
            "argv": [sys.executable, "-c", "pass"]
        }}}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--config", str(config), "--name", "test_full",
                         "--task", "TASK-CLI-1", "--worker", "worker-a",
                         "--worktree", str(self.root), "--sha", self.sha])
        response = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(response["status"], "passed")
        self.assertEqual(response["sha"], self.sha)
        self.assertTrue(response["evidence_ref"].startswith(f"testrun:{self.sha}:test_full:"))

    def test_control_needs_review_requires_registered_test_receipt_for_exact_sha(self):
        task = self.task.__class__(self.task.task_id, self.task.title, TaskState.IN_PROGRESS,
                                   self.task.assigned_worker, self.task.priority,
                                   self.task.owned_paths)
        db = StateStore(Path(self.temp.name) / "control-evidence.sqlite")
        lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
        db.seed_task(lease, task, event_id="seed-control-evidence", now=101)
        missing = ControlMessage("control-needs-review", task.task_id, "worker-a",
                                 TaskState.NEEDS_REVIEW, test_evidence=("tests passed",),
                                 head_sha=self.sha)
        with self.assertRaisesRegex(LeaseError, "registered passed test run"):
            db.apply_control(lease, missing, now=102)
        result = self.runner.run_test_command(
            task, [sys.executable, "-c", "pass"], worktree=self.root,
            expected_sha=self.sha)
        evidence_ref = db.register_test_run(lease, task.task_id, result,
                                            event_id="register-control-test-run", now=102.5)
        report = ControlMessage("control-needs-review", task.task_id, "worker-a",
                                TaskState.NEEDS_REVIEW, test_evidence=(evidence_ref,),
                                head_sha=self.sha)
        self.assertTrue(db.apply_control(lease, report, now=103))
        self.assertEqual(db.get_task(task.task_id).state, TaskState.NEEDS_REVIEW)
        self.assertEqual(db.get_task(task.task_id).test_evidence, (evidence_ref,))
        self.assertFalse(db.apply_control(lease, report, now=104))

    def test_concurrent_same_key_test_runs_execute_once(self):
        counter = Path(self.temp.name) / "concurrent-run-count.txt"
        code = ("import time; from pathlib import Path; "
                f"p=Path({str(counter)!r}); time.sleep(0.2); "
                "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')")
        cache = TestRunCache(self.artifacts.parent / "concurrent-tests")
        def run():
            return cache.run("full", [sys.executable, "-c", code], worktree=self.root,
                             expected_sha=self.sha, environment={}, timeout_seconds=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run)
            second = pool.submit(run)
            results = (first.result(), second.result())
        self.assertEqual(sum(not result.cached for result in results), 1)
        self.assertEqual(counter.read_text(encoding="utf-8"), "1")

    def test_task_only_failure_is_classified_as_new(self):
        command = [sys.executable, "-c",
                   "assert open('src/a.py', encoding='utf-8').read().strip() == 'base = True'"]
        result = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                 commands=self.passing_commands(command))
        self.assertEqual(result.checks["base_build"].status, "passed")
        self.assertEqual(result.new_failures, ("build",))
        self.assertEqual(result.status, "failed")

    def test_inherited_failure_requires_an_open_owner(self):
        command = [sys.executable, "-c", "raise RuntimeError('stable baseline failure')"]
        result = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                 commands=self.passing_commands(command), refresh=True)
        self.assertEqual(result.base_failures, ("build",))
        self.assertEqual(result.inherited_failures, ("build",))
        self.assertEqual(result.status, "failed")

    def test_open_owner_can_accept_inherited_failure_through_review_evidence(self):
        from dataclasses import replace

        task = replace(self.task, owns_failures=("TASK-99",),
                       maker_identity={"unit_id": "maker", "runtime": "codex",
                                       "model": "maker-model"})
        command = [sys.executable, "-c", "raise RuntimeError('stable baseline failure')"]
        result = self.runner.run(task, self.root, expected_sha=self.sha, base_ref=self.base,
                                 commands=self.passing_commands(command),
                                 open_task_ids=("TASK-99",), refresh=True)
        self.assertEqual(result.inherited_failures, ("build",))
        self.assertEqual(result.open_failure_owner_ids, ("TASK-99",))
        self.assertEqual(result.status, "passed")
        payload = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
        self.assertEqual(gate_artifact_findings(payload), ())
        checker = WorkerIdentity("checker", "reviewer", "high", "other", "checker-model")
        verdict = ReviewVerdict(task.task_id, self.sha, result.fingerprint, checker,
                                "approved", "inherited failure has an active owner")
        self.assertEqual(validate_review(task, result, verdict), ())
        db = StateStore(Path(self.temp.name) / "owned-review.sqlite")
        lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
        owner = TaskRecord("TASK-99", "Repair inherited failure", TaskState.IN_PROGRESS,
                           "repair-worker", "high", ("src/**",))
        db.seed_task(lease, owner, event_id="seed-owner", now=101)
        db.seed_task(lease, task, event_id="seed-owned-review", now=101.2)
        db.record_gate_result(lease, result, now=101.5)
        db.set_supervisor_mode(lease, "running", event_id="review-mode", now=101.55)
        db.start_checker_invocation(
            lease, task.task_id,
            {"unit_id": checker.unit_id, "runtime": checker.runtime, "model": checker.model},
            sha=self.sha, gate_fingerprint=result.fingerprint,
            invocation_id="review-run-1", now=101.6)
        db.record_invocation(lease, self.checker_receipt(checker, result), now=101.7)
        self.assertTrue(db.apply_review(lease, verdict, result,
                                        checker_invocation_id="review-run-1",
                                        event_id="owned-review-approved", now=102))

    def test_dirty_worktree_bypasses_cached_result_and_fails(self):
        commands = self.passing_commands()
        self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base, commands=commands)
        (self.root / "untracked.tmp").write_text("dirty\n", encoding="utf-8")
        result = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base, commands=commands)
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.cached)
        self.assertEqual(result.checks["clean_worktree"].status, "failed")

    def test_out_of_territory_commit_fails_before_commands(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "outside.md").write_text("outside\n", encoding="utf-8")
        self.git("add", "docs/outside.md")
        self.git("commit", "-m", "outside task territory")
        sha = self.git("rev-parse", "HEAD").strip()
        result = self.runner.run(self.task, self.root, expected_sha=sha, base_ref=self.base,
                                 commands=self.passing_commands(
                                     [sys.executable, "-c", "raise SystemExit(99)"]))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.checks["territory"].status, "failed")

    def test_post_run_gate_rejects_path_reserved_by_another_active_task(self):
        other = TaskRecord("TASK-41", "Concurrent owner", TaskState.IN_PROGRESS,
                           "worker-b", "high", ("src/a.py",))
        result = self.runner.run(
            self.task, self.root, expected_sha=self.sha, base_ref=self.base,
            commands=self.passing_commands(), active_tasks=(self.task, other),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.checks["territory"].status, "failed")
        self.assertIn("reserved by active task TASK-41",
                      result.checks["territory"].summary)
        self.assertEqual(result.checks["build"].status, "skipped")

    def test_post_run_gate_checks_both_sides_of_cross_territory_rename(self):
        (self.root / "dst").mkdir()
        self.git("mv", "src/a.py", "dst/a.py")
        self.git("commit", "-m", "move another task's file")
        sha = self.git("rev-parse", "HEAD").strip()
        task = TaskRecord(self.task.task_id, self.task.title, TaskState.NEEDS_REVIEW,
                          self.task.assigned_worker, self.task.priority, ("dst/**",))
        other = TaskRecord("TASK-42", "Original file owner", TaskState.IN_PROGRESS,
                           "worker-b", "high", ("src/**",))
        result = self.runner.run(
            task, self.root, expected_sha=sha, base_ref=self.base,
            commands=self.passing_commands(), active_tasks=(task, other),
        )
        self.assertEqual(result.checks["territory"].status, "failed")
        self.assertIn("src/a.py", result.checks["territory"].summary)
        self.assertNotIn("dst/a.py", result.checks["territory"].summary)

    def test_post_run_gate_requires_authoritative_active_task_snapshot(self):
        runner = GateRunner(self.root, self.artifacts)
        with self.assertRaisesRegex(ValueError, "active_tasks"):
            runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                       commands=self.passing_commands())

    def test_gate_cache_is_invalidated_when_active_territory_snapshot_changes(self):
        first = self.runner.run(
            self.task, self.root, expected_sha=self.sha, base_ref=self.base,
            commands=self.passing_commands(),
        )
        other = TaskRecord("TASK-43", "Independent active task", TaskState.IN_PROGRESS,
                           "worker-b", "medium", ("docs/**",))
        second = self.runner.run(
            self.task, self.root, expected_sha=self.sha, base_ref=self.base,
            commands=self.passing_commands(), active_tasks=(self.task, other),
        )
        self.assertEqual(first.status, "passed")
        self.assertFalse(second.cached)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_gate_protects_framework_state_unless_task_has_explicit_grant(self):
        path = ".codexdevteam/project/receipt.json"
        target = self.root / path
        target.parent.mkdir(parents=True)
        target.write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", path], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "add project state"],
                       check=True, capture_output=True)
        sha = self.git("rev-parse", "HEAD").strip()
        task = TaskRecord(self.task.task_id, self.task.title, TaskState.NEEDS_REVIEW,
                          self.task.assigned_worker, self.task.priority,
                          (".codexdevteam/project/**", "src/a.py"))
        denied = self.runner.run(task, self.root, expected_sha=sha, base_ref=self.base,
                                 commands=self.passing_commands(), refresh=True)
        self.assertEqual(denied.checks["territory"].status, "failed")
        granted = TaskRecord(self.task.task_id, self.task.title, TaskState.NEEDS_REVIEW,
                             self.task.assigned_worker, self.task.priority,
                             (".codexdevteam/project/**", "src/a.py"),
                             protected_grants=(path,))
        allowed = self.runner.run(granted, self.root, expected_sha=sha, base_ref=self.base,
                                  commands=self.passing_commands(), refresh=True)
        self.assertEqual(allowed.checks["territory"].status, "passed",
                         allowed.checks["territory"].summary)

        (self.root / "PLAN.md").write_text("# Supervisor-owned plan\n", encoding="utf-8")
        self.git("add", "PLAN.md")
        self.git("commit", "-m", "add supervisor plan")
        plan_sha = self.git("rev-parse", "HEAD").strip()
        plan_task = TaskRecord(self.task.task_id, self.task.title, TaskState.NEEDS_REVIEW,
                               self.task.assigned_worker, self.task.priority,
                               ("**",), protected_grants=("PLAN.md",))
        plan_result = self.runner.run(plan_task, self.root, expected_sha=plan_sha,
                                      base_ref=self.base,
                                      commands=self.passing_commands(), refresh=True)
        self.assertEqual(plan_result.checks["territory"].status, "failed")
        self.assertIn("controlled through HEAD", plan_result.checks["territory"].summary)

    def test_gate_rejects_changed_symlink_that_resolves_outside_worktree(self):
        target = Path(self.temp.name) / "outside-target"
        target.write_text("outside\n", encoding="utf-8")
        link = self.root / "src" / "escape"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        subprocess.run(["git", "-C", str(self.root), "add", "src/escape"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "add escaping symlink"],
                       check=True, capture_output=True)
        sha = self.git("rev-parse", "HEAD").strip()
        result = self.runner.run(self.task, self.root, expected_sha=sha, base_ref=self.base,
                                 commands=self.passing_commands(), refresh=True)
        self.assertEqual(result.checks["territory"].status, "failed")
        self.assertIn("resolves outside", result.checks["territory"].summary)
        self.assertEqual(result.checks["build"].status, "skipped")

    def test_gate_with_no_configured_mechanical_command_fails(self):
        result = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                 commands={"build": None, "typecheck": None, "test_full": None})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.checks["build"].status, "skipped")
        self.assertEqual(result.checks["configuration"].status, "failed")

    def test_gate_rejects_unrecognized_check_names(self):
        with self.assertRaisesRegex(ValueError, "unknown mechanical checks"):
            self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                            commands={"lint": [sys.executable, "-c", "pass"]})

    def test_missing_core_checks_fail_and_optional_checks_are_opt_in(self):
        missing = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                  commands={"build": [sys.executable, "-c", "pass"]})
        self.assertEqual(missing.status, "failed")
        optional = self.passing_commands()
        optional["reachability"] = [sys.executable, "-c", "raise SystemExit(2)"]
        failed_optional = self.runner.run(self.task, self.root, expected_sha=self.sha,
                                          base_ref=self.base, commands=optional, refresh=True)
        self.assertEqual(failed_optional.status, "failed")

    def test_secret_scan_blocks_credential_pattern_without_echoing_value(self):
        synthetic = "sk-" + "A" * 24
        secret_file = self.root / "src" / "credential_fixture.py"
        secret_file.write_text(f'credential = "{synthetic}"\n', encoding="utf-8")
        self.git("add", "src/credential_fixture.py")
        self.git("commit", "-m", "add credential fixture")
        sha = self.git("rev-parse", "HEAD").strip()
        result = self.runner.run(self.task, self.root, expected_sha=sha, base_ref=self.base,
                                 commands=self.passing_commands())
        self.assertEqual(result.status, "failed")
        self.assertIn("secret_scan", result.checks)
        self.assertIn("credential patterns found", result.checks["secret_scan"].summary)
        self.assertNotIn(synthetic, result.checks["secret_scan"].summary)

    def test_atomic_review_completion_requires_distinct_checker_and_passed_artifact(self):
        from dataclasses import replace

        maker = {"unit_id": "maker", "runtime": "codex", "model": "maker-model"}
        task = replace(self.task, maker_identity=maker)
        gate = self.runner.run(task, self.root, expected_sha=self.sha, base_ref=self.base,
                               commands=self.passing_commands())
        checker = WorkerIdentity("checker", "reviewer", "high", "other-runtime", "checker-model")
        verdict = ReviewVerdict(task.task_id, self.sha, gate.fingerprint, checker,
                                "approved", "Mechanical evidence reviewed")
        self.assertEqual(validate_review(task, gate, verdict), ())
        db = StateStore(Path(self.temp.name) / "review.sqlite")
        lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
        db.seed_task(lease, task, event_id="seed-review", now=101)
        from codexdevteam_kernel.supervisor import Supervisor
        outcome_supervisor = Supervisor(db, WorkerRegistry.from_dict({
            "protocol_version": 1, "active": [], "head_candidate": None, "defined": {}}))
        outcome_supervisor.record_gate_outcome(lease, gate, attempt_event_id="passed-gate",
                                               now=101.5)
        self.assertEqual(len(db.verified_gate_attempt_events()), 1)
        db.set_supervisor_mode(lease, "running", event_id="review-mode", now=101.55)
        with self.assertRaisesRegex(LeaseError, "does not match its HEAD-reserved identity"):
            db.record_invocation(lease, self.checker_receipt(checker, gate), now=101.58)
        db.start_checker_invocation(
            lease, task.task_id,
            {"unit_id": checker.unit_id, "runtime": checker.runtime, "model": checker.model},
            sha=self.sha, gate_fingerprint=gate.fingerprint,
            invocation_id="review-run-1", now=101.6)
        early = replace(self.checker_receipt(checker, gate), started_at=101.59,
                        finished_at=101.61)
        with self.assertRaisesRegex(LeaseError, "start time"):
            db.record_invocation(lease, early, now=101.62)
        forged = replace(self.checker_receipt(checker, gate), unit_id="substituted-checker")
        with self.assertRaisesRegex(LeaseError, "does not match its HEAD-reserved identity"):
            db.record_invocation(lease, forged, now=101.65)
        db.record_invocation(lease, self.checker_receipt(checker, gate), now=101.7)
        def apply_review():
            return db.apply_review(lease, verdict, gate, checker_invocation_id="review-run-1",
                                   event_id="review-approved", now=102)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: apply_review(), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual([item["event_id"] for item in db.verified_review_events()],
                         ["review-approved"])
        self.assertEqual(db.get_task(task.task_id).state, TaskState.DONE)
        self.assertFalse(db.apply_review(lease, verdict, gate, checker_invocation_id="review-run-1",
                                         event_id="review-approved", now=103))
        Path(gate.artifact_path).write_text("tampered", encoding="utf-8")
        self.assertEqual(db.verified_review_events(), [])

    def test_failed_gate_attempts_are_head_recorded_and_artifact_verified(self):
        from codexdevteam_kernel.miners import mine_gate_history_facts

        db = StateStore(Path(self.temp.name) / "gate-attempts.sqlite")
        lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
        db.seed_task(lease, self.task, event_id="seed-gate-attempts", now=100.5)
        from codexdevteam_kernel.supervisor import Supervisor
        supervisor = Supervisor(db, WorkerRegistry.from_dict({
            "protocol_version": 1, "active": [], "head_candidate": None, "defined": {}}))
        scripts = (
            "from pathlib import Path; raise SystemExit(1 if 'change' in Path('src/a.py').read_text() else 0)",
            "from pathlib import Path;raise SystemExit(1 if 'change' in Path('src/a.py').read_text() else 0)",
        )
        gates = []
        for index, script in enumerate(scripts):
            commands = self.passing_commands()
            commands["build"] = [sys.executable, "-c", script]
            gate = self.runner.run(self.task, self.root, expected_sha=self.sha, base_ref=self.base,
                                   commands=commands, refresh=True)
            self.assertEqual(gate.status, "failed")
            self.assertIn("build", gate.new_failures)
            gates.append(gate)
            self.assertIsNone(supervisor.record_gate_outcome(
                lease, gate, attempt_event_id=f"failed-gate-{index}", now=101 + index))
        verified = db.verified_gate_attempt_events()
        self.assertEqual(len(verified), 2)
        memory = EvidenceMemory(Path(self.temp.name) / "gate-memory.sqlite")
        facts = mine_gate_history_facts(memory, db, project="project-a", now=110)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].subject, "gate-check:build")
        Path(gates[0].artifact_path).write_text("tampered", encoding="utf-8")
        self.assertEqual(len(db.verified_gate_attempt_events()), 1)

    def test_supervisor_launches_configured_mixed_runtime_checker_once_after_gate(self):
        from dataclasses import replace
        from codexdevteam_kernel.supervisor import Supervisor, SupervisorPolicy

        maker = {"unit_id": "maker", "runtime": "codex", "model": "maker-model"}
        task = replace(self.task, assigned_worker="maker", maker_identity=maker)
        gate = self.runner.run(task, self.root, expected_sha=self.sha, base_ref=self.base,
                               commands=self.passing_commands())
        db = StateStore(Path(self.temp.name) / "supervised-checker.sqlite")
        lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
        db.seed_task(lease, task, event_id="seed-supervised-checker", now=100.5)
        memory = EvidenceMemory(Path(self.temp.name) / "review-memory.sqlite")
        memory.add_fact(fact_id="fact-review-test", kind="review_catch", scope="project",
                        subject="src/a.py", statement="Check the changed implementation boundary.",
                        evidence=("review:prior",), origin_project="fixture", now=100.6)
        injection = memory.retrieve_for_task(
            event_id="review-memory-injection", task_id=task.task_id, project="fixture",
            owned_paths=task.owned_paths, now=100.7)
        db.record_invocation(lease, InvocationResult(
            "maker-review-run", task.task_id, "maker", "maker", "codex", "maker-model",
            "succeeded", 0, 0.1, "implemented", "", False, "b" * 64,
            101.0, 101.1, role="implementation",
            memory_injection_event_id=injection.event_id,
        ), now=101.2)
        db.set_supervisor_mode(lease, "running", event_id="run-supervised-checker", now=101)
        db.record_gate_result(lease, gate, now=101.5)
        registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["maker", "same-model", "checker"],
            "head_candidate": None,
            "defined": {
                "maker": {"role": "implementation", "capability_floor": "standard",
                          "runtime": "codex", "model": "maker-model"},
                "same-model": {"role": "judgment", "capability_floor": "high",
                               "runtime": "codex", "model": "maker-model"},
                "checker": {"role": "judgment", "capability_floor": "high",
                            "runtime": "other-runtime", "model": "checker-model"},
            },
        })

        class CheckerAdapter:
            def __init__(self):
                self.requests = []

            def invoke(self, request):
                self.requests.append(request)
                output = json.dumps({
                    "task_id": request.task_id, "sha": request.review_sha,
                    "gate_fingerprint": request.gate_fingerprint,
                    "decision": "approved", "rationale": "Gate evidence and change reviewed.",
                    "evidence_refs": ["gate:registered-artifact"],
                })
                return InvocationResult(
                    request.invocation_id, request.task_id, "checker", request.identity.unit_id,
                    request.identity.runtime, request.identity.model, "succeeded", 0, 0.1,
                    output, "", False, "c" * 64, 102.0, 102.1,
                    request.review_sha, request.gate_fingerprint,
                )

        adapter = CheckerAdapter()
        supervisor = Supervisor(db, registry, SupervisorPolicy(
            require_strict=False, require_capacity_observation=False))
        with self.assertRaisesRegex(ValueError, "independent runtime/model"):
            supervisor.invoke_checker(
                lease, task.task_id, "same-model", gate=gate, prompt="review",
                working_directory=self.root, adapters={"codex": adapter},
                invocation_id="checker-same", now=102,
            )
        result = supervisor.invoke_checker(
            lease, task.task_id, "checker", gate=gate, prompt="review exactly this SHA",
            working_directory=self.root, adapters={"other-runtime": adapter},
            invocation_id="checker-mixed", memory_injection=injection, now=102,
        )
        self.assertEqual(result.invocation.runtime, "other-runtime")
        self.assertFalse(adapter.requests[0].writable)
        self.assertEqual(adapter.requests[0].review_sha, gate.sha)
        self.assertIn("memory-fact:fact-review-test", adapter.requests[0].prompt)
        self.assertIn("Do not cite unused facts", adapter.requests[0].prompt)
        with self.assertRaisesRegex(ValueError, "already started"):
            supervisor.invoke_checker(
                lease, task.task_id, "checker", gate=gate, prompt="review again",
                working_directory=self.root, adapters={"other-runtime": adapter},
                invocation_id="checker-mixed", now=103,
            )
        self.assertEqual(len(adapter.requests), 1)
        self.assertTrue(supervisor.apply_checker_output(
            lease, result, gate=gate, event_id="checker-review-applied",
            memory=memory, now=103.5))
        self.assertEqual(db.get_task(task.task_id).state, TaskState.DONE)
        self.assertEqual(memory.get_fact("fact-review-test").wins, 1)
        review_event = next(event["payload"] for event in db.events()
                            if event["payload"].get("type") == "task.reviewed")
        self.assertEqual(review_event["memory_injection_event_id"], injection.event_id)

    def test_review_rejects_same_checker_and_mismatched_gate(self):
        from dataclasses import replace

        task = replace(self.task, maker_identity={"unit_id": "worker-a", "runtime": "codex",
                                                  "model": "same-model"})
        gate = self.runner.run(task, self.root, expected_sha=self.sha, base_ref=self.base,
                               commands=self.passing_commands())
        checker = WorkerIdentity("worker-a", "reviewer", "high", "codex", "same-model")
        verdict = ReviewVerdict(task.task_id, self.sha, "wrong-fingerprint", checker,
                                "approved", "looks good")
        findings = validate_review(task, gate, verdict)
        self.assertTrue(any("maker and checker" in item for item in findings))
        self.assertTrue(any("not bound" in item for item in findings))

    def test_review_requires_registered_unchanged_gate_artifact(self):
        from dataclasses import replace

        task = replace(self.task, maker_identity={"unit_id": "maker", "runtime": "codex",
                                                  "model": "maker-model"})
        gate = self.runner.run(task, self.root, expected_sha=self.sha, base_ref=self.base,
                               commands=self.passing_commands())
        checker = WorkerIdentity("checker", "reviewer", "high", "other", "checker-model")
        verdict = ReviewVerdict(task.task_id, self.sha, gate.fingerprint, checker,
                                "approved", "reviewed")
        db = StateStore(Path(self.temp.name) / "tamper.sqlite")
        lease = db.acquire_head("CODEXDEVTEAM", "head", now=100)
        db.seed_task(lease, task, event_id="seed-tamper", now=101)
        with self.assertRaisesRegex(LeaseError, "registered by HEAD"):
            db.apply_review(lease, verdict, gate, checker_invocation_id="not-recorded",
                            event_id="review-no-receipt", now=102)
        db.record_gate_result(lease, gate, now=102)
        Path(gate.artifact_path).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(LeaseError, "missing or has changed"):
            db.apply_review(lease, verdict, gate, checker_invocation_id="not-recorded",
                            event_id="review-tampered", now=103)


class TaskTransitionTests(unittest.TestCase):
    def test_assigned_worker_can_submit_but_not_approve(self):
        self.assertTrue(allowed_transition(TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW,
                                           assigned_worker=True))
        self.assertFalse(allowed_transition(TaskState.NEEDS_REVIEW, TaskState.DONE,
                                            assigned_worker=True))

    def test_head_authority_can_review_and_unblock(self):
        self.assertTrue(allowed_transition(TaskState.NEEDS_REVIEW, TaskState.DONE,
                                           head_authority=True))
        self.assertTrue(allowed_transition(TaskState.BLOCKED, TaskState.IN_PROGRESS,
                                           head_authority=True))

    def test_terminal_state_cannot_transition(self):
        self.assertFalse(allowed_transition(TaskState.DONE, TaskState.PENDING,
                                            head_authority=True))


class TerritoryTests(unittest.TestCase):
    def test_recursive_glob_and_segment_boundaries(self):
        self.assertTrue(decide_write("src/pkg/file.py", ["src/**"]).allowed)
        self.assertFalse(decide_write("src-other/file.py", ["src/**"]).allowed)

    def test_protected_paths_override_owned_paths(self):
        decision = decide_write("PLAN.md", ["**"], protected_paths=["PLAN.md"])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "protected path")

    def test_unknown_actor_and_path_traversal_fail_closed(self):
        self.assertFalse(decide_write("src/a.py", ["src/**"], actor_known=False).allowed)
        with self.assertRaises(ValueError):
            decide_write("../outside", ["**"])

    def test_grant_must_be_allowed_and_uncontended(self):
        self.assertTrue(validate_grant("src/a_test.py", allowed_patterns=["src/*_test.py"]).allowed)
        self.assertFalse(validate_grant("src/a_test.py", allowed_patterns=["src/*_test.py"],
                                        other_active_owners=["src/**"]).allowed)


class TerritoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repository"
        self.root.mkdir()
        (self.root / "src").mkdir()
        self.policy = TerritoryPolicy(self.root, protected_paths=(".codexdevteam/**",))
        self.builder = WorkerIdentity("builder-a", "implementation", "standard",
                                      "codex", "configured-model")

    def tearDown(self):
        self.temp.cleanup()

    def task(self, state=TaskState.IN_PROGRESS, *, task_id="TASK-80",
             worker="builder-a", owned=("src/**",), grants=()):
        return TaskRecord(task_id, "Firewall fixture", state, worker,
                          "medium", owned, protected_grants=grants)

    def authorize(self, paths, tasks=None, *, actor=None, task_id="TASK-80"):
        return self.policy.authorize(actor=actor or self.builder, task_id=task_id,
                                     paths=paths, tasks=tasks or (self.task(),))

    def test_assigned_active_builder_is_limited_to_task_territory(self):
        inside = self.authorize(("src/new.py",))
        outside = self.authorize(("docs/new.md",))
        self.assertTrue(inside.allowed)
        self.assertFalse(outside.allowed)
        self.assertEqual(outside.decisions[0].reason, "outside owned paths")

    def test_unknown_mismatched_and_nonwritable_task_states_fail_closed(self):
        self.assertFalse(self.policy.authorize(actor=None, task_id="TASK-80",
                                               paths=("src/a.py",),
                                               tasks=(self.task(),)).allowed)
        other_actor = WorkerIdentity("builder-b", "implementation", "standard",
                                     "codex", "configured-model")
        self.assertFalse(self.authorize(("src/a.py",), actor=other_actor).allowed)
        self.assertFalse(self.authorize(("src/a.py",), tasks=(self.task(TaskState.NEEDS_REVIEW),)).allowed)

    def test_protected_paths_require_explicit_task_grant_and_stay_in_owned_scope(self):
        path = ".codexdevteam/project/work.json"
        owned = (".codexdevteam/project/**",)
        no_grant = self.task(owned=owned)
        grant = self.task(owned=owned, grants=(".codexdevteam/project/work.json",))
        denied = self.authorize((path,), tasks=(no_grant,))
        allowed = self.authorize((path,), tasks=(grant,))
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.decisions[0].reason, "protected path")
        self.assertTrue(allowed.allowed)

    def test_active_task_overlap_denies_even_when_current_task_has_grant(self):
        current = self.task(owned=("src/**",), grants=("src/shared.py",))
        other = self.task(task_id="TASK-81", worker="builder-b", owned=("src/shared.py",))
        result = self.authorize(("src/shared.py",), tasks=(current, other))
        self.assertFalse(result.allowed)
        self.assertIn("TASK-81", result.decisions[0].reason)

    def test_duplicate_task_ids_are_ambiguous_and_denied(self):
        task = self.task()
        result = self.authorize(("src/a.py",), tasks=(task, task))
        self.assertFalse(result.allowed)
        self.assertEqual(result.decisions[0].reason, "task set has an ambiguous task ID")

    def test_plan_and_head_identity_require_the_separate_lease_checked_state_path(self):
        denied = self.authorize(("PLAN.md",), tasks=(self.task(owned=("**",)),))
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.decisions[0].reason, "PLAN.md is controlled through HEAD")
        head = WorkerIdentity("head-main", "head", "architecture", "codex", "configured-model")
        self.assertFalse(self.authorize(("src/a.py",), actor=head).allowed)

    def test_traversal_absolute_windows_separators_and_symlink_escape_fail_closed(self):
        result = self.authorize(("../outside", "/etc/passwd", "src\\file.py"))
        self.assertFalse(result.allowed)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "src" / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        escaped = self.authorize(("src/escape/new.py",))
        self.assertFalse(escaped.allowed)
        self.assertEqual(escaped.decisions[0].reason, "path resolves outside repository")


class CodexHookAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repository"
        self.root.mkdir()
        subprocess.run(["git", "-C", str(self.root), "init"], check=True,
                       capture_output=True)
        self.state_path = Path(self.temp.name) / "state.sqlite"
        self.store = StateStore(self.state_path)
        self.lease = self.store.acquire_head("CODEXDEVTEAM", "head", now=100)
        self.task = TaskRecord("TASK-82", "Hook fixture", TaskState.IN_PROGRESS,
                               "builder-hook", "medium", ("src/**",))
        self.store.seed_task(self.lease, self.task, event_id="seed-hook-task", now=101)
        self.environment = {
            "CODEXDEVTEAM_WORKER_ID": "builder-hook",
            "CODEXDEVTEAM_WORKER_ROLE": "implementation",
            "CODEXDEVTEAM_CAPABILITY_FLOOR": "standard",
            "CODEXDEVTEAM_RUNTIME": "codex",
            "CODEXDEVTEAM_MODEL": "configured-model",
            "CODEXDEVTEAM_TASK_ID": "TASK-82",
            "CODEXDEVTEAM_STATE_DB": str(self.state_path),
        }

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def event(tool_input, *, tool="apply_patch", cwd=None):
        return {"hook_event_name": "PreToolUse", "tool_name": tool,
                "tool_input": tool_input, "cwd": cwd}

    def test_codex_patch_adapter_allows_owned_edit_and_denies_out_of_territory(self):
        from codexdevteam_kernel.codex_hook import evaluate_codex_file_event

        inside = self.event({"command": "*** Begin Patch\n*** Add File: src/new.py\n+line\n*** End Patch"},
                            cwd=str(self.root))
        outside = self.event({"command": "*** Begin Patch\n*** Add File: docs/new.md\n+line\n*** End Patch"},
                             cwd=str(self.root))
        self.assertTrue(evaluate_codex_file_event(inside, self.environment).allowed)
        denied = evaluate_codex_file_event(outside, self.environment)
        self.assertFalse(denied.allowed)
        response = denied.to_codex_response()
        self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(response["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_codex_hook_fails_closed_on_missing_identity_malformed_patch_and_secret(self):
        from codexdevteam_kernel.codex_hook import evaluate_codex_file_event

        valid = self.event({"command": "*** Begin Patch\n*** Add File: src/new.py\n+x\n*** End Patch"},
                           cwd=str(self.root))
        self.assertFalse(evaluate_codex_file_event(valid, {}).allowed)
        malformed = self.event({"command": "not a recognized patch"}, cwd=str(self.root))
        self.assertFalse(evaluate_codex_file_event(malformed, self.environment).allowed)
        unsupported = self.event({"command": "*** Begin Patch\n*** Create File: src/new.py\n*** End Patch"},
                                 cwd=str(self.root))
        self.assertFalse(evaluate_codex_file_event(unsupported, self.environment).allowed)
        incomplete_edit = self.event({"file_path": "src/new.py"}, tool="Edit",
                                     cwd=str(self.root))
        self.assertFalse(evaluate_codex_file_event(incomplete_edit, self.environment).allowed)
        secret = "sk-" + "A" * 24
        leaked = self.event({"command": f"*** Begin Patch\n*** Add File: src/key.py\n+{secret}\n*** End Patch"},
                            cwd=str(self.root))
        result = evaluate_codex_file_event(leaked, self.environment)
        self.assertFalse(result.allowed)
        self.assertNotIn(secret, result.reason)


class StateSchemaMigrationTests(unittest.TestCase):
    def test_schema_v6_adds_containment_identity_without_losing_live_invocation(self):
        from codexdevteam_kernel.process_identity import ProcessIdentity

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state-v6.sqlite"
            store = StateStore(path)
            lease = store.acquire_head("CODEXDEVTEAM", "schema-v6", now=10)
            task = TaskRecord(
                "TASK-V6", "Preserve live maker containment", TaskState.CLAIMED,
                "builder", "medium", ("src/v6.py",),
                maker_identity={"unit_id": "builder", "runtime": "codex", "model": "model"},
            )
            store.seed_task(lease, task, event_id="schema-v6-seed", now=11)
            store.set_supervisor_mode(lease, "running", event_id="schema-v6-running", now=12)
            invocation_id = "maker:schema-v6"
            store.start_task_invocation(lease, task.task_id, invocation_id, now=13)
            identity = ProcessIdentity(43210, "windows:12345", None)
            store.record_task_invocation_process(
                lease, task.task_id, invocation_id, identity, now=14)
            db = sqlite3.connect(path)
            try:
                db.execute("ALTER TABLE task_invocation_liveness "
                           "DROP COLUMN process_containment_ref")
                db.execute("PRAGMA user_version=6")
            finally:
                db.close()

            migrated = StateStore(path)
            liveness = migrated.task_invocation_liveness(task_id=task.task_id)[0]
            self.assertEqual(liveness["state"], "running")
            self.assertEqual((liveness["process_pid"], liveness["process_start_token"],
                              liveness["process_group_id"],
                              liveness["process_containment_ref"]),
                             (43210, "windows:12345", None, None))
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            finally:
                db.close()

    def test_schema_v5_adds_process_identity_columns_without_losing_live_invocation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state-v5.sqlite"
            store = StateStore(path)
            lease = store.acquire_head("CODEXDEVTEAM", "schema-v5", now=10)
            task = TaskRecord(
                "TASK-V5", "Preserve live maker", TaskState.CLAIMED, "builder",
                "medium", ("src/v5.py",),
                maker_identity={"unit_id": "builder", "runtime": "codex", "model": "model"},
            )
            store.seed_task(lease, task, event_id="schema-v5-seed", now=11)
            store.set_supervisor_mode(lease, "running", event_id="schema-v5-running", now=12)
            store.start_task_invocation(lease, task.task_id, "maker:schema-v5", now=13)
            db = sqlite3.connect(path)
            try:
                for column in ("process_containment_ref", "process_group_id",
                               "process_start_token", "process_pid"):
                    db.execute(f"ALTER TABLE task_invocation_liveness DROP COLUMN {column}")
                db.execute("PRAGMA user_version=5")
            finally:
                db.close()

            migrated = StateStore(path)
            liveness = migrated.task_invocation_liveness(task_id=task.task_id)
            self.assertEqual(len(liveness), 1)
            self.assertEqual(liveness[0]["state"], "running")
            self.assertIsNone(liveness[0]["process_pid"])
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            finally:
                db.close()

    def test_unversioned_current_schema_is_adopted_without_losing_events(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite"
            store = StateStore(path)
            lease = store.acquire_head("CODEXDEVTEAM", "schema-test", now=10)
            task = TaskRecord("TASK-SCHEMA", "Preserve this task", TaskState.PENDING,
                              None, "medium", ("src/schema.py",))
            store.seed_task(lease, task, event_id="schema-seed", now=11)
            db = sqlite3.connect(path)
            try:
                db.execute("PRAGMA user_version=0")
            finally:
                db.close()
            reopened = StateStore(path)
            self.assertEqual(reopened.get_task(task.task_id), task)
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            finally:
                db.close()

    def test_schema_v1_adds_handover_provenance_tables_without_losing_task_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite"
            store = StateStore(path)
            lease = store.acquire_head("CODEXDEVTEAM", "schema-v1", now=10)
            task = TaskRecord("TASK-V1", "Preserve across v2", TaskState.PENDING,
                              None, "medium", ("src/v1.py",))
            store.seed_task(lease, task, event_id="schema-v1-seed", now=11)
            db = sqlite3.connect(path)
            try:
                db.execute("DROP TABLE handover_imports")
                db.execute("DROP TABLE historical_task_ids")
                db.execute("DROP TABLE handover_context_fields")
                db.execute("PRAGMA user_version=1")
            finally:
                db.close()
            migrated = StateStore(path)
            self.assertEqual(migrated.get_task(task.task_id), task)
            self.assertEqual(migrated.historical_task_ids(), ())
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            finally:
                db.close()

    def test_schema_v2_adds_handover_context_table_without_losing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite"
            store = StateStore(path)
            lease = store.acquire_head("CODEXDEVTEAM", "schema-v2", now=10)
            task = TaskRecord("TASK-V2", "Preserve across v3", TaskState.PENDING,
                              None, "medium", ("src/v2.py",))
            store.seed_task(lease, task, event_id="schema-v2-seed", now=11)
            db = sqlite3.connect(path)
            try:
                db.execute("DROP TABLE handover_context_fields")
                db.execute("PRAGMA user_version=2")
            finally:
                db.close()
            migrated = StateStore(path)
            self.assertEqual(migrated.get_task(task.task_id), task)
            self.assertEqual(migrated.handover_context_fields(), ())
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            finally:
                db.close()

    def test_schema_v3_adds_process_termination_evidence_without_losing_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite"
            store = StateStore(path)
            lease = store.acquire_head("CODEXDEVTEAM", "schema-v3", now=10)
            store.record_invocation(lease, InvocationResult(
                "schema-v3-invocation", None, "head", "head", "codex", "model",
                "succeeded", 0, 1.0, "", "", False, "a" * 64, 11, 12,
            ), now=13)
            db = sqlite3.connect(path)
            try:
                for column in ("process_tree_cancel_exit_code",
                               "process_tree_cancel_verified",
                               "process_tree_cancel_method"):
                    db.execute(f"ALTER TABLE invocation_receipts DROP COLUMN {column}")
                db.execute("PRAGMA user_version=3")
            finally:
                db.close()
            migrated = StateStore(path)
            db = sqlite3.connect(path)
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                row = db.execute(
                    "SELECT status, process_tree_cancel_method, "
                    "process_tree_cancel_verified, process_tree_cancel_exit_code "
                    "FROM invocation_receipts WHERE invocation_id='schema-v3-invocation'").fetchone()
            finally:
                db.close()
            self.assertEqual(version, 7)
            self.assertEqual(row, ("succeeded", None, None, None))
            self.assertEqual(len(migrated.events()), 1)

    def test_schema_v4_adds_signed_cancellation_receipt_storage_without_losing_events(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite"
            store = StateStore(path)
            lease = store.acquire_head("CODEXDEVTEAM", "schema-v4", now=10)
            store.record_invocation(lease, InvocationResult(
                "schema-v4-invocation", None, "head", "head", "codex", "model",
                "succeeded", 0, 1.0, "", "", False, "b" * 64, 11, 12,
            ), now=13)
            db = sqlite3.connect(path)
            try:
                db.execute("DROP TABLE invocation_cancellation_receipts")
                db.execute("ALTER TABLE task_invocation_liveness "
                           "DROP COLUMN termination_hmac_key")
                db.execute("PRAGMA user_version=4")
            finally:
                db.close()
            migrated = StateStore(path)
            db = sqlite3.connect(path)
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                liveness_columns = {row[1] for row in db.execute(
                    "PRAGMA table_info(task_invocation_liveness)")}
            finally:
                db.close()
            self.assertEqual(version, 7)
            self.assertIn("invocation_cancellation_receipts", tables)
            self.assertIn("termination_hmac_key", liveness_columns)
            self.assertEqual(len(migrated.events()), 1)

    def test_future_schema_is_rejected_before_initialization(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "future.sqlite"
            db = sqlite3.connect(path)
            try:
                db.execute("PRAGMA user_version=999")
            finally:
                db.close()
            with self.assertRaisesRegex(LeaseError, "newer than supported"):
                StateStore(path)
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("SELECT name FROM sqlite_master "
                                             "WHERE type='table'").fetchall(), [])
            finally:
                db.close()

    def test_incompatible_legacy_schema_fails_and_rolls_back_table_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.sqlite"
            db = sqlite3.connect(path)
            try:
                db.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY)")
            finally:
                db.close()
            with self.assertRaisesRegex(LeaseError, "table tasks is incompatible"):
                StateStore(path)
            db = sqlite3.connect(path)
            try:
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                db.close()
            self.assertEqual(tables, {"tasks"})


class HeadLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "state.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_stale_invocation_reaper_keeps_liveness_held_until_verified(self):
        from codexdevteam_kernel.process_identity import ProcessIdentity
        from codexdevteam_kernel.process_reaper import ProcessReapResult, ProcessReapStatus

        lease = self.store.acquire_head("CODEXDEVTEAM", "reaper-head",
                                        ttl_seconds=1000, now=100)
        task = TaskRecord("TASK-REAPER-STATE", "Reap only after proof", TaskState.CLAIMED,
                          "builder", "medium", ("src/reaper.py",),
                          maker_identity={"unit_id": "builder", "runtime": "codex",
                                          "model": "builder-model"})
        self.store.seed_task(lease, task, event_id="seed-reaper-state", now=101)
        self.store.set_supervisor_mode(lease, "running",
                                       event_id="run-reaper-state", now=101.5)
        invocation_id = "maker:reaper-state"
        self.assertTrue(self.store.start_task_invocation(
            lease, task.task_id, invocation_id, now=102))
        identity = ProcessIdentity(43210, "fixture-boot:12345", 43210)
        self.assertTrue(self.store.record_task_invocation_process(
            lease, task.task_id, invocation_id, identity, now=103))
        uncertain = ProcessReapResult(ProcessReapStatus.IDENTITY_MISMATCH,
                                      "fixture_reaper", identity.pid,
                                      identity.process_group_id)
        with patch("codexdevteam_kernel.state.reap_managed_process", return_value=uncertain):
            result = self.store.reap_stale_invocation(
                lease, invocation_id, stale_after_seconds=10, now=200)
        self.assertFalse(result.verified)
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "running")
        self.assertTrue(any(event["payload"].get("type") == "task.invocation_reap_attempted"
                            and event["payload"].get("verified") is False
                            for event in self.store.events()))
        verified_group = ProcessReapResult(ProcessReapStatus.GROUP_TERMINATED,
                                     "fixture_reaper", identity.pid,
                                     identity.process_group_id)
        with patch("codexdevteam_kernel.state.reap_managed_process", return_value=verified_group):
            result = self.store.reap_stale_invocation(
                lease, invocation_id, stale_after_seconds=10, now=201)
        self.assertTrue(result.verified)
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "running")
        self.assertTrue(any(event["payload"].get("type") ==
                            "task.invocation_process_group_terminated"
                            and event["payload"].get("whole_tree_verified") is False
                            for event in self.store.events()))
        verified_tree = ProcessReapResult(ProcessReapStatus.TERMINATED,
                                          "fixture_containment", identity.pid,
                                          identity.process_group_id,
                                          whole_tree_verified=True)
        with patch("codexdevteam_kernel.state.reap_managed_process", return_value=verified_tree):
            result = self.store.reap_stale_invocation(
                lease, invocation_id, stale_after_seconds=10, now=202)
        self.assertTrue(result.whole_tree_verified)
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "completed")
        self.assertTrue(any(event["payload"].get("type") == "task.invocation_process_reaped"
                            and event["payload"].get("verified") is True
                            for event in self.store.events()))

    def test_state_store_linux_reaper_terminates_stale_group_under_head_lease(self):
        import signal
        if (os.name != "posix" or not hasattr(os, "pidfd_open")
                or not hasattr(signal, "pidfd_send_signal")
                or not Path("/proc/sys/kernel/random/boot_id").is_file()):
            self.skipTest("Linux pidfd process-group reaping is unavailable")
        from codexdevteam_kernel.process_identity import capture_process_identity

        lease = self.store.acquire_head("CODEXDEVTEAM", "linux-reaper-head",
                                        ttl_seconds=1000, now=100)
        task = TaskRecord("TASK-REAPER-LINUX", "Reap a stale isolated group", TaskState.CLAIMED,
                          "builder", "medium", ("src/reaper.py",),
                          maker_identity={"unit_id": "builder", "runtime": "codex",
                                          "model": "builder-model"})
        self.store.seed_task(lease, task, event_id="seed-reaper-linux", now=101)
        self.store.set_supervisor_mode(lease, "running",
                                       event_id="run-reaper-linux", now=101.5)
        invocation_id = "maker:reaper-linux"
        self.assertTrue(self.store.start_task_invocation(
            lease, task.task_id, invocation_id, now=102))
        child_code = ("import subprocess,sys,time; "
                      "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
                      "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(30)")
        process = subprocess.Popen([sys.executable, "-c", child_code], start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            identity = capture_process_identity(process.pid)
            self.assertTrue(self.store.record_task_invocation_process(
                lease, task.task_id, invocation_id, identity, now=103))
            result = self.store.reap_stale_invocation(
                lease, invocation_id, stale_after_seconds=10, grace_seconds=0.05,
                timeout_seconds=3, now=200)
            self.assertTrue(result.verified)
            self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                             "running")
            self.assertTrue(any(event["payload"].get("type") ==
                                "task.invocation_process_group_terminated"
                                and event["payload"].get("whole_tree_verified") is False
                                for event in self.store.events()))
            process.wait(timeout=3)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def test_state_store_windows_job_recovery_clears_liveness_only_after_full_tree_proof(self):
        if os.name != "nt":
            self.skipTest("Windows Job Object recovery test")
        from codexdevteam_kernel.runtime import _run_invocation_process

        lease = self.store.acquire_head("CODEXDEVTEAM", "windows-reaper-head",
                                        ttl_seconds=1000, now=100)
        task = TaskRecord("TASK-REAPER-WINDOWS", "Recover a Windows job", TaskState.CLAIMED,
                          "builder", "medium", ("src/reaper.py",),
                          maker_identity={"unit_id": "builder", "runtime": "codex",
                                          "model": "builder-model"})
        self.store.seed_task(lease, task, event_id="seed-reaper-windows", now=101)
        self.store.set_supervisor_mode(lease, "running",
                                       event_id="run-reaper-windows", now=101.5)
        invocation_id = "maker:reaper-windows"
        self.store.start_task_invocation(lease, task.task_id, invocation_id, now=102)
        lease = self.store.renew_head(lease, ttl_seconds=1000, now=103)
        identity = WorkerIdentity("builder", "implementation", "standard",
                                  "codex", "builder-model")
        request = InvocationRequest(
            invocation_id=invocation_id, task_id=task.task_id, purpose="maker",
            identity=identity, prompt="run maker", working_directory=str(Path.cwd()),
            timeout_seconds=15, writable=True,
            on_process_start=lambda process_identity: self.store.record_task_invocation_process(
                lease, task.task_id, invocation_id, process_identity, now=104),
        )
        child_code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid, flush=True)"
        result = _run_invocation_process(
            [sys.executable, "-c", child_code], "run maker", Path.cwd(),
            os.environ.copy(), request,
        )
        self.assertEqual(result[3:5], ("windows_job_object", True))
        liveness = self.store.task_invocation_liveness(task_id=task.task_id)[0]
        self.assertRegex(liveness["process_containment_ref"],
                         r"^Global\\CODEXDEVTEAM-[0-9a-f]{32}$")
        reaped = self.store.reap_stale_invocation(
            lease, invocation_id, stale_after_seconds=10, now=200)
        self.assertTrue(reaped.whole_tree_verified)
        self.assertEqual(self.store.task_invocation_liveness(task_id=task.task_id)[0]["state"],
                         "completed")

    def test_task_snapshot_contains_only_active_tasks_and_is_read_only(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        records = (
            TaskRecord("TASK-SNAPSHOT", "Current", TaskState.CLAIMED,
                       "builder", "medium", ("src/current.py",),
                       acceptance_criteria=("private acceptance detail",)),
            TaskRecord("TASK-ACTIVE-OTHER", "Other active", TaskState.IN_PROGRESS,
                       "builder-2", "high", ("src/other.py",)),
            TaskRecord("TASK-PENDING-OTHER", "Pending", TaskState.PENDING,
                       None, "low", ("docs/pending.md",)),
        )
        for index, task in enumerate(records):
            self.store.seed_task(lease, task, event_id=f"snapshot-seed-{index}", now=101 + index)
        directory = Path(self.temp.name) / "snapshot"
        directory.mkdir()
        snapshot = self.store.create_task_snapshot(directory / "builder.sqlite", "TASK-SNAPSHOT")
        db = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT task_id, payload_json FROM tasks ORDER BY task_id").fetchall()
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        finally:
            db.close()
        self.assertEqual([row[0] for row in rows], ["TASK-ACTIVE-OTHER", "TASK-SNAPSHOT"])
        snapshot_task = TaskRecord.from_dict(json.loads(rows[1][1]))
        self.assertEqual(snapshot_task.title, "Hook policy snapshot")
        self.assertEqual(snapshot_task.acceptance_criteria, ())
        self.assertEqual([row[0] for row in tables], ["tasks"])
        db = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM tasks")
        finally:
            db.close()

    def plan_fixture(self, task_id="TASK-PLAN"):
        root = Path(self.temp.name) / "project"
        root.mkdir(exist_ok=True)
        plan = root / "PLAN.md"
        plan.write_text(
            f"### {task_id}\n**Title:** Project task\n**Status:** pending\n"
            "**Assigned_To:** worker-a\n**Priority:** medium\n"
            "**Owned_Paths:** src/project.py\n**Custom_Field:** retain\n",
            encoding="utf-8", newline="",
        )
        return root, plan

    def test_plan_projection_and_task_transition_commit_together(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-PLAN", "Project task", TaskState.PENDING,
                          "worker-a", "medium", ("src/project.py",))
        self.store.seed_task(lease, task, event_id="seed-plan-project", now=101)
        root, plan = self.plan_fixture()
        original_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
        changed = self.store.transition_task_with_plan(
            lease, task.task_id, TaskState.CLAIMED, event_id="claim-with-plan",
            expected_state=TaskState.PENDING, project_root=root,
            expected_plan_sha256=original_sha, now=102,
        )
        self.assertTrue(changed)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.CLAIMED)
        content = plan.read_text(encoding="utf-8")
        self.assertIn("**Status:** claimed", content)
        self.assertIn("**Assigned_To:** worker-a", content)
        self.assertIn("**Custom_Field:** retain", content)
        self.assertEqual(self.store.pending_plan_projections(), ())
        self.assertFalse(self.store.transition_task_with_plan(
            lease, task.task_id, TaskState.CLAIMED, event_id="claim-with-plan",
            expected_state=TaskState.PENDING, project_root=root,
            expected_plan_sha256=original_sha, now=103,
        ))

    def test_assignment_projects_claimed_state_when_project_root_is_supplied(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-PLAN", "Project task", TaskState.PENDING,
                          None, "medium", ("src/project.py",))
        self.store.seed_task(lease, task, event_id="seed-plan-assign", now=101)
        root, plan = self.plan_fixture()
        registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["worker-a"], "head_candidate": None,
            "defined": {"worker-a": {"role": "implementation", "capability_floor": "standard",
                                      "runtime": "codex", "model": "configured-worker"}},
        })
        claimed = self.store.assign_pending_task(
            lease, task.task_id, "worker-a", registry, event_id="assign-plan",
            project_root=root, now=102,
        )
        self.assertEqual(claimed.state, TaskState.CLAIMED)
        self.assertIn("**Status:** claimed", plan.read_text(encoding="utf-8"))
        self.assertEqual(self.store.pending_plan_projections(), ())

    def test_plan_projection_outbox_recovers_after_file_replace_failure(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-PLAN", "Project task", TaskState.PENDING,
                          "worker-a", "medium", ("src/project.py",))
        self.store.seed_task(lease, task, event_id="seed-plan-recovery", now=101)
        root, plan = self.plan_fixture()
        expected_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
        with patch("codexdevteam_kernel.state._atomic_replace_plan",
                   side_effect=OSError("simulated process interruption")):
            with self.assertRaisesRegex(PlanProjectionPending, "simulated process"):
                self.store.transition_task_with_plan(
                    lease, task.task_id, TaskState.CLAIMED, event_id="claim-plan-recovery",
                    expected_state=TaskState.PENDING, project_root=root,
                    expected_plan_sha256=expected_sha, now=102,
                )
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.CLAIMED)
        self.assertEqual(len(self.store.pending_plan_projections()), 1)
        original = plan.read_bytes()
        plan.write_bytes(original + b"\n# concurrent edit\n")
        with self.assertRaisesRegex(PlanWriteConflict, "no longer matches"):
            self.store.apply_pending_plan_projections(lease, now=103)
        self.assertIn(b"# concurrent edit", plan.read_bytes())
        self.assertEqual(len(self.store.pending_plan_projections()), 1)
        plan.write_bytes(original)
        self.assertEqual(self.store.apply_pending_plan_projections(lease, now=103),
                         ("claim-plan-recovery",))
        self.assertIn("**Status:** claimed", plan.read_text(encoding="utf-8"))
        self.assertEqual(self.store.pending_plan_projections(), ())

    def test_plan_projection_recovery_is_fenced_and_retries_applied_replace(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100,
                                        ttl_seconds=5)
        task = TaskRecord("TASK-PLAN", "Project task", TaskState.PENDING,
                          "worker-a", "medium", ("src/project.py",))
        self.store.seed_task(lease, task, event_id="seed-plan-fence", now=101)
        root, plan = self.plan_fixture()
        expected_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
        with patch("codexdevteam_kernel.state._atomic_replace_plan",
                   side_effect=OSError("after intent commit")):
            with self.assertRaises(PlanProjectionPending):
                self.store.transition_task_with_plan(
                    lease, task.task_id, TaskState.CLAIMED, event_id="claim-plan-fence",
                    expected_state=TaskState.PENDING, project_root=root,
                    expected_plan_sha256=expected_sha, now=102,
                )
        next_lease = self.store.acquire_head(
            "CODEXDEVTEAM", "instance-b", now=107, takeover_confirmed=True)
        with self.assertRaises(LeaseError):
            self.store.apply_pending_plan_projections(lease, now=108)
        from codexdevteam_kernel import state as state_module
        replace_plan = state_module._atomic_replace_plan

        def replace_then_interrupt(*args):
            replace_plan(*args)
            raise OSError("after disk replace")

        with patch("codexdevteam_kernel.state._atomic_replace_plan",
                   side_effect=replace_then_interrupt):
            with self.assertRaises(OSError):
                self.store.apply_pending_plan_projections(next_lease, now=108)
        self.assertIn("**Status:** claimed", plan.read_text(encoding="utf-8"))
        self.assertEqual(len(self.store.pending_plan_projections()), 1)
        # Retry sees the projected hash already on disk and only clears the intent.
        self.assertEqual(self.store.apply_pending_plan_projections(next_lease, now=109),
                         ("claim-plan-fence",))
        self.assertEqual(self.store.pending_plan_projections(), ())

    def test_live_lease_is_exclusive_and_release_allows_handover(self):
        first = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        with self.assertRaises(LeaseError):
            self.store.acquire_head("DEVDEPARTMENT", "instance-b", now=101)
        self.store.release_head(first, now=101)
        with self.assertRaises(LeaseError):
            self.store.acquire_head("DEVDEPARTMENT", "instance-b", now=102)
        second = self.store.acquire_head("DEVDEPARTMENT", "instance-b", now=102,
                                         takeover_confirmed=True)
        self.assertGreater(second.generation, first.generation)

    def test_head_lease_guard_renews_during_external_work(self):
        import time
        from codexdevteam_kernel.lease_guard import HeadLeaseGuard
        first = self.store.acquire_head("CODEXDEVTEAM", "guarded-head", ttl_seconds=0.04)
        renewals = []
        with HeadLeaseGuard(self.store, first, ttl_seconds=0.12,
                            renew_interval_seconds=0.02,
                            on_renew=renewals.append) as guard:
            time.sleep(0.07)
            guard.raise_if_lost()
            with self.assertRaises(LeaseError):
                self.store.acquire_head("CODEXDEVTEAM", "competing-head")
        self.assertGreaterEqual(len(renewals), 2)
        self.store.release_head(first)
        second = self.store.acquire_head("CODEXDEVTEAM", "next-head",
                                         takeover_confirmed=True)
        self.assertGreater(second.generation, first.generation)

    def test_head_lease_guard_signals_loss_and_stops_renewing(self):
        import time
        from codexdevteam_kernel.lease_guard import HeadLeaseGuard
        first = self.store.acquire_head("CODEXDEVTEAM", "guarded-head", ttl_seconds=1)
        original_renew = self.store.renew_head
        with patch.object(self.store, "renew_head", side_effect=(
                original_renew(first, ttl_seconds=0.15), LeaseError("fenced"))):
            guard = HeadLeaseGuard(self.store, first, ttl_seconds=0.15,
                                   renew_interval_seconds=0.02)
            with guard:
                time.sleep(0.05)
                self.assertTrue(guard.lost_event.is_set())
                with self.assertRaisesRegex(LeaseError, "lost during runtime"):
                    guard.raise_if_lost()

    def test_two_store_instances_cannot_claim_one_task_twice(self):
        import threading

        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-CONTENDED", "Single claim", TaskState.PENDING,
                          None, "medium", ("src/contended/**",))
        self.store.seed_task(lease, task, event_id="seed-contended", now=101)
        registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["builder-a", "builder-b"],
            "head_candidate": None,
            "defined": {worker: {"role": "implementation", "capability_floor": "standard",
                                 "runtime": "codex", "model": "model-" + worker}
                        for worker in ("builder-a", "builder-b")},
        })
        other_store = StateStore(self.store.path)
        start = threading.Barrier(2)

        def claim(store, worker_id):
            start.wait()
            try:
                store.assign_pending_task(lease, task.task_id, worker_id, registry,
                                          event_id="contended-" + worker_id, now=102)
                return "claimed"
            except (LeaseError, DispatchError):
                return "denied"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda item: claim(*item),
                                     ((self.store, "builder-a"), (other_store, "builder-b"))))
        self.assertEqual(sorted(outcomes), ["claimed", "denied"])
        assigned = [event for event in self.store.events()
                    if event["payload"].get("type") == "task.assigned"]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.CLAIMED)

    def test_expiry_fences_stale_writer(self):
        first = self.store.acquire_head("CODEXDEVTEAM", "instance-a", ttl_seconds=5, now=100)
        second = self.store.acquire_head("CODEXDEVTEAM", "instance-b", now=106,
                                         takeover_confirmed=True)
        with self.assertRaises(LeaseError):
            self.store.record_event(first, "old", {"state": "done"}, now=107)
        self.assertTrue(self.store.record_event(second, "new", {"state": "claimed"}, now=107))

    def test_event_recording_is_idempotent_and_requires_live_lease(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        self.assertTrue(self.store.record_event(lease, "event-1", {"x": 1}, now=101))
        self.assertFalse(self.store.record_event(lease, "event-1", {"x": 1}, now=102))
        self.assertEqual(len(self.store.events()), 1)

    def test_expired_lease_cannot_write(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", ttl_seconds=1, now=100)
        with self.assertRaises(LeaseError):
            self.store.record_event(lease, "late", {}, now=102)

    def test_supervisor_starts_parked_and_park_resume_are_lease_journaled(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        self.assertEqual(self.store.get_supervisor_mode()["mode"], "parked")
        with self.assertRaisesRegex(ValueError, "reason"):
            self.store.set_supervisor_mode(lease, "parked", event_id="park-no-reason", now=101)
        self.assertTrue(self.store.set_supervisor_mode(lease, "running", event_id="resume", now=101))
        self.assertEqual(self.store.get_supervisor_mode()["mode"], "running")
        self.assertTrue(self.store.set_supervisor_mode(lease, "parked", event_id="park",
                                                        reason="operator requested", now=102))
        self.assertEqual(self.store.get_supervisor_mode()["reason"], "operator requested")

    def test_escalations_dedupe_backoff_and_resolve_durably(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", ttl_seconds=20000, now=100)
        first = self.store.record_escalation(
            lease, escalation_key="TASK-9:gate", task_id="TASK-9", severity="high",
            message="gate repeatedly failed", event_id="escalate-1", now=101,
            remind_after_seconds=60)
        self.assertTrue(first["notify"])
        again = self.store.record_escalation(
            lease, escalation_key="TASK-9:gate", task_id="TASK-9", severity="high",
            message="gate repeatedly failed", event_id="escalate-2", now=120,
            remind_after_seconds=60)
        self.assertFalse(again["notify"])
        due = self.store.record_escalation(
            lease, escalation_key="TASK-9:gate", task_id="TASK-9", severity="high",
            message="gate still failing", event_id="escalate-3", now=162,
            remind_after_seconds=60)
        self.assertTrue(due["notify"])
        self.assertEqual(due["notification_count"], 2)
        duplicate_event = self.store.record_escalation(
            lease, escalation_key="TASK-9:gate", task_id="TASK-9", severity="high",
            message="gate repeatedly failed", event_id="escalate-1", now=500,
            remind_after_seconds=60)
        self.assertTrue(duplicate_event["duplicate"])
        self.assertTrue(duplicate_event["notify"])
        self.assertEqual(duplicate_event["notification_count"], 1)
        self.assertTrue(self.store.resolve_escalation(lease, "TASK-9:gate",
                                                      event_id="resolve-1", now=170))
        self.assertEqual(self.store.escalations(status="open"), [])
        self.assertEqual(self.store.pending_escalation_notifications(), ())

    def test_escalation_outbox_redacts_secret_like_message_before_persistence(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        secret = "sk-" + "A" * 24
        self.store.record_escalation(
            lease, escalation_key="TASK-SECRET:failure", task_id="TASK-SECRET",
            severity="high", message=f"upstream failure included {secret}",
            event_id="escalate-secret", now=101,
        )
        self.assertNotIn(secret, str(self.store.events()))
        self.assertNotIn(secret, str(self.store.pending_escalation_notifications()))
        self.assertEqual(self.store.pending_escalation_notifications()[0]["message"],
                         "Sensitive escalation details were redacted before delivery.")

    def test_stagnation_samples_are_transactional_and_idempotent(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", ttl_seconds=1000, now=100)
        task = TaskRecord("TASK-10", "No progress", TaskState.IN_PROGRESS,
                          "worker-a", "medium", ("src/**",))
        self.store.seed_task(lease, task, event_id="seed-health", now=101)
        first = self.store.evaluate_stagnation(lease, task.task_id, StagnationSample(False, 0),
                                               event_id="health-1", now=102)
        duplicate = self.store.evaluate_stagnation(lease, task.task_id, StagnationSample(False, 0),
                                                   event_id="health-1", now=103)
        self.assertEqual(first, duplicate)
        self.assertEqual(first["action"], "none")
        self.store.evaluate_stagnation(lease, task.task_id, StagnationSample(False, 0),
                                       event_id="health-2", now=104)
        action = self.store.evaluate_stagnation(lease, task.task_id, StagnationSample(False, 0),
                                                event_id="health-3", now=105)
        self.assertEqual(action["action"], "redispatch")
        urgent = self.store.evaluate_stagnation(lease, task.task_id, StagnationSample(False, 2),
                                                event_id="health-4", policy={"denial_ticks": 2}, now=106)
        self.assertEqual(urgent["action"], "redispatch")
        escalated = self.store.evaluate_stagnation(
            lease, task.task_id, StagnationSample(False, 2), event_id="health-5",
            policy={"denial_ticks": 2}, now=107)
        self.assertEqual(escalated["action"], "escalate")

    def test_same_instance_cannot_reacquire_or_impersonate_lease(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        with self.assertRaises(LeaseError):
            self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=101)
        forged = type(lease)(lease.system_id, lease.instance_id, lease.generation,
                             lease.expires_at, "not-the-capability")
        with self.assertRaises(LeaseError):
            self.store.record_event(forged, "forged", {}, now=101)

    def test_task_transition_and_event_commit_atomically(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-1", "A task", TaskState.PENDING, "worker-a", "medium",
                          ("src/**",), test_evidence=("python -m unittest: passed",))
        self.assertTrue(self.store.seed_task(lease, task, event_id="seed-1", now=101))
        self.assertTrue(self.store.transition_task(lease, "TASK-1", TaskState.CLAIMED,
                                                   event_id="claim-1", expected_state=TaskState.PENDING,
                                                   now=102))
        self.assertFalse(self.store.transition_task(lease, "TASK-1", TaskState.CLAIMED,
                                                    event_id="claim-1", expected_state=TaskState.PENDING,
                                                    now=103))
        self.assertEqual(self.store.get_task("TASK-1").state, TaskState.CLAIMED)
        with self.assertRaises(LeaseError):
            self.store.transition_task(lease, "TASK-1", TaskState.DONE,
                                       event_id="done-1", expected_state=TaskState.CLAIMED, now=104)

    def test_head_cannot_bypass_review_when_marking_task_done(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-3", "Reviewed task", TaskState.NEEDS_REVIEW, "worker-a",
                          "medium", ("src/**",), test_evidence=("mechanical evidence ref",))
        self.store.seed_task(lease, task, event_id="seed-3", now=101)
        with self.assertRaisesRegex(LeaseError, "typed review"):
            self.store.transition_task(lease, "TASK-3", TaskState.DONE,
                                       event_id="done-3", expected_state=TaskState.NEEDS_REVIEW,
                                       now=102)

    def test_task_archive_is_completed_only_lossless_and_idempotent(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        active = TaskRecord("TASK-12", "Still active", TaskState.IN_PROGRESS,
                            "worker-a", "medium", ("src/**",))
        completed = TaskRecord("TASK-13", "Done", TaskState.DONE,
                               "worker-b", "low", ("docs/**",), acceptance_criteria=("saved",))
        self.store.seed_task(lease, active, event_id="seed-active", now=101)
        self.store.seed_task(lease, completed, event_id="seed-done", now=102)
        with self.assertRaisesRegex(LeaseError, "only completed"):
            self.store.archive_completed_task(lease, active.task_id,
                                              event_id="archive-active", now=103)
        self.assertTrue(self.store.archive_completed_task(lease, completed.task_id,
                                                          event_id="archive-done", now=103))
        self.assertFalse(self.store.archive_completed_task(lease, completed.task_id,
                                                           event_id="archive-again", now=104))
        self.assertEqual(self.store.archived_tasks(), [completed])

    def test_task_transition_rejects_stale_state_and_missing_test_evidence(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-2", "A task", TaskState.IN_PROGRESS, "worker-a", "medium", ("src/**",))
        self.store.seed_task(lease, task, event_id="seed-2", now=101)
        with self.assertRaises(LeaseError):
            self.store.transition_task(lease, "TASK-2", TaskState.NEEDS_REVIEW,
                                       event_id="review-2", expected_state=TaskState.IN_PROGRESS, now=102)

    def test_head_assignment_is_atomic_active_only_and_snapshots_maker(self):
        lease = self.store.acquire_head("CODEXDEVTEAM", "instance-a", now=100)
        task = TaskRecord("TASK-11", "Assign", TaskState.PENDING, None,
                          "medium", ("src/one/**",))
        self.store.seed_task(lease, task, event_id="seed-assign", now=101)
        registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["builder"], "head_candidate": None,
            "defined": {"builder": {"role": "implementation", "capability_floor": "standard",
                                      "runtime": "codex", "model": "worker-model"}}
        })
        claimed = self.store.assign_pending_task(lease, task.task_id, "builder", registry,
                                                 event_id="assign-11", now=102)
        self.assertEqual(claimed.state, TaskState.CLAIMED)
        self.assertEqual(claimed.maker_identity["model"], "worker-model")
        duplicate = self.store.assign_pending_task(lease, task.task_id, "builder", registry,
                                                   event_id="assign-11", now=103)
        self.assertEqual(duplicate, claimed)


class TaskProtocolTests(unittest.TestCase):
    def make_task(self, task_id="TASK-1", state=TaskState.PENDING, paths=("src/a.py",), depends=()):
        return TaskRecord(task_id, "Example", state, "worker-a", "medium", paths,
                          depends_on=depends)

    def test_task_record_round_trips_through_versioned_dict(self):
        from dataclasses import replace
        task = replace(self.make_task(state=TaskState.IN_PROGRESS, paths=("src/**",)),
                       task_class="complex")
        self.assertEqual(TaskRecord.from_dict(task.to_dict()), task)

    def test_unknown_version_and_path_traversal_are_rejected(self):
        data = self.make_task().to_dict()
        data["protocol_version"] = 999
        with self.assertRaisesRegex(ValueError, "unsupported task protocol"):
            TaskRecord.from_dict(data)
        with self.assertRaises(ValueError):
            self.make_task(paths=("../outside",))

    def test_protected_grants_must_be_a_subset_of_owned_paths(self):
        self.assertTrue(grant_within_owned("hooks/lib.js", ("hooks/**",)))
        self.assertFalse(grant_within_owned("scripts/**", ("scripts/one.py",)))
        with self.assertRaisesRegex(ValueError, "outside owned_paths"):
            TaskRecord("TASK-8", "Bad grant", TaskState.PENDING, "worker-a", "medium",
                       ("src/**",), protected_grants=("docs/**",))

    def test_task_set_reports_missing_dependencies_and_overlaps(self):
        first = self.make_task("TASK-1", TaskState.IN_PROGRESS, ("src/**",))
        second = self.make_task("TASK-2", TaskState.CLAIMED, ("src/pkg/file.py",), ("TASK-9",))
        findings = validate_task_set([first, second])
        self.assertTrue(any("missing task TASK-9" in item for item in findings))
        self.assertTrue(any("territory overlaps" in item for item in findings))

    def test_failure_ownership_must_reference_an_open_distinct_task(self):
        owner = TaskRecord("TASK-50", "Repair baseline", TaskState.IN_PROGRESS,
                           "worker", "high", ("src/**",))
        dependent = TaskRecord("TASK-51", "Carry inherited failure", TaskState.PENDING,
                               None, "medium", ("docs/**",), owns_failures=("TASK-50",))
        self.assertEqual(validate_task_set((owner, dependent)), [])
        missing_owner = TaskRecord("TASK-52", "Missing owner", TaskState.PENDING,
                                   None, "low", ("misc/**",), owns_failures=("TASK-99",))
        self.assertTrue(any("missing task TASK-99" in finding
                            for finding in validate_task_set((owner, missing_owner))))
        completed_owner = TaskRecord("TASK-53", "Completed owner", TaskState.DONE,
                                     "worker", "low", ("old/**",))
        refers_to_done = TaskRecord("TASK-54", "Stale owner", TaskState.PENDING,
                                    None, "low", ("new/**",), owns_failures=("TASK-53",))
        self.assertTrue(any("completed task TASK-53" in finding
                            for finding in validate_task_set((completed_owner, refers_to_done))))

    def test_disjoint_literal_territories_do_not_conflict(self):
        first = self.make_task("TASK-1", TaskState.IN_PROGRESS, ("src/a.py",))
        second = self.make_task("TASK-2", TaskState.CLAIMED, ("src/b.py",))
        self.assertEqual(validate_task_set([first, second]), [])

    def test_markdown_plan_projects_fields_without_rewriting_source(self):
        source = """# Plan

### TASK-017
**Title:** Add the entry point
**Status:** in_progress
**Assigned_To:** CX
**Priority:** high
**Task_Class:** critical
**Owned_Paths:** src/main.py, tests/main_test.py (new)
**Protected_Grants:** tests/main_test.py
**Depends_On:** TASK-016
**Acceptance_Criteria:**
- [ ] App starts
- [x] No provider literal in policy
**Test_Evidence:**
- `python -m unittest` — 4 passed
"""
        parsed = parse_plan_markdown(source)
        self.assertEqual(parsed.findings, ())
        self.assertEqual(len(parsed.tasks), 1)
        task = parsed.tasks[0]
        self.assertEqual(task.task_id, "TASK-017")
        self.assertEqual(task.owned_paths, ("src/main.py", "tests/main_test.py"))
        self.assertEqual(task.task_class, "critical")
        self.assertEqual(task.depends_on, ("TASK-016",))
        self.assertEqual(task.acceptance_criteria[0], "App starts")

    def test_plan_state_patch_is_hash_bound_surgical_and_preserves_crlf_unknown_fields(self):
        source = ("# Project\r\n\r\n### TASK-70\r\n**Title:** Keep this\r\n"
                  "**Status:** pending\r\n**Assigned_To:** —\r\n**Owned_Paths:** src/**\r\n"
                  "**Custom_Field:** preserve this\r\n\r\n### TASK-71\r\n**Keep:** untouched\r\n")
        expected_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        updated, digest = patch_plan_task_state(
            source, task_id="TASK-70", state=TaskState.CLAIMED,
            assigned_worker="builder.a", expected_sha256=expected_hash)
        self.assertIn("**Status:** claimed\r\n**Assigned_To:** builder.a\r\n", updated)
        self.assertIn("**Custom_Field:** preserve this\r\n", updated)
        self.assertTrue(updated.endswith("### TASK-71\r\n**Keep:** untouched\r\n"))
        self.assertEqual(digest, hashlib.sha256(updated.encode("utf-8")).hexdigest())
        with self.assertRaisesRegex(PlanWriteConflict, "changed"):
            patch_plan_task_state(source, task_id="TASK-70", state=TaskState.CLAIMED,
                                  assigned_worker="builder.a", expected_sha256="0" * 64)
        with self.assertRaisesRegex(PlanWriteConflict, "found 0"):
            patch_plan_task_state(source, task_id="TASK-99", state=TaskState.CLAIMED,
                                  assigned_worker="builder.a", expected_sha256=expected_hash)

    def test_malformed_markdown_task_is_reported_not_silently_dropped(self):
        parsed = parse_plan_markdown("### TASK-1\n**Title:** Missing fields\n")
        self.assertEqual(parsed.tasks, ())
        self.assertTrue(any("TASK-1" in finding for finding in parsed.findings))


class PlanArchiveTests(unittest.TestCase):
    @staticmethod
    def source(old_updated="2026-04-03T12:00:00Z"):
        archived = (
            "### TASK-OLD\n**Title:** Wave A completed work\n**Status:** done\n"
            f"**Updated_At:** {old_updated}\n**Private_Field:** preserve exactly\n\n"
        )
        active = (
            "### TASK-CURRENT\n**Title:** Wave B ongoing work\n**Status:** pending\n"
            "**Assigned_To:** —\n**Priority:** medium\n**Owned_Paths:** src/current.py\n"
            "**Depends_On:** TASK-OLD\n**Updated_At:** 2026-09-01T00:00:00Z\n"
        )
        done_current = (
            "\n### TASK-DONE-CURRENT\n**Title:** Wave B done but keep current wave\n"
            "**Status:** done\n**Assigned_To:** —\n**Priority:** medium\n"
            "**Owned_Paths:** src/current.py\n**Updated_At:** 2026-09-02T00:00:00Z\n"
        )
        return "# Project plan\n\n" + archived + active + done_current

    def test_archived_stub_preserves_dependency_satisfaction(self):
        source = self.source()
        result = plan_archive(source)
        self.assertTrue(result.changed)
        self.assertEqual([item.task_id for item in result.blocks], ["TASK-OLD"])
        self.assertIn("**Archived:** plan/archive/2026-04.md", result.text)
        self.assertIn("### TASK-DONE-CURRENT", result.text)
        self.assertNotIn("Private_Field", result.text)
        parsed = parse_plan_markdown(result.text)
        self.assertEqual(parsed.findings, ())
        self.assertEqual(parsed.archived_task_ids, ("TASK-OLD",))
        self.assertEqual(validate_task_set(
            parsed.tasks, archived_task_ids=parsed.archived_task_ids), [])

    def test_archived_task_cannot_own_inherited_failures(self):
        active = TaskRecord("TASK-OPEN", "Open work", TaskState.PENDING,
                            None, "medium", ("src/open.py",),
                            owns_failures=("TASK-OLD",))
        findings = validate_task_set([active], archived_task_ids=["TASK-OLD"])
        self.assertTrue(any("completed task TASK-OLD" in finding for finding in findings))

    def test_missing_or_invalid_archive_date_leaves_task_in_plan(self):
        result = plan_archive(self.source("2026-13-40T00:00:00Z"))
        self.assertFalse(result.changed)
        self.assertIn("TASK-OLD", result.text)
        self.assertTrue(any("invalid Updated_At" in finding for finding in result.findings))

    def test_duplicate_task_ids_and_duplicate_stub_fields_fail_closed(self):
        source = self.source()
        duplicate = source + source.split("### TASK-CURRENT", 1)[0].split("# Project plan\n\n", 1)[1]
        result = plan_archive(duplicate)
        self.assertFalse(result.changed)
        self.assertTrue(any("duplicate task ID" in finding for finding in result.findings))
        parsed = parse_plan_markdown(
            "### TASK-OLD\n**Status:** pending\n**Status:** done\n"
            "**Archived:** plan/archive/2026-04.md\n"
        )
        self.assertTrue(any("duplicate fields" in finding for finding in parsed.findings))

    def test_append_is_exact_idempotent_and_detects_conflicting_existing_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source()
            first = plan_archive(source)
            written = append_archive_blocks(root, first)
            self.assertEqual(written, ("plan/archive/2026-04.md",))
            archive_path = root / "plan" / "archive" / "2026-04.md"
            archive_text = archive_path.read_bytes().decode("utf-8")
            self.assertEqual(read_archived_block(archive_text, "TASK-OLD"),
                             first.blocks[0].block)
            self.assertEqual(append_archive_blocks(root, first), ())
            changed = plan_archive(source.replace("Private_Field:** preserve", "Private_Field:** changed"))
            with self.assertRaisesRegex(ArchiveConflict, "differs from PLAN"):
                append_archive_blocks(root, changed)

    def test_head_archival_projects_plan_and_recovers_interrupted_replace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            plan = root / "PLAN.md"
            plan.write_text(self.source(), encoding="utf-8", newline="")
            store = StateStore(Path(temp) / "state.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
            done = TaskRecord("TASK-OLD", "Wave A completed work", TaskState.DONE,
                              "worker-a", "medium", ("src/old.py",))
            store.seed_task(lease, done, event_id="seed-archive-old", now=101)
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            with patch("codexdevteam_kernel.state._atomic_replace_plan",
                       side_effect=OSError("interrupted PLAN archival")):
                with self.assertRaisesRegex(PlanProjectionPending, "interrupted PLAN archival"):
                    store.archive_older_plan_tasks(
                        lease, root, event_id="archive-plan-wave-a",
                        expected_plan_sha256=expected, now=102,
                    )
            archive_path = root / "plan" / "archive" / "2026-04.md"
            self.assertTrue(archive_path.exists())
            self.assertEqual([task.task_id for task in store.archived_tasks()], ["TASK-OLD"])
            self.assertEqual(len(store.pending_plan_projections()), 1)
            self.assertEqual(store.apply_pending_plan_projections(lease, now=103),
                             ("archive-plan-wave-a",))
            projected = plan.read_text(encoding="utf-8")
            self.assertIn("**Archived:** plan/archive/2026-04.md", projected)
            self.assertNotIn("**Private_Field:** preserve exactly", projected)
            self.assertEqual(store.pending_plan_projections(), ())
            self.assertEqual(store.archive_older_plan_tasks(
                lease, root, event_id="archive-plan-wave-a",
                expected_plan_sha256=expected, now=104,
            ), ("TASK-OLD",))

    def test_plan_archive_rechecks_lease_after_slow_archive_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            plan = root / "PLAN.md"
            plan.write_text(self.source(), encoding="utf-8", newline="")
            store = StateStore(Path(temp) / "state.sqlite")
            started = time.time()
            lease = store.acquire_head("CODEXDEVTEAM", "short-head",
                                       ttl_seconds=0.1, now=started)
            done = TaskRecord("TASK-OLD", "Wave A completed work", TaskState.DONE,
                              "worker-a", "medium", ("src/old.py",))
            store.seed_task(lease, done, event_id="seed-archive-expiry", now=started + 0.001)
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            actual_append = append_archive_blocks

            def slow_append(*args, **kwargs):
                result = actual_append(*args, **kwargs)
                time.sleep(0.12)
                return result

            with patch("codexdevteam_kernel.state.append_archive_blocks",
                       side_effect=slow_append):
                with self.assertRaisesRegex(LeaseError, "lease is absent, expired"):
                    store.archive_older_plan_tasks(
                        lease, root, event_id="archive-plan-expired",
                        expected_plan_sha256=expected,
                    )
            self.assertEqual(store.archived_tasks(), [])
            self.assertEqual(store.pending_plan_projections(), ())

    def test_notes_rotation_preserves_frontmatter_and_appends_once(self):
        notes = "Long operational note; " * 30
        source = ("---\r\nplan_version: 1\r\norchestrator_notes: "
                  + json.dumps(notes) + "\r\nother_setting: preserve\r\n---\r\n\r\n# Plan\r\n")
        local_time = datetime(2026, 9, 27, 0, 30, tzinfo=timezone(timedelta(hours=3)))
        rotation = plan_notes_rotation(source, now=local_time, cap=140)
        self.assertEqual(rotation.original_chars, len(notes))
        self.assertEqual(rotation.relative_path,
                         "docs/handovers/2026-09-26-notes.md")
        self.assertIn("other_setting: preserve\r\n", rotation.text)
        self.assertIn("# Plan\r\n", rotation.text)
        self.assertIn("docs/handovers/2026-09-26-notes.md", rotation.text)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(append_notes_rotation(root, rotation),
                             (rotation.relative_path,))
            archive = root / rotation.relative_path
            before = archive.read_bytes()
            self.assertEqual(append_notes_rotation(root, rotation), ())
            self.assertEqual(archive.read_bytes(), before)
            damaged = before.replace(notes[:20].encode(), b"X" + notes[1:20].encode(), 1)
            archive.write_bytes(damaged)
            with self.assertRaisesRegex(ArchiveConflict, "differs from planned body"):
                append_notes_rotation(root, rotation)

    def test_notes_rotation_noop_and_invalid_configuration(self):
        source = "---\nplan_version: 1\norchestrator_notes: short\n---\n"
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)
        result = plan_notes_rotation(source, now=now, cap=10)
        self.assertIsNone(result.relative_path)
        self.assertEqual(result.text, source)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            plan_notes_rotation(source, now=datetime(2026, 9, 27), cap=1)
        with self.assertRaisesRegex(ArchiveConflict, "no YAML frontmatter"):
            plan_notes_rotation("# no frontmatter\n", now=now, cap=1)


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Kernel Test")
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("local/config.json\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("add", ".gitignore")
        self.git("commit", "-m", "initial")
        (self.root / "local").mkdir()
        (self.root / "local" / "config.json").write_text("secret-one\n", encoding="utf-8")
        (self.root / "local" / "normal.json").write_text("not ignored\n", encoding="utf-8")
        self.manager = GitWorktreeManager(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout

    def test_create_inspect_and_remove_owned_worktree(self):
        info = self.manager.create("TASK-1", "task/TASK-1-worker-a", "HEAD")
        self.assertEqual(info.branch, "task/TASK-1-worker-a")
        self.assertEqual(self.manager.inspect(info.path), info)
        self.manager.remove(info.path)
        self.assertFalse(info.path.exists())

    def test_existing_unregistered_path_is_never_reused(self):
        path = self.manager.managed_root / "TASK-2"
        path.mkdir(parents=True)
        with self.assertRaises(WorktreeError):
            self.manager.create("TASK-2", "task/TASK-2-worker-a", "HEAD")

    def test_paths_outside_managed_root_cannot_be_inspected_or_removed(self):
        with self.assertRaises(WorktreeError):
            self.manager.remove(self.root)

    def test_dirty_worktree_is_not_force_removed(self):
        info = self.manager.create("TASK-3", "task/TASK-3-worker-a", "HEAD")
        (info.path / "unfinished.txt").write_text("preserve me\n", encoding="utf-8")
        with self.assertRaises(WorktreeError):
            self.manager.remove(info.path)
        self.assertTrue((info.path / "unfinished.txt").exists())

    def test_configured_ignored_files_copy_on_create_refresh_and_remove(self):
        info = self.manager.create("TASK-4", "task/TASK-4-worker-a", "HEAD",
                                   worktree_copy=("local/config.json",))
        destination = info.path / "local" / "config.json"
        self.assertEqual(destination.read_text(encoding="utf-8"), "secret-one\n")
        (self.root / "local" / "config.json").write_text("secret-two\n", encoding="utf-8")
        refreshed = self.manager.create("TASK-4", "task/TASK-4-worker-a", "HEAD",
                                        worktree_copy=("local/config.json",))
        self.assertEqual(refreshed, info)
        self.assertEqual(destination.read_text(encoding="utf-8"), "secret-two\n")
        self.assertEqual(self.manager._run("-C", str(info.path), "status", "--porcelain").strip(), "")
        self.manager.remove(info.path, worktree_copy=("local/config.json",))
        self.assertFalse(info.path.exists())

    def test_worktree_copy_refuses_nonignored_missing_and_unsafe_paths(self):
        info = self.manager.create("TASK-5", "task/TASK-5-worker-a", "HEAD")
        with self.assertRaisesRegex(WorktreeError, "not gitignored"):
            self.manager.copy_worktree_files(info.path, ("local/normal.json",))
        with self.assertRaisesRegex(WorktreeError, "missing or not a file"):
            self.manager.copy_worktree_files(info.path, ("local/missing.json",))
        with self.assertRaisesRegex(WorktreeError, "unsafe"):
            self.manager.copy_worktree_files(info.path, ("../outside.json",))
        self.assertFalse((info.path / "local" / "normal.json").exists())
        self.manager.remove(info.path)

    def test_create_copy_failure_removes_only_the_new_clean_worktree(self):
        path = self.manager.managed_root / "TASK-6"
        with self.assertRaisesRegex(WorktreeError, "not gitignored"):
            self.manager.create("TASK-6", "task/TASK-6-worker-a", "HEAD",
                                worktree_copy=("local/normal.json",))
        self.assertFalse(path.exists())
        self.assertNotIn(self.manager._key(path), self.manager._registered())


class SecretScannerTests(unittest.TestCase):
    def test_common_credential_patterns_return_labels_only(self):
        synthetic = "token=\"sk-" + "A" * 24 + "\""
        hits = find_secrets(synthetic)
        self.assertIn("Anthropic/OpenAI-style API key", hits)
        self.assertNotIn("A" * 24, hits)

    def test_redacted_examples_do_not_match(self):
        self.assertEqual(find_secrets("YOUR_KEY_HERE and password=YOUR_PASSWORD"), ())


class EvidenceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = EvidenceMemory(Path(self.temp.name) / "atlas.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def add_hot_file(self, fact_id="fact-ports", project="project-a", now=100):
        return self.memory.add_fact(
            fact_id=fact_id, kind="hot_file", scope="project", subject="src/ports.py",
            statement="Check import boundaries when changing the shared ports module.",
            evidence=("review:REVIEW-14", "commit:" + "a" * 40),
            origin_project=project, now=now,
        )

    def test_rendered_injection_is_cited_and_bound_to_its_task(self):
        from codexdevteam_kernel.memory import render_fact_injection
        self.add_hot_file()
        injection = self.memory.retrieve_for_task(
            event_id="render-injection", task_id="TASK-RENDER", project="project-a",
            owned_paths=("src/**",), now=101)
        rendered = render_fact_injection(injection, task_id="TASK-RENDER")
        self.assertIn("advisory", rendered)
        self.assertIn("review:REVIEW-14", rendered)
        with self.assertRaisesRegex(ValueError, "bound to the current task"):
            render_fact_injection(injection, task_id="TASK-OTHER")

    def test_test_command_miner_uses_registered_artifact_and_retrieves_by_task_evidence(self):
        from codexdevteam_kernel.miners import mine_passing_test_facts
        temp_root = Path(self.temp.name) / "miner-project"
        temp_root.mkdir()
        for args in (("init",), ("config", "user.email", "test@example.invalid"),
                     ("config", "user.name", "Kernel Test")):
            subprocess.run(["git", "-C", str(temp_root), *args], check=True,
                           capture_output=True)
        (temp_root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(temp_root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(temp_root), "commit", "-m", "base"], check=True,
                       capture_output=True)
        sha = subprocess.run(["git", "-C", str(temp_root), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
        task = TaskRecord("TASK-MINER", "Wire the service", TaskState.IN_PROGRESS,
                          "builder", "medium", ("src/**",))
        result = GateRunner(temp_root).run_test_command(
            task, [sys.executable, "-c", "print('passed')"], worktree=temp_root,
            expected_sha=sha, name="test_full")
        store = StateStore(Path(self.temp.name) / "miner-state.sqlite")
        lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
        store.seed_task(lease, task, event_id="seed-miner-task", now=101)
        store.register_test_run(lease, task.task_id, result,
                                event_id="register-miner-test", now=102)
        facts = mine_passing_test_facts(self.memory, store, project="miner-project")
        self.assertEqual(len(facts), 1)
        self.assertEqual((facts[0].kind, facts[0].subject), ("test_command", "test_full"))
        self.assertIn("state-event:register-miner-test", facts[0].evidence)
        task_with_evidence = TaskRecord(
            task.task_id, task.title, task.state, task.assigned_worker, task.priority,
            task.owned_paths, test_evidence=(result.evidence_ref,))
        injection = self.memory.retrieve_for_task_record(
            event_id="miner-test-injection", task=task_with_evidence,
            project="miner-project", now=103)
        self.assertEqual([fact.fact_id for fact in injection.facts], [facts[0].fact_id])
        result_log = Path(result.log_path)
        result_log.write_text("changed after registration", encoding="utf-8")
        self.assertEqual(store.verified_test_run_events(), [])

    def test_hot_file_miner_requires_repeated_verified_exact_path_conflicts(self):
        from codexdevteam_kernel.miners import mine_hot_file_facts

        class Source:
            def __init__(self, events):
                self.events = events

            def verified_test_run_events(self):
                return []

            def verified_territory_conflict_events(self):
                return self.events

        def event(event_id, created_at, subject_path="src/shared.py"):
            return {"event_id": event_id, "created_at": created_at, "payload": {
                "type": "territory.conflict", "task_id": "TASK-X", "conflicts": [
                    {"other_task_id": "TASK-A", "attempted_path": subject_path,
                     "existing_path": subject_path, "subject_path": subject_path}]} }

        self.assertEqual(mine_hot_file_facts(
            self.memory, Source([event("one", 90)]), project="project-a", now=100), ())
        malformed = [
            {"event_id": "bad-time", "created_at": "yesterday", "payload": {
                "type": "territory.conflict", "conflicts": [
                    {"subject_path": "src/shared.py"}]}},
            {"event_id": "missing-time", "payload": {
                "type": "territory.conflict", "conflicts": [
                    {"subject_path": "src/shared.py"}]}},
            {"event_id": "boolean-time", "created_at": True, "payload": {
                "type": "territory.conflict", "conflicts": [
                    {"subject_path": "src/shared.py"}]}},
        ]
        self.assertEqual(mine_hot_file_facts(
            self.memory, Source(malformed), project="project-a", now=100), ())
        facts = mine_hot_file_facts(
            self.memory, Source([event("one", 90), event("two", 95)]),
            project="project-a", now=100)
        self.assertEqual(len(facts), 1)
        self.assertEqual((facts[0].kind, facts[0].subject), ("hot_file", "src/shared.py"))
        self.assertEqual(facts[0].evidence, ("state-event:one", "state-event:two"))
        repeated = mine_hot_file_facts(
            self.memory, Source([event("one", 90), event("two", 95)]),
            project="project-a", now=100)
        self.assertEqual(repeated, facts)

    def test_head_records_repeat_territory_denials_for_hot_file_mining(self):
        from codexdevteam_kernel.miners import mine_hot_file_facts

        store = StateStore(Path(self.temp.name) / "conflicts.sqlite")
        lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
        active = TaskRecord("TASK-ACTIVE", "Own shared file", TaskState.CLAIMED,
                            "builder", "medium", ("src/shared.py",))
        store.seed_task(lease, active, event_id="seed-active-conflict", now=101)
        pending = [TaskRecord(f"TASK-PENDING-{suffix}", "Edit shared file", TaskState.PENDING,
                              None, "medium", ("src/shared.py",)) for suffix in ("B", "C")]
        for task in pending:
            store.seed_task(lease, task, event_id="seed-" + task.task_id, now=102)
        registry = WorkerRegistry.from_dict({
            "protocol_version": 1, "active": ["builder-b", "builder-c"], "head_candidate": None,
            "defined": {worker: {"role": "implementation", "capability_floor": "standard",
                                 "runtime": "codex", "model": "configured-model"}
                        for worker in ("builder-b", "builder-c")},
        })
        for index, task in enumerate(pending):
            with self.assertRaisesRegex(LeaseError, "territory conflicts"):
                store.assign_pending_task(lease, task.task_id, f"builder-{('b', 'c')[index]}", registry,
                                          event_id=f"assign-conflict-{index}", now=103 + index)
        events = store.verified_territory_conflict_events()
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event["payload"]["conflicts"][0]["subject_path"] == "src/shared.py"
                            for event in events))
        facts = mine_hot_file_facts(self.memory, store, project="project-a", now=110)
        self.assertEqual(len(facts), 1)
        self.assertEqual(set(facts[0].evidence),
                         {f"state-event:{event['event_id']}" for event in events})

    def test_review_miner_uses_only_repeated_verified_in_territory_path_citations(self):
        from codexdevteam_kernel.miners import mine_review_catch_facts

        class Source:
            def verified_review_events(self):
                return [
                    {"event_id": "review-one", "created_at": 90, "task_owned_paths": ["src/**"],
                     "payload": {"type": "task.reviewed", "decision": "changes_requested",
                                 "evidence_refs": ["file:src/repeated.py", "file:../escape"]}},
                    {"event_id": "review-two", "created_at": 95, "task_owned_paths": ["src/**"],
                     "payload": {"type": "task.reviewed", "decision": "changes_requested",
                                 "evidence_refs": ["file:src/repeated.py"]}},
                    {"event_id": "review-three", "created_at": 96, "task_owned_paths": ["src/**"],
                     "payload": {"type": "task.reviewed", "decision": "approved",
                                 "evidence_refs": ["file:src/approved.py"]}},
                    {"event_id": "review-four", "created_at": 97, "task_owned_paths": ["src/**"],
                     "payload": {"type": "task.reviewed", "decision": "changes_requested",
                                 "evidence_refs": ["file:docs/outside.md"]}},
                ]

        facts = mine_review_catch_facts(self.memory, Source(), project="project-a", now=100)
        self.assertEqual(len(facts), 1)
        self.assertEqual((facts[0].kind, facts[0].subject),
                         ("review_catch", "src/repeated.py"))
        self.assertEqual(facts[0].evidence,
                         ("state-event:review-one", "state-event:review-two"))

    def test_gate_miner_counts_distinct_verified_attempts_and_new_failures_only(self):
        from codexdevteam_kernel.miners import mine_gate_history_facts

        class Source:
            def verified_gate_attempt_events(self):
                return [
                    {"event_id": "gate-one", "created_at": 90, "payload": {
                        "type": "gate.attempted", "task_id": "TASK-A", "sha": "a" * 40,
                        "fingerprint": "fp-1", "checks": {"test_full": "failed"},
                        "new_failures": ["test_full"]}},
                    {"event_id": "gate-replay", "created_at": 91, "payload": {
                        "type": "gate.attempted", "task_id": "TASK-A", "sha": "a" * 40,
                        "fingerprint": "fp-1", "checks": {"test_full": "failed"},
                        "new_failures": ["test_full"]}},
                    {"event_id": "gate-two", "created_at": 95, "payload": {
                        "type": "gate.attempted", "task_id": "TASK-B", "sha": "b" * 40,
                        "fingerprint": "fp-2", "checks": {"test_full": "failed"},
                        "new_failures": ["test_full"]}},
                    {"event_id": "gate-inherited", "created_at": 96, "payload": {
                        "type": "gate.attempted", "task_id": "TASK-C", "sha": "c" * 40,
                        "fingerprint": "fp-3", "checks": {"build": "failed"},
                        "new_failures": []}},
                ]

        facts = mine_gate_history_facts(self.memory, Source(), project="project-a", now=100)
        self.assertEqual(len(facts), 1)
        self.assertEqual((facts[0].kind, facts[0].subject),
                         ("spec_gotcha", "gate-check:test_full"))
        self.assertEqual(facts[0].evidence, ("state-event:gate-one", "state-event:gate-two"))

    def test_fact_creation_requires_citations_and_rejects_secrets(self):
        args = {"fact_id": "fact-invalid", "kind": "review_catch", "scope": "project",
                "subject": "TASK-1", "statement": "Review finding",
                "origin_project": "project-a", "now": 100}
        with self.assertRaisesRegex(ValueError, "evidence citations"):
            self.memory.add_fact(**args, evidence=())
        with self.assertRaisesRegex(ValueError, "secret-like"):
            self.memory.add_fact(**{**args, "statement": "token=sk-" + "A" * 24},
                                 evidence=("review:1",))

    def test_retrieval_respects_project_scope_path_match_and_budget(self):
        self.add_hot_file()
        self.add_hot_file("fact-other-project", project="project-b")
        self.memory.add_fact(
            fact_id="fact-unit", kind="unit_tendency", scope="project", subject="builder-1",
            statement="This unit needs test output summarized by failure category.",
            evidence=("ledger:run-8",), origin_project="project-a", now=101,
        )
        result = self.memory.retrieve_for_task(
            event_id="inject-task-1", task_id="TASK-1", project="project-a",
            owned_paths=("src/**",), subjects=("builder-1",), limit=5, token_budget=600,
            now=102,
        )
        self.assertEqual({fact.fact_id for fact in result.facts}, {"fact-ports", "fact-unit"})
        self.assertEqual(self.memory.get_fact("fact-ports").injections, 1)
        repeated = self.memory.retrieve_for_task(
            event_id="inject-task-1", task_id="TASK-1", project="project-a",
            owned_paths=("src/**",), subjects=("builder-1",), limit=5, token_budget=600,
            now=102,
        )
        self.assertEqual({fact.fact_id for fact in repeated.facts}, {"fact-ports", "fact-unit"})
        self.assertEqual(self.memory.get_fact("fact-ports").injections, 1)
        other = self.memory.retrieve_for_task(
            event_id="inject-task-2", task_id="TASK-2", project="project-z",
            owned_paths=("docs/**",), now=103,
        )
        self.assertEqual(other.facts, ())

    def test_rework_only_scores_matching_facts_and_each_injection_once(self):
        self.add_hot_file()
        other = self.memory.add_fact(
            fact_id="fact-other", kind="review_catch", scope="project", subject="src/ports.py",
            statement="Record the error code when changing ports.", evidence=("review:REVIEW-22",),
            origin_project="project-a", now=100,
        )
        injection = self.memory.retrieve_for_task(
            event_id="inject-selective", task_id="TASK-3", project="project-a",
            owned_paths=("src/**",), now=101,
        )
        updated = self.memory.record_review_outcome(
            event_id="outcome-selective", injection_event_id=injection.event_id,
            outcome="rework", matching_fact_ids=("fact-ports",), now=102,
        )
        by_id = {fact.fact_id: fact for fact in updated}
        self.assertEqual(by_id["fact-ports"].losses, 1)
        self.assertEqual(self.memory.get_fact("fact-other").losses, 0)
        with self.assertRaisesRegex(ValueError, "already has a review outcome"):
            self.memory.record_review_outcome(
                event_id="outcome-selective-again", injection_event_id=injection.event_id,
                outcome="approved", now=103,
            )

    def test_repeated_losses_demote_and_probation_timeout_retires_fact(self):
        self.add_hot_file()
        injections = [self.memory.retrieve_for_task(
            event_id=f"inject-lifecycle-{index}", task_id=f"TASK-{index}",
            project="project-a", owned_paths=("src/**",), now=100 + index,
        ) for index in range(5)]
        for index, injection in enumerate(injections[:4]):
            self.memory.record_review_outcome(
                event_id=f"rework-{index}", injection_event_id=injection.event_id,
                outcome="rework", matching_fact_ids=("fact-ports",), now=110 + index,
            )
        fact = self.memory.get_fact("fact-ports")
        self.assertEqual(fact.status, "probation")
        self.assertLess(fact.confidence, 0.35)
        self.memory.record_review_outcome(
            event_id="approve-last", injection_event_id=injections[4].event_id,
            outcome="approved", now=115,
        )
        retired = self.memory.retire_expired_probation(
            now=fact.probation_at + 30 * 24 * 60 * 60)
        self.assertEqual(retired, ("fact-ports",))
        self.assertEqual(self.memory.get_fact("fact-ports").status, "retired")

    def test_pending_evidence_can_promote_probation_fact_after_repeated_wins(self):
        self.add_hot_file()
        injections = [self.memory.retrieve_for_task(
            event_id=f"inject-recovery-{index}", task_id=f"TASK-{index}",
            project="project-a", owned_paths=("src/**",), now=100 + index,
        ) for index in range(13)]
        for index, injection in enumerate(injections[:5]):
            self.memory.record_review_outcome(
                event_id=f"recovery-loss-{index}", injection_event_id=injection.event_id,
                outcome="rework", matching_fact_ids=("fact-ports",), now=120 + index,
            )
        self.assertEqual(self.memory.get_fact("fact-ports").status, "probation")
        for index, injection in enumerate(injections[5:]):
            self.memory.record_review_outcome(
                event_id=f"recovery-win-{index}", injection_event_id=injection.event_id,
                outcome="approved", now=130 + index,
            )
        recovered = self.memory.get_fact("fact-ports")
        self.assertEqual(recovered.status, "active")
        self.assertGreaterEqual(recovered.confidence, 0.6)


class UsageReportingTests(unittest.TestCase):
    def test_optional_tier_rent_requires_sample_and_meets_outcome_cost_thresholds(self):
        from codexdevteam_kernel.usage import compare_tier_rent
        events = []
        sequence = 0
        for runtime, model, task_prefix, decisions, cost in (
            ("codex", "optional", "C", (("approved",), ("approved",),
                                             ("changes_requested", "approved")), 0.02),
            ("codex", "baseline", "B", (("approved",), ("changes_requested", "approved"),
                                             ("changes_requested",)), 0.02),
        ):
            for index, task_decisions in enumerate(decisions):
                task_id = f"TASK-{task_prefix}{index}"
                sequence += 1
                events.append({"created_at": sequence, "payload": {
                    "type": "runtime.invoked", "invocation_id": f"run-{task_id}",
                    "task_id": task_id, "purpose": "maker", "runtime": runtime,
                    "model": model, "role": "implementation", "status": "succeeded",
                    "reported_cost_usd": cost,
                }})
                maker = {"unit_id": task_prefix + "-maker", "runtime": runtime, "model": model}
                for decision in task_decisions:
                    sequence += 1
                    events.append({"created_at": sequence, "payload": {
                        "type": "task.reviewed", "task_id": task_id,
                        "maker_identity": maker, "decision": decision,
                    }})
        comparison = compare_tier_rent(
            events, candidate=("codex", "optional"), baseline=("codex", "baseline"),
            minimum_tasks=3, minimum_first_pass_lift=0.2,
            maximum_cost_per_approval_ratio=1.0,
        )
        self.assertTrue(comparison.pays_rent)
        self.assertEqual(comparison.candidate.reviewed_tasks, 3)
        self.assertEqual(comparison.candidate.first_pass_approved, 2)
        self.assertEqual(comparison.baseline.first_pass_approved, 1)
        self.assertAlmostEqual(comparison.candidate.cost_per_first_pass_usd, 0.03)
        under_sampled = compare_tier_rent(
            events, candidate=("codex", "optional"), baseline=("codex", "baseline"),
            minimum_tasks=4,
        )
        self.assertIsNone(under_sampled.pays_rent)
        misses_lift = compare_tier_rent(
            events, candidate=("codex", "optional"), baseline=("codex", "baseline"),
            minimum_tasks=3, minimum_first_pass_lift=0.5,
        )
        self.assertFalse(misses_lift.pays_rent)
        unmetered = [{**event, "payload": dict(event["payload"])} for event in events]
        for event in unmetered:
            event["payload"].pop("reported_cost_usd", None)
        unknown_cost = compare_tier_rent(
            unmetered, candidate=("codex", "optional"), baseline=("codex", "baseline"),
            minimum_tasks=3,
        )
        self.assertIsNone(unknown_cost.pays_rent)
        self.assertIn("unmetered", unknown_cost.reason)

    def test_review_outcomes_are_attributed_and_first_pass_is_counted(self):
        from codexdevteam_kernel.usage import summarize_invocations
        maker = {"unit_id": "maker", "runtime": "codex", "model": "maker-model"}
        checker = {"unit_id": "reviewer", "runtime": "claude", "model": "review-model"}
        events = [
            {"created_at": 1, "payload": {"type": "runtime.invoked", "invocation_id": "m1",
             "task_id": "T1", "unit_id": "maker", "role": "implementation",
             "runtime": "codex", "model": "maker-model", "purpose": "maker",
             "status": "succeeded"}},
            {"created_at": 2, "payload": {"type": "runtime.invoked", "invocation_id": "c1",
             "task_id": "T1", "unit_id": "reviewer", "role": "judgment",
             "runtime": "claude", "model": "review-model", "purpose": "checker",
             "status": "succeeded"}},
            {"created_at": 3, "payload": {"type": "task.reviewed", "task_id": "T1",
             "maker_identity": maker, "checker": checker, "checker_invocation_id": "c1",
             "decision": "changes_requested"}},
            {"created_at": 4, "payload": {"type": "runtime.invoked", "invocation_id": "m2",
             "task_id": "T1", "unit_id": "maker", "role": "implementation",
             "runtime": "codex", "model": "maker-model", "purpose": "maker",
             "status": "succeeded"}},
            {"created_at": 5, "payload": {"type": "runtime.invoked", "invocation_id": "c2",
             "task_id": "T1", "unit_id": "reviewer", "role": "judgment",
             "runtime": "claude", "model": "review-model", "purpose": "checker",
             "status": "succeeded"}},
            {"created_at": 6, "payload": {"type": "task.reviewed", "task_id": "T1",
             "maker_identity": maker, "checker": checker, "checker_invocation_id": "c2",
             "decision": "approved"}},
            {"created_at": 7, "payload": {"type": "runtime.invoked", "invocation_id": "m3",
             "task_id": "T2", "unit_id": "maker", "role": "implementation",
             "runtime": "codex", "model": "maker-model", "purpose": "maker",
             "status": "succeeded"}},
            {"created_at": 8, "payload": {"type": "runtime.invoked", "invocation_id": "c3",
             "task_id": "T2", "unit_id": "reviewer", "role": "judgment",
             "runtime": "claude", "model": "review-model", "purpose": "checker",
             "status": "succeeded"}},
            {"created_at": 9, "payload": {"type": "task.reviewed", "task_id": "T2",
             "maker_identity": maker, "checker": checker, "checker_invocation_id": "c3",
             "decision": "approved"}},
        ]
        summaries = {(row.role, row.purpose): row for row in summarize_invocations(events)}
        maker_summary = summaries[("implementation", "maker")]
        checker_summary = summaries[("judgment", "checker")]
        self.assertEqual((maker_summary.review_changes_requested, maker_summary.review_approved,
                          maker_summary.first_pass_approved), (1, 2, 1))
        self.assertEqual((checker_summary.review_changes_requested, checker_summary.review_approved,
                          checker_summary.first_pass_approved), (1, 2, 1))

    def test_rate_config_prices_known_runs_and_keeps_unmetered_runs_visible(self):
        from codexdevteam_kernel.usage import UsageRate, summarize_invocations
        events = [
            {"payload": {"type": "runtime.invoked", "role": "implementation",
                          "runtime": "codex", "model": "configured-model", "purpose": "maker",
                          "status": "succeeded", "duration_seconds": 12.5,
                          "started_at": 100, "input_tokens": 100, "output_tokens": 50,
                          "cached_input_tokens": 25}},
            {"payload": {"type": "runtime.invoked", "role": "implementation",
                          "runtime": "codex", "model": "configured-model", "purpose": "maker",
                          "status": "timed_out", "duration_seconds": 30,
                          "started_at": 101}},
        ]
        rate = UsageRate("codex", "configured-model", 2.0, 8.0, 0.2, effective_at=90)
        result = summarize_invocations(events, rates=(rate,))[0]
        self.assertEqual(result.invocations, 2)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.timed_out, 1)
        self.assertEqual(result.input_tokens, 100)
        self.assertEqual(result.duration_seconds, 42.5)
        self.assertEqual(result.metered_invocations, 1)
        self.assertEqual(result.unmetered_invocations, 1)
        self.assertAlmostEqual(result.cost_usd, 0.000555)

    def test_store_invocation_report_uses_identity_role_and_recorded_outcomes(self):
        from codexdevteam_kernel.usage import UsageRate
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "usage.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
            result = InvocationResult(
                "usage-run", None, "head", "codex-head", "codex", "configured-head",
                "succeeded", 0, 2.0, "", "", False, "d" * 64, 101, 103,
                role="planner", input_tokens=1_000, output_tokens=500,
                cached_input_tokens=250,
            )
            self.assertTrue(store.record_invocation(lease, result, now=104))
            report = store.invocation_summary(rates=(UsageRate(
                "codex", "configured-head", 1.0, 4.0, 0.25, effective_at=0),))[0]
            self.assertEqual((report.role, report.runtime, report.model),
                             ("planner", "codex", "configured-head"))
            self.assertEqual((report.input_tokens, report.output_tokens), (1_000, 500))
            self.assertEqual(report.metered_invocations, 1)
            self.assertAlmostEqual(report.cost_usd, 0.0028125)

    def test_usage_cli_reads_state_read_only_and_renders_configured_costs(self):
        from codexdevteam_kernel.onboarding_cli import main
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = StateStore(root / "state.sqlite")
            lease = store.acquire_head("CODEXDEVTEAM", "head", now=100)
            store.record_invocation(lease, InvocationResult(
                "cli-usage-run", None, "head", "head-unit", "codex", "head-model",
                "succeeded", 0, 1.0, "", "", False, "e" * 64,
                101, 102, role="planner", input_tokens=100, output_tokens=20,
                cached_input_tokens=0,
            ), now=103)
            rates_path = root / "rates.json"
            rates_path.write_text(json.dumps([{
                "runtime": "codex", "model": "head-model",
                "input_usd_per_million": 1, "output_usd_per_million": 2,
                "effective_at": 0,
            }]), encoding="utf-8")
            output = io.StringIO()
            with patch("sys.argv", ["codexdevteam", "usage", "--state-db",
                                     str(store.path), "--rates", str(rates_path)]), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(main(), 0)
            report = json.loads(output.getvalue())[0]
            self.assertEqual(report["role"], "planner")
            self.assertEqual(report["model"], "head-model")
            self.assertEqual(report["metered_invocations"], 1)
            self.assertAlmostEqual(report["cost_usd"], 0.00014)


if __name__ == "__main__":
    unittest.main()
