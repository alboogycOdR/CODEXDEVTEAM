import os
import stat
import subprocess
import tempfile
import unittest
import importlib
from pathlib import Path
from unittest.mock import patch

from codexdevteam_kernel.host_commit import (
    CommitLimits,
    QuiescenceProof,
    _check_case_aliases,
    _validate_path_form,
    host_commit,
)
from codexdevteam_kernel.protocol import TaskRecord
from codexdevteam_kernel.tasks import TaskState


class HostCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codexdevteam-host-commit-test-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.worktree = self.root / "task-worktree"
        self.repo.mkdir()
        self.git("init", "-b", "main", str(self.repo))
        self.git("-C", str(self.repo), "config", "user.name", "Fixture Author")
        self.git("-C", str(self.repo), "config", "user.email", "fixture@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "modify.txt").write_text("before\n")
        (self.repo / "src" / "delete.txt").write_text("remove me\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "unowned.txt").write_text("keep me\n")
        self.git("-C", str(self.repo), "add", "--all")
        self.git("-C", str(self.repo), "commit", "-m", "fixture base")
        self.parent = self.git("-C", str(self.repo), "rev-parse", "HEAD").strip()
        self.branch = "task/TASK-HOST-COMMIT"
        self.git("-C", str(self.repo), "worktree", "add", "-b", self.branch,
                 str(self.worktree), self.parent)
        self.task = self.make_task()
        self.proof = QuiescenceProof("windows" if os.name == "nt" else "linux",
                                     "windows_job_object" if os.name == "nt" else "linux_cgroup_v2",
                                     True, 0, "fixture")
        self.limits = CommitLimits(settle_seconds=0)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def git(*args, check=True):
        result = subprocess.run(["git", *args], capture_output=True, text=True)
        if check and result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout

    def make_task(self, owned=("src/**",)):
        return TaskRecord("TASK-HOST-COMMIT", "Host commit fixture", TaskState.IN_PROGRESS,
                          "maker", "high", owned)

    def commit(self, **overrides):
        options = {
            "repository": self.repo,
            "worktree": self.worktree,
            "task": self.task,
            "task_branch": self.branch,
            "expected_parent": self.parent,
            "invocation_id": "invoke-fixture-001",
            "quiescence": self.proof,
            "limits": self.limits,
        }
        options.update(overrides)
        return host_commit(**options)

    def head(self):
        return self.git("-C", str(self.repo), "rev-parse", f"refs/heads/{self.branch}").strip()

    def assert_refused(self, result, code):
        self.assertEqual(result.status, "refused")
        self.assertIn(code, {reason.code for reason in result.reasons})

    def test_owned_add_modify_delete_commit_exactly_one_commit(self):
        (self.worktree / "src" / "modify.txt").write_text("after\n")
        (self.worktree / "src" / "delete.txt").unlink()
        (self.worktree / "src" / "added.txt").write_text("new\n")
        result = self.commit()
        self.assertEqual(result.status, "committed", result.reasons)
        self.assertEqual(self.git("-C", str(self.repo), "rev-list", "--count",
                                  f"{self.parent}..{self.branch}").strip(), "1")
        self.assertEqual((self.worktree / "src" / "modify.txt").read_text(), "after\n")
        self.assertFalse((self.worktree / "src" / "delete.txt").exists())
        self.assertEqual((self.worktree / "src" / "added.txt").read_text(), "new\n")

    def test_primary_checkout_head_does_not_mark_task_parent_files_as_changes(self):
        # A linked worktree's HEAD may differ from the primary checkout. The
        # host snapshot's temporary index is based on the task parent, so its
        # status command must use the worktree-specific Git directory.
        self.git("-C", str(self.repo), "rm", "-r", "--", "src")
        self.git("-C", str(self.repo), "commit", "-m", "primary checkout advances without task source")
        self.assertNotEqual(self.git("-C", str(self.repo), "rev-parse", "HEAD").strip(),
                            self.parent)
        (self.worktree / "src" / "modify.txt").write_text("task-owned change\n")

        result = self.commit()

        self.assertEqual(result.status, "committed", result.reasons)
        self.assertEqual(result.paths, ("src/modify.txt",))
        self.assertEqual(self.head(), result.sha)
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "HEAD").strip(),
                         self.git("-C", str(self.repo), "rev-parse", "main").strip())

    def test_mixed_owned_and_unowned_refuses_without_partial_commit(self):
        (self.worktree / "src" / "added.txt").write_text("owned\n")
        (self.worktree / "docs" / "unowned.txt").write_text("outside\n")
        before_objects = set(self.git("-C", str(self.repo), "rev-list", "--all", "--objects").splitlines())
        result = self.commit()
        self.assert_refused(result, "OUTSIDE_TERRITORY")
        self.assertIn("docs/unowned.txt", result.paths)
        self.assertEqual(self.head(), self.parent)
        self.assertEqual(set(self.git("-C", str(self.repo), "rev-list", "--all", "--objects").splitlines()),
                         before_objects)

    def test_rename_from_unowned_to_owned_refuses_source(self):
        (self.worktree / "docs" / "unowned.txt").rename(self.worktree / "src" / "renamed.txt")
        result = self.commit()
        self.assert_refused(result, "OUTSIDE_TERRITORY")
        self.assertIn("docs/unowned.txt", result.paths)

    def test_delete_unowned_tracked_file_refuses(self):
        (self.worktree / "docs" / "unowned.txt").unlink()
        self.assert_refused(self.commit(), "OUTSIDE_TERRITORY")

    def test_ignored_owned_path_requires_allowlist(self):
        with (self.worktree / ".gitignore").open("a") as stream:
            stream.write("src/ignored.txt\n")
        (self.worktree / "src" / "ignored.txt").write_text("ignored\n")
        result = self.commit(task=self.make_task(("src/**", ".gitignore")))
        self.assert_refused(result, "IGNORED_PATH")

    @unittest.skipIf(os.name == "nt", "POSIX symlink case")
    def test_symlink_in_owned_path_refuses(self):
        (self.worktree / "src" / "link").symlink_to(self.worktree / "src" / "modify.txt")
        self.assert_refused(self.commit(), "LINK")

    @unittest.skipUnless(os.name == "nt", "Windows junction case")
    def test_junction_in_owned_path_refuses(self):
        target = self.worktree / "src" / "modify.txt"
        link = self.worktree / "src" / "junction"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target.parent)],
                       capture_output=True, check=True)
        self.assert_refused(self.commit(), "LINK")

    def test_hard_link_in_owned_path_refuses(self):
        os.link(self.worktree / "src" / "modify.txt", self.worktree / "src" / "alias.txt")
        (self.worktree / "src" / "alias.txt").write_text("different\n")
        self.assert_refused(self.commit(), "HARDLINK")

    @unittest.skipIf(os.name == "nt", "POSIX executable mode")
    def test_executable_mode_change_is_committed(self):
        path = self.worktree / "src" / "modify.txt"
        os.chmod(path, path.stat().st_mode | 0o100)
        result = self.commit()
        self.assertEqual(result.status, "committed", result.reasons)
        mode = self.git("-C", str(self.repo), "ls-tree", result.sha, "src/modify.txt").split()[0]
        self.assertEqual(mode, "100755")

    def test_casefold_path_alias_is_rejected(self):
        from codexdevteam_kernel.host_commit import _Refused
        with self.assertRaisesRegex(_Refused, "case-folded path alias"):
            _check_case_aliases((), ("src/MODIFY.txt",), {"src/modify.txt": (0o100644, "0" * 40)})

    def test_secret_in_owned_file_refuses_without_disclosing_value(self):
        (self.worktree / "src" / "secret.txt").write_text("api_key=\"supersecretvalue123456\"\n")
        result = self.commit()
        self.assert_refused(result, "SECRET")
        self.assertNotIn("supersecretvalue123456", repr(result))

    def test_oversize_file_refuses(self):
        (self.worktree / "src" / "large.txt").write_bytes(b"x" * 33)
        self.assert_refused(self.commit(limits=CommitLimits(max_file_bytes=32, settle_seconds=0)),
                            "SIZE")

    @unittest.skipUnless(os.name == "nt", "Windows path forms")
    def test_ads_and_reserved_windows_names_refuse(self):
        ads = self.worktree / "src" / "file.txt:secret"
        ads.write_text("data")
        self.assert_refused(self.commit(), "PATH_FORM")
        with self.assertRaisesRegex(Exception, "reserved Windows device"):
            _validate_path_form("src/CON.txt")
        with self.assertRaisesRegex(Exception, "8.3"):
            _validate_path_form("src/PROGRA~1.txt")

    def test_rewritten_git_pointer_refuses(self):
        pointer = self.worktree / ".git"
        original = pointer.read_bytes()
        os.chmod(pointer, stat.S_IWRITE | stat.S_IREAD)
        pointer.unlink()
        pointer.write_text("gitdir: C:/redirected/repository/.git\n")
        try:
            result = self.commit()
            self.assert_refused(result, "GITFILE_TAMPERED")
        finally:
            pointer.unlink(missing_ok=True)
            pointer.write_bytes(original)

    def test_moved_branch_refuses(self):
        # Create a second commit on the task branch after the expected parent was captured.
        self.git("-C", str(self.worktree), "-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "moved")
        moved = self.head()
        result = self.commit()
        self.assert_refused(result, "PARENT_MOVED")
        self.assertEqual(self.head(), moved)

    def test_default_branch_cannot_be_named_as_task_branch(self):
        result = self.commit(task_branch="main")
        self.assert_refused(result, "BRANCH")
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "refs/heads/main").strip(),
                         self.parent)

    def test_late_write_after_validation_refuses_before_ref_update(self):
        (self.worktree / "src" / "added.txt").write_text("initial\n")
        module = importlib.import_module("codexdevteam_kernel.host_commit")
        original = module._capture_snapshot
        calls = 0

        def mutate_on_final(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                (self.worktree / "src" / "added.txt").write_text("changed late\n")
            return original(*args, **kwargs)

        with patch("codexdevteam_kernel.host_commit._capture_snapshot", side_effect=mutate_on_final):
            result = self.commit()
        self.assert_refused(result, "LATE_WRITE_DETECTED")
        self.assertEqual(self.head(), self.parent)
        self.assertFalse((self.repo / ".git" / "worktrees" / self.worktree.name /
                          "index.lock").exists())

    def test_unverified_quiescence_refuses_before_git_objects(self):
        (self.worktree / "src" / "added.txt").write_text("new\n")
        before = set(self.git("-C", str(self.repo), "cat-file", "--batch-all-objects",
                              "--batch-check=%(objectname)").splitlines())
        proof = QuiescenceProof("linux", "posix_process_group", False, 1, "unverified")
        result = self.commit(quiescence=proof)
        self.assert_refused(result, "QUIESCENCE_UNPROVEN")
        after = set(self.git("-C", str(self.repo), "cat-file", "--batch-all-objects",
                             "--batch-check=%(objectname)").splitlines())
        self.assertEqual(before, after)
        self.assertEqual(self.head(), self.parent)

    def test_empty_worktree_returns_no_changes(self):
        result = self.commit()
        self.assertEqual(result.status, "no_changes", (result.reasons, result.paths))
        self.assertEqual(self.head(), self.parent)

    def test_hostile_git_environment_and_hook_do_not_affect_commit(self):
        marker = self.root / "hook-marker"
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        pre_commit = hooks / "pre-commit"
        if os.name == "nt":
            pre_commit = hooks / "pre-commit.exe"
        pre_commit.write_text(f"#!/bin/sh\nprintf called > '{marker.as_posix()}'\n")
        if os.name != "nt":
            pre_commit.chmod(0o755)
        (self.worktree / "src" / "added.txt").write_text("new\n")
        with patch.dict(os.environ, {"GIT_DIR": str(self.root / "redirect"),
                                     "GIT_INDEX_FILE": str(self.root / "bad-index"),
                                     "GIT_WORK_TREE": str(self.root / "redirect")}, clear=False):
            result = self.commit()
        self.assertEqual(result.status, "committed", result.reasons)
        self.assertFalse(marker.exists())

    def test_configured_clean_filter_is_refused_before_status(self):
        self.git("-C", str(self.repo), "config", "filter.evil.clean", "write-marker")
        (self.worktree / "src" / "added.txt").write_text("new\n")
        result = self.commit()
        self.assert_refused(result, "UNSAFE_GIT_CONFIG")
        self.assertEqual(self.head(), self.parent)

    def test_commit_metadata_is_fixed_and_has_invocation_trailer(self):
        (self.worktree / "src" / "added.txt").write_text("new\n")
        result = self.commit()
        self.assertEqual(result.status, "committed", result.reasons)
        raw = self.git("-C", str(self.repo), "cat-file", "-p", result.sha)
        self.assertIn("author CODEXDEVTEAM Host <host-committer@codexdevteam.invalid>", raw)
        self.assertIn("committer CODEXDEVTEAM Host <host-committer@codexdevteam.invalid>", raw)
        self.assertTrue(raw.split("\n\n", 1)[1].splitlines()[0].endswith("[TASK-HOST-COMMIT]"))
        self.assertIn("CODEXDEVTEAM-Maker-Invocation: invoke-fixture-001", raw)
        clean = self.git("-C", str(self.worktree), "status", "--porcelain=v2", "-z",
                         "--untracked-files=all", "--ignored=matching")
        self.assertEqual(clean, "")


if __name__ == "__main__":
    unittest.main()
