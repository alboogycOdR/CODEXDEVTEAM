import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codexdevteam_kernel.gate import GateRunner


class BaselineCleanupTests(unittest.TestCase):
    def setUp(self):
        self.parent = tempfile.TemporaryDirectory()
        self.root = Path(self.parent.name)
        subprocess.run(["git", "init", str(self.root)], capture_output=True, check=True)
        self.runner = GateRunner(self.root, self.root / "artifacts")
        self.temporary = tempfile.TemporaryDirectory(dir=self.root)
        self.baseline = Path(self.temporary.name) / "checkout"
        self.baseline.mkdir()
        (self.baseline / "residual.txt").write_text("residual")

    def tearDown(self):
        self.temporary.cleanup()
        self.parent.cleanup()

    def commands(self, registration="", list_code=0):
        return [subprocess.CompletedProcess([], 1, "", "Directory not empty"),
                subprocess.CompletedProcess([], list_code, registration, "")]

    def test_failed_git_remove_then_successful_fallback_passes(self):
        with patch("codexdevteam_kernel.gate.subprocess.run",
                   side_effect=self.commands()) as run:
            result = self.runner._cleanup_baseline(self.baseline, self.temporary)
        self.assertEqual(result.status, "passed")
        self.assertFalse(self.baseline.exists())
        self.assertFalse(Path(self.temporary.name).exists())
        self.assertEqual(run.call_args_list[0].args[0][-4:],
                         ["worktree", "remove", "--force", str(self.baseline)])
        self.assertEqual(run.call_args_list[1].args[0][-4:],
                         ["worktree", "list", "--porcelain", "-z"])

    def test_residual_files_fail_even_without_registration(self):
        with patch.object(self.temporary, "cleanup"), patch(
                "codexdevteam_kernel.gate.subprocess.run", side_effect=self.commands()):
            result = self.runner._cleanup_baseline(self.baseline, self.temporary)
        self.assertEqual(result.status, "failed")
        self.assertTrue((self.baseline / "residual.txt").exists())

    def test_residual_registration_fails_even_without_files(self):
        registration = "worktree " + self.baseline.as_posix() + "\0HEAD abc\0\0"
        with patch("codexdevteam_kernel.gate.subprocess.run",
                   side_effect=self.commands(registration)):
            result = self.runner._cleanup_baseline(self.baseline, self.temporary)
        self.assertEqual(result.status, "failed")
        self.assertFalse(self.baseline.exists())

    def test_unrelated_registration_and_directory_are_untouched(self):
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep")
        registration = "worktree " + unrelated.as_posix() + "\0HEAD abc\0\0"
        with patch("codexdevteam_kernel.gate.subprocess.run",
                   side_effect=self.commands(registration)):
            result = self.runner._cleanup_baseline(self.baseline, self.temporary)
        self.assertEqual(result.status, "passed")
        self.assertEqual((unrelated / "keep.txt").read_text(), "keep")

    def test_registration_query_failure_fails_closed(self):
        with patch("codexdevteam_kernel.gate.subprocess.run",
                   side_effect=self.commands(list_code=1)):
            result = self.runner._cleanup_baseline(self.baseline, self.temporary)
        self.assertEqual(result.status, "failed")

    def test_unrelated_cleanup_path_is_refused_before_any_action(self):
        with patch("codexdevteam_kernel.gate.subprocess.run") as run:
            result = self.runner._cleanup_baseline(self.root, self.temporary)
        self.assertEqual(result.status, "failed")
        run.assert_not_called()
        self.assertTrue(self.baseline.exists())
