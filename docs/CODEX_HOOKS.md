# Codex write-hook adapter

The provider-neutral write policy is in `codexdevteam_kernel.firewall`.
`codexdevteam-codex-hook` adapts Codex `PreToolUse` file-edit events to it. The
hook reads a per-invocation SQLite snapshot in read-only mode. The snapshot
contains only the current task and other active tasks, with fields needed for
territory checks; unrelated task details, receipts, and event ledgers are
omitted. It is created under that worktree's Git metadata directory, injected
as `CODEXDEVTEAM_STATE_DB`, and removed after the runtime exits. Maker setup also
injects the worker/task identity and configured runtime/model values into the
Codex process environment; builders cannot set the hook's parent environment
from inside the session. Teardown removes the snapshot and its SQLite
`-journal`, `-wal`, and `-shm` sidecars, while refusing to recursively remove
unexpected non-file artifacts. Each Codex invocation also receives a fresh
invocation-specific `TEMP`/`TMP`/`TMPDIR`; the directory is removed after success, failure,
timeout, or lease-loss cancellation.

After installing the CODEXDEVTEAM package in the runtime environment, a
project can opt into the hook with `.codex/config.toml`:

```toml
[features]
hooks = true

[[hooks.PreToolUse]]
matcher = "^(apply_patch|Edit|Write)$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "codexdevteam-codex-hook"
command_windows = "codexdevteam-codex-hook"
timeout = 10
statusMessage = "Checking CODEXDEVTEAM write territory"
```

When active, the hook explicitly denies malformed events, unknown task/worker context,
unassigned or non-active tasks, paths outside Owned_Paths, Protected_Grants
violations, overlapping active ownership, path traversal, symlink escapes,
`PLAN.md`, and detected credential-like content. It never trusts a role name
as HEAD authority; authoritative state changes use the separate lease-checked
CONTROL/PLAN path.

This adapter covers structured edit tools only. Shell commands can write files
through many mechanisms, so the hook is an optional pre-write guard and
efficiency aid, not the territory security boundary. A fresh headless Codex
smoke did not produce a direct hook trace; activation for that invocation is
unverified. Do not claim that the hook blocked writes unless a nonce-bound hook
trace proves it.

The territory boundary for a supervised maker is the host commit step: after
verified Windows process quiescence, the host validates the complete worktree
against `Owned_Paths` and publishes no commit if any changed path is outside
scope. Strict worker receipts require `control_protocol`,
`host_commit_boundary`, `task_worktree_isolation`, and
`post_run_territory_gate`. A live hook may be recorded as an additional
capability, but is not required for that commit-time guarantee. The runtime
sandbox and task worktree constrain where a maker can write; they do not make
the optional hook's activation observable.

Registry validation requires a timezone-qualified ISO 8601 verification
timestamp, a non-empty evidence reference, unique capability names, and rejects
timestamps more than five minutes in the future. These schema checks do not
validate the referenced evidence; each required capability still needs
evidence from the configured runtime and host path.
