# Windows host setup and operation

Run these host commands from native Windows PowerShell. Linux and WSL are not
supported for supervised builder execution.

## Prepare an inactive fresh project

1. Create a Git repository and commit a baseline before supervised
   activation. The host needs a verifiable `HEAD` as the parent for task
   worktrees and the compare-and-swap integration boundary. `host-run --brief`
   may create `PLAN.md`, but it cannot create the repository's first commit.
2. Install CODEXDEVTEAM metadata without activating a HEAD:

   ```powershell
   codexdevteam init --project C:\Projects\MyProject
   ```

3. Configure `.codexdevteam/framework/registry.template.json` with strict,
   live-verified worker identities. Use distinct maker and `reviewer` (or
   `judgment`) roles with different configured runtime/model identities. The
   host currently binds the Codex CLI runtime only. Commit tracked framework
   configuration before activation; integration refuses to advance the project
   ref while tracked configuration changes are uncommitted.
4. Configure concrete `build`, `typecheck`, and `test_full` argv arrays and
   `strict_supervision: true` in
   `.codexdevteam/framework/verification.json`.
   Add narrow `.gitignore` entries for generated host runtime directories
   (`.codexdevteam/state/`, `.codexdevteam/gates/`, `.codexdevteam/control/`,
   and `.codexdevteam/logs/`) and for build outputs such as `__pycache__/`.
   Keep `.codexdevteam/framework/` tracked when the project shares its worker
   and gate policy. Do not ignore `.codexdevteam/` wholesale: the host commit
   boundary rejects broad ignored host metadata.
   If those commands create ignored build artifacts inside task worktrees,
   add narrowly scoped repository-relative patterns to
   `ignored_paths_allowlist`. Matching ignored paths are excluded from the
   host commit; all other ignored paths still refuse the commit. Do not use
   broad patterns or patterns that cover `.git` or `.codexdevteam`.
5. The default `capacity_source` is `codex_app_server`. The host uses the
   configured Codex executable to query the local Codex app-server
   `account/rateLimits/read` API before activation and on each cycle. This
   requires working Codex account authentication and one unambiguous shared
   `codex` quota bucket. Missing auth, unavailable API data, model-specific
   buckets, and unknown buckets all block dispatch. `host-preflight` performs
   the same read without activating the project.

   Projects that explicitly configure `"capacity_source": "snapshot"` use the
   manual compatibility source below. This mode requires an owner-maintained
   snapshot and does not satisfy unattended automatic capacity monitoring.

   ```powershell
   New-Item -ItemType Directory -Force .codexdevteam\control | Out-Null
   Copy-Item .codexdevteam\framework\capacity.template.json `
     .codexdevteam\control\capacity.json
   ```

   For snapshot mode, fill `observed_at` and one worker entry per active worker
   with freshly observed availability, free slots, quota, and any cooldown. Do
   not estimate capacity or copy an old observation forward.
6. Ensure `PLAN.md` contains only unassigned `pending` or `blocked` tasks, plus
   valid archived stubs. Tasks already `claimed`, `in_progress`, `needs_review`,
   or `done` require an explicit migration/review decision and block bootstrap.
7. Seed the parked state database from the exact PLAN snapshot:

   ```powershell
   codexdevteam host-bootstrap --project C:\Projects\MyProject
   ```

   Bootstrap does not activate supervision. It preserves archived task IDs for
   dependency checks and records the source PLAN SHA-256.

## Preflight and run

Run the side-effect-free worker/capacity preflight:

```powershell
codexdevteam host-preflight --project C:\Projects\MyProject
```

Read the durable supervisor mode, lease expiry, running maker count, and task
state totals at any time with:

```powershell
codexdevteam host-status --project C:\Projects\MyProject
```

When the active strict workers, gate commands, and configured capacity source
are ready, start the bounded host loop with explicit confirmation:

```powershell
codexdevteam host-run --project C:\Projects\MyProject --confirm-activation
```

For a fresh project that has no `PLAN.md` or task database, the host can start
directly from a UTF-8 brief. This creates the plan, validates it, bootstraps task
state, acquires the single HEAD lease, and starts supervision. Both file creation
and activation require explicit confirmations:

```powershell
codexdevteam host-run --project C:\Projects\MyProject `
  --brief C:\Projects\MyProject\PROJECT_BRIEF.md `
  --confirm-plan-write --confirm-activation
```

The combined command only treats the lease created by its own task bootstrap as
a controlled predecessor lease. It refuses existing plans or task databases.
All strict builder/checker, gate, and fresh-capacity prerequisites still apply.

After a previous host or bootstrap lease was cleanly released, the state store
still requires explicit confirmation before a new HEAD generation takes over:

```powershell
codexdevteam host-run --project C:\Projects\MyProject `
  --confirm-activation --confirm-prior-lease-takeover
```

The loop is bounded by `max_cycles_per_process`, reloads capacity each tick,
and parks/releases its lease when the bound is reached or the process receives
Ctrl+C. A second PowerShell window can request an orderly park after the current
closeout:

```powershell
codexdevteam host-stop --project C:\Projects\MyProject --reason "Operator requested pause"
codexdevteam host-status --project C:\Projects\MyProject
```

The stop request is acknowledged under `.codexdevteam\control` after the active
closeout. It does not kill a running maker. It commits through the Windows host
boundary, runs the exact-SHA
mechanical gate, and requires independent checker review before another cycle.
If a checker requests changes, the host uses the bounded `max_rework_attempts`
setting (default `1`), reuses the original maker identity, and includes the
verified checker rationale and evidence references in the next attempt. When
the cap is reached, the task stays open for human recovery and the run parks.
The host projects task status and assignment into PLAN; before each tick it
checks that the authoritative task fields still match SQLite.

## Restart recovery and branch integration

On startup after taking the HEAD lease, the Windows host first completes any
pending host integration transaction. It compares the checked-out files with
the recorded base or integrated commit, restores the verified PLAN backup, and
records completion. If the project contains unrelated changes or the target ref
has moved unexpectedly, recovery fails closed and no task is dispatched.

The host then verifies signed cancellation receipts and reaps any remaining
maker invocation through its persisted Windows Job Object identity. A task left
`claimed`, `in_progress`, or `needs_review` is recorded in the durable
escalation ledger and the host parks before dispatch. The task branch is
preserved. Inspect its branch, invocation receipts, gate artifacts, and review
events before deliberately returning it to work; the host does not guess
whether interrupted work should be replayed or reviewed.

After a task reaches `done` through an exact-SHA gate and independent checker,
the host verifies that the task branch still points at the approved SHA. It
merges the branch in a temporary worktree, runs the configured mechanical gate
against the integrated commit, and fast-forwards the checked-out project branch
only if the target has not moved. A merge conflict or failed post-integration
gate creates a durable escalation and parks the host. PLAN projection is
preserved across the branch update. This does not push or deploy.

## Current limits

- `host-run` activates only a fresh CODEXDEVTEAM installation. It refuses a
  DEVDEPARTMENT sidecar; the incumbent process fence and handover transaction
  are not implemented yet.
- Stop/status controls are local to the same project and host. `host-stop`
  requests an orderly park; Ctrl+C remains available. A running invocation is
  bounded by its configured timeout and Windows Job Object.
- Interrupted task branches require operator review after the host records a
  restart-recovery escalation. The host verifies process-tree quiescence and
  never automatically replays uncertain maker work.
- Rework beyond the configured cap remains open for human recovery. The loop
  stops rather than dispatching a subsequent task after incomplete closeout.
- Project branch integration is automatic after independent approval and a
  passing post-integration gate. Push and deployment remain separate.
