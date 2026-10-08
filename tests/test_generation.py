"""Focused Wave O generation boundary regressions."""

import hashlib
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codexdevteam_kernel.generation import (
    generation_maker_note, generation_report, prepare_generation, stage_generation,
)
from codexdevteam_kernel.generation_stream import (
    PathPolicy, materialize, parse_segments,
)
from codexdevteam_kernel.identity import WorkerIdentity
from codexdevteam_kernel.installer import install_fresh_project
from codexdevteam_kernel.installer_resources import default_framework_files
from codexdevteam_kernel.protocol import TaskRecord
from codexdevteam_kernel.runtime import InvocationResult
from codexdevteam_kernel.state import StateStore
from codexdevteam_kernel.tasks import TaskState


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Generation Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email",
                        "generation@example.invalid"], check=True)
        (self.root / "SPEC.md").write_text("Implement two tiny functions.\n", encoding="utf-8")
        install_fresh_project(self.root, default_framework_files())
        cfg_path = self.root / ".codexdevteam" / "framework" / "supervisor.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["instance_id"] = "generation-test"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.manifest_path = self.root / "generation.json"
        self._write_manifest(["src/first.py", "src/second.py"])
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "seed"], check=True)
        state_db = self.root / ".codexdevteam" / "state" / "state.sqlite"
        store = StateStore(state_db)
        lease = store.acquire_head("codexdevteam", "seed", ttl_seconds=90)
        task = TaskRecord(
            "TASK-1", "Implement two fixed functions", TaskState.PENDING, None,
            "high", ("src/first.py", "src/second.py"),
            acceptance_criteria=("first returns 1", "second returns 2"),
        )
        store.seed_task(lease, task, event_id="seed-task")
        store.release_head(lease)
        self.maker = WorkerIdentity("maker", "implementation", "standard",
                                    "codex", "maker-model")
        self.checker = WorkerIdentity("checker", "reviewer", "frontier",
                                      "codex", "checker-model")
        self.bindings = SimpleNamespace(
            registry=SimpleNamespace(active_workers=lambda: [
                SimpleNamespace(identity=self.maker, control_mode="strict"),
                SimpleNamespace(identity=self.checker, control_mode="strict"),
            ], active=("maker", "checker"), defined={
                "maker": SimpleNamespace(identity=self.maker, control_mode="strict"),
                "checker": SimpleNamespace(identity=self.checker, control_mode="strict"),
            }),
            protected_paths=("PLAN.md", ".codexdevteam/**"),
            ignored_paths_allowlist=(),
        )

    def _write_manifest(self, paths):
        data = {
            "files": [{"path": path, "purpose": "A tiny complete function",
                       "exports": [("first" if "first" in path else "second")],
                       "est_lines": 5} for path in paths],
            "context": ["SPEC.md"],
            "conventions": "Python standard library only.",
            "dependencies": {},
            "tests": "python -m unittest discover -s tests",
        }
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")

    def _prepare(self):
        with patch("codexdevteam_kernel.generation.load_host_runtime",
                   return_value=self.bindings):
            return prepare_generation(self.root, "TASK-1", self.manifest_path)

    def test_parser_keeps_only_complete_nonce_delimited_files(self):
        parsed = parse_segments(
            "abc123abc123",
            ["@@OMLCP abc123abc123 FILE src/first.py\ndef first():\n    return 1\n"
             "@@OMLCP abc123abc123 END src/first.py\n"
             "@@OMLCP abc123abc123 FILE src/second.py\ndef second():\n",
             "@@OMLCP abc123abc123 FILE src/second.py\ndef second():\n    return 2\n"
             "@@OMLCP abc123abc123 END src/second.py\n@@OMLCP abc123abc123 DONE\n"],
        )
        self.assertTrue(parsed.done)
        self.assertEqual(sorted(parsed.complete_files()), ["src/first.py", "src/second.py"])
        self.assertEqual(parsed.complete_files()["src/second.py"].content,
                         "def second():\n    return 2\n")

    def test_stage_continuation_and_ready_receipt_bind_files(self):
        context = self._prepare()
        self.assertFalse(context.blockers)
        responses = [
            f"@@OMLCP {context.nonce} FILE src/first.py\ndef first():\n    return 1\n"
            f"@@OMLCP {context.nonce} END src/first.py\n",
            f"@@OMLCP {context.nonce} FILE src/second.py\ndef second():\n    return 2\n"
            f"@@OMLCP {context.nonce} END src/second.py\n"
            f"@@OMLCP {context.nonce} DONE\n",
        ]
        calls = []

        def fake_invoke(request_context, prompt, cancel):
            index = len(calls)
            calls.append(prompt)
            response = responses[index]
            return InvocationResult(
                f"generation-test-{index}", "TASK-1", "head",
                "maker", "codex", "maker-model", "succeeded", 0, 0.1,
                response, "", False, hashlib.sha256(response.encode()).hexdigest(),
                time.time(), time.time() + 0.1,
                role="implementation", input_tokens=10, output_tokens=20,
            )

        with patch("codexdevteam_kernel.generation.load_host_runtime",
                   return_value=self.bindings), patch(
                   "codexdevteam_kernel.generation.load_runtime_capacity",
                   return_value={"maker": SimpleNamespace(eligible=lambda now: True)}):
            result = stage_generation(context, takeover_confirmed=True,
                                      invoke=fake_invoke)
        self.assertEqual(result["status"], "staged")
        self.assertEqual(result["segments_this_run"], 2)
        self.assertEqual(len(calls), 2)
        self.assertIn("Remaining paths: src/second.py", calls[1])
        worktree = (self.root.parent /
                    f"{self.root.name}-codexdevteam-worktrees" / "TASK-1")
        self.assertTrue((worktree / "src" / "first.py").is_file())
        self.assertTrue((worktree / "src" / "second.py").is_file())
        self.assertIn("HOST-STAGED GENERATION",
                      generation_maker_note(context.config, context.task, self.bindings))
        report = generation_report(self.root, "TASK-1")
        self.assertEqual(report["segments"], 2)
        self.assertEqual(report["usage"]["output_tokens"], 40)
        (worktree / "src" / "first.py").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed before dispatch"):
            generation_maker_note(context.config, context.task, self.bindings)
        (worktree / "src" / "first.py").write_text(
            "def first():\n    return 1\n", encoding="utf-8", newline="\n")
        (worktree / "src" / "extra.py").write_text("unexpected = True\n", encoding="utf-8")
        with self.assertRaises(Exception):
            generation_maker_note(context.config, context.task, self.bindings)
        (worktree / "src" / "extra.py").unlink()
        ready = self.root / ".codexdevteam" / "state" / "generation" / "TASK-1" / "ready.json"
        receipt = json.loads(ready.read_text(encoding="utf-8"))
        receipt["generator"]["model"] = "unregistered-model"
        ready.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "generator no longer matches"):
            generation_maker_note(context.config, context.task, self.bindings)

    def test_out_of_territory_manifest_refuses_before_model_call(self):
        self._write_manifest(["src/first.py", "src/foreign.py"])
        context = self._prepare()
        self.assertTrue(any("outside" in blocker for blocker in context.blockers))
        with self.assertRaisesRegex(ValueError, "not eligible"):
            stage_generation(context, takeover_confirmed=True,
                             invoke=lambda *_: self.fail("model called"))

    def test_protected_path_is_never_materialized(self):
        nonce = "abc123abc123"
        parsed = parse_segments(
            nonce, [f"@@OMLCP {nonce} FILE .codexdevteam/state/secret.py\n"
                    f"x = 1\n@@OMLCP {nonce} END .codexdevteam/state/secret.py\n"
                    f"@@OMLCP {nonce} DONE\n"])
        policy = PathPolicy([".codexdevteam/state/secret.py"],
                            [".codexdevteam/state/secret.py"],
                            [".codexdevteam/state/secret.py"])
        result = materialize(parsed, policy, self.root)
        self.assertIn(".codexdevteam/state/secret.py", result.rejected)


if __name__ == "__main__":
    unittest.main()
