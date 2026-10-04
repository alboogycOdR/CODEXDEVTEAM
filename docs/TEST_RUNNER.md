# Builder test runner

The `codexdevteam-test` command runs a named, configured argv without a shell.
It requires a clean Git worktree at the full SHA supplied on the command line.
The environment is scrubbed to essential process variables plus names
explicitly allowed by configuration. Task/worktree-specific values can be
materialized from templates.
Receipts are cached below Git's common directory so the primary checkout and
linked task worktrees can reuse evidence when SHA, argv fingerprint,
environment and timeout match.

Example `.codexdevteam/project/gate.json`:

```json
{
  "version": 1,
  "allowed_environment": ["TEST_DATABASE_URL"],
  "tests": {
    "test_full": {
      "argv": ["python", "-m", "unittest", "discover", "-s", "tests"],
      "timeout_seconds": 1800,
      "per_worktree_environment": {
        "TEST_DATABASE_URL": "postgres://test/{task_id}-{worker_id}-{run_scope}"
      }
    }
  }
}
```

Run it from the installed package with the task, worker, worktree and full
commit SHA:

```text
codexdevteam-test --config .codexdevteam/project/gate.json --name test_full --task TASK-123 --worker codex-builder --worktree C:\work\TASK-123 --sha <full-git-sha>
```

The JSON response includes pass/fail, output digest, cached status, artifact
path and a `testrun:` evidence reference. The cache receipt stores an argv
fingerprint rather than raw arguments, and logs redact configured argv values,
allowed environment values, and detected credential patterns. A failed run
returns exit code 1; invalid configuration or preconditions return 2.

The command reports an `evidence_ref`; it does not write task state or
impersonate HEAD. A builder may include that reference and its tested SHA in a
CONTROL `needs_review` request. The launcher must defer CONTROL draining until
HEAD has run the configured gate for the maker worktree. `GateRunner` returns
the associated `TestRunResult` to HEAD without embedding argv in the gate
artifact; `Supervisor.finalize_maker_gate()` registers the test receipt and
passed gate under the lease, verifies the gate SHA matches the maker worktree,
then drains CONTROL. A mismatched or unregistered reference is rejected by the
normal CONTROL validator. Builders never register receipts directly.

The gate caller must provide the authoritative task snapshot, including the
exact task being gated:

```python
gate = GateRunner(project_root).run(
    task, worktree, expected_sha=sha, base_ref=base_ref,
    commands=commands, active_tasks=tuple(state_store.list_tasks()),
)
```

The gate checks changed paths against other claimed, in-progress, and
needs-review task territories. This snapshot is included in the gate
fingerprint so a changed active ownership set invalidates a cached result.
Missing or inconsistent task snapshots are rejected instead of silently
skipping the cross-task check.
