# Controlled handover matrix

This matrix describes when CODEXDEVTEAM may take control of a project. A
sidecar install is metadata-only and is never a handover. Handover means one
system has been fenced from writing before the other can dispatch or review.

## Source/target matrix

| Source | Target | Current disposition | Required evidence | State migration |
|---|---|---|---|---|
| Fresh project | CODEXDEVTEAM | Eligible only after runtime, write-firewall, CONTROL, strict-worker, and explicit-activation checks pass | Installed defaults committed; runtime-state ignore rules; live capability receipt for every strict worker; one explicit operator activation; HEAD lease acquired | Initialize CODEXDEVTEAM state; no incumbent state |
| DEVDEPARTMENT Wave E at `2d4202c9c70300f3f5cdf97a0b866c1a15b4c760` | CODEXDEVTEAM | Sidecar installation only; handover is not currently executable or verified | Pinned revision and metadata allow the sidecar only. A controlled transfer additionally requires incumbent process/service fencing, no in-flight builders or reviews, a clean worktree disposition, and explicit task mapping | No shared task/CONTROL/lease schema. Do not auto-import PLAN or `.autopilot_state.json` |
| Other DEVDEPARTMENT revision | CODEXDEVTEAM | Refuse activation; preserve incumbent | A new compatibility profile and fixtures are required before any sidecar or migration claim | Unknown |
| CODEXDEVTEAM | DEVDEPARTMENT Wave E | Refuse automatic return transfer | CODEXDEVTEAM must be parked, all maker/checker processes fenced, its lease released, and task state translated under an explicit reviewed mapping | No CODEXDEVTEAM-to-PLAN writer or reverse state translator exists |
| CODEXDEVTEAM v1 | CODEXDEVTEAM v1 | Supported managed upgrade path | Hash-clean ownership manifest; project-state preservation; conflict-free sync | CODEXDEVTEAM project state remains CODEXDEVTEAM-owned |

## Why parking is insufficient for DEVDEPARTMENT Wave E

The pinned supervisor's `RuntimeState.parked` branch is a dispatch decision,
not a process fence. Before that decision, a live tick may reap dispatches,
drain Telegram and durable inbox commands, run maintenance and learning,
drain CONTROL, refresh the PLAN projection, and read usage/review state. After
the action handler, it saves supervisor state and may push, publish the board,
and run Tower sync. A parked process remains alive to service inputs.

The `STOP` file is checked during decision-making, after earlier tick work.
The current tick still reaches state persistence and configured push, board,
and Tower paths before the loop exits. The supervisor's in-memory `Popen`
tracking is process-local. A restart loses that bookkeeping; the PLAN busy
check helps avoid duplicate dispatch but is not a cross-system lease.

The `.devteam/review.lock` is scoped to review and can be reclaimed as stale
after the configured timeout. It does not fence dispatch, CONTROL application,
pushes, or the supervisor process. There is no global DEVDEPARTMENT HEAD lease
that CODEXDEVTEAM can acquire or renew.

These findings were read from the pinned checkout only. No DEVDEPARTMENT file
was changed. Relevant source locations include:

- `scripts/supervisor.py`: `decide()` parked/STOP handling, `_acquire_review_lock()`,
  and the main tick order in `main()`;
- `autopilot.json`: configured builder, review, push, and control modes;
- `docs/CONTROL.md`: the incumbent strict-mode single-writer contract;
- `docs/DEPLOY_CLAWSRV.md` and `deploy/ecosystem.config.js`: process-manager
  deployment and restart behavior.

CODEXDEVTEAM maker and checker runtime calls now renew the SQLite HEAD lease
while the synchronous invocation is active. If renewal is fenced, the Codex
adapter receives a cancellation signal and terminates its invocation process
group; timeouts use the same process-tree cleanup. This protects CODEXDEVTEAM's
own bounded runtime calls but does not fence the DEVDEPARTMENT supervisor,
remote-control listeners, push process, or any child started outside the
adapter. It is a prerequisite improvement, not evidence that either
cross-system handover is safe.

## Safe operational sequence

For a future DEVDEPARTMENT-to-CODEXDEVTEAM transfer, the target host's operator
must perform and record each step. A parked marker or a clean PLAN alone does
not satisfy the fence.

1. Stop creating work and disable the incumbent service's automatic restart.
2. Request the incumbent STOP and wait for the supervisor process to exit.
   Capture the process-manager status and verify the PID is absent after the
   final tick. A successful STOP request alone is insufficient.
3. Wait for all builder, checker, review, push, and sync child processes to
   exit. Reconcile durable in-flight and CONTROL queues and record their
   disposition.
4. Record the source commit, PLAN hash, autopilot-state hash, open-task list,
   worktree/branch disposition, and relevant queue/ledger hashes. Preserve a
   backup of the incumbent project state.
5. Require a clean source worktree or explicitly enumerate and preserve every
   intentional uncommitted change. Do not silently discard it.
6. Create a human-reviewed task mapping. Until a tested translator exists,
   the only automatically safe mapping is an empty open-task set; otherwise
   leave DEVDEPARTMENT as HEAD and refuse activation.
7. Obtain explicit operator authorization for the target HEAD. Only then may a
   future activation command acquire CODEXDEVTEAM's lease and update its
   installation marker as one recoverable operation.
8. For return transfer, stop and fence CODEXDEVTEAM symmetrically. Never let
   both supervisors remain live with write or remote-control capability.

An operator handover record should bind the project root, source/target
identities, source revision, source-state hashes, process-manager stop evidence,
remaining child-process evidence, worktree disposition, task mapping hash,
operator identity, and timestamp. Until a host-specific verifier can check the
service and process evidence and an activation transaction consumes that
record, it is documentary evidence only and cannot authorize activation.

## Current gate result

`assess_compatibility()` deliberately returns `SIDECAR_ONLY` for the pinned
DEVDEPARTMENT baseline and `activation_allowed=False` for every profile. The
Wave E supervisor can still write after a park request and does not honor the
CODEXDEVTEAM SQLite lease. No CODEXDEVTEAM handover implementation can safely
override that result using metadata alone. A hash-bound read-only task-map
preview and lease-fenced inactive target-state staging now exist. Process
fencing, activation, reverse transfer, and real-project round-trip tests remain
open.

## Read-only task-map preview

`plan_handover()` in `codexdevteam_kernel.handover` builds a deterministic,
read-only preview from a source PLAN and an explicit version-1 or version-2 mapping. The
mapping is bound to the exact UTF-8 PLAN hash and must account for every parsed
task. Open legacy work can enter only as `pending` or `blocked`; active runtime
state and maker identity are discarded. Completed source tasks remain
historical and are not imported as CODEXDEVTEAM-approved `done` tasks. Protected
grants are empty unless explicitly regranted in the mapping, and assigned
target workers must already be active and strict-verified. The preview checks
dependencies and territory overlap and emits source/mapping hashes. It also
lists source-only PLAN fields by task in `unmapped_source_fields`, including
fields such as Description, Progress_Notes, and Review_Findings that the kernel
does not yet model. Field values are not copied into the preview output; each
listed field requires explicit human disposition in a version-2 mapping:
`preserve` stores its parsed occurrences as separate non-authoritative context,
while `exclude` records an acknowledged omission from target task context.
Duplicate unmodeled source fields are reported separately in
`source_field_conflicts` by task and field name only; preserving them keeps
every occurrence and its order. Duplicate authoritative fields still fail
closed. Preview output contains field names and decisions, never source values.
Preserved fields are secret-scanned before target-state writes.

The installed CLI exposes the preview as:

```text
codexdevteam handover-plan --project <incumbent> --mapping <reviewed-map.json> --registry <target-registry.json>
```

To emit an incomplete map draft bound to the current PLAN, run:

```text
codexdevteam handover-map-template --project <incumbent>
```

The version-2 draft marks source-completed tasks as `historical`, leaves
open-task dispositions and states blank, and leaves each unmodeled field's
disposition blank. It is intentionally rejected by `handover-plan` until the
operator reviews and completes every required entry. Both commands print to
stdout and leave the incumbent project unchanged.

The read-only preflight against the pinned Wave E reference PLAN
(`fce78b7f05b06e165fd0a6e12241025a71d9492e3911620e5a896fc4baeeb024`)
parsed 45 tasks, all already `done`, so it produced no active target tasks.
It reported 12 distinct source-only fields and one duplicate unmodeled
`Artifacts` field on `TASK-026`, without returning any of their values. This is
translation evidence only; it does not establish a viable live handover or
provide an active-work pilot.

`PLAN.md` is read from the incumbent project. Mapping and target registry are
read-only inputs; symlink files are refused. The command prints a JSON preview
and returns a nonzero status for stale hashes, incomplete mappings, unsafe
states, unverified target assignments, or invalid task sets.

This operation writes no project files or state database, acquires no HEAD
lease, and cannot authorize activation. Its output is input for human review
and future translation/application work; it does not satisfy process fencing,
incumbent-state preservation, or handover completion evidence.

## Inactive target-state staging

The Python API `stage_handover()` applies the explicit version-2 mapping into
an empty CODEXDEVTEAM SQLite store while holding the target HEAD lease. It
recomputes translation from the supplied current PLAN bytes and reviewed map,
requires a disposition for every unmodeled source field, validates the complete
task/dependency set, and records one idempotent `handover.tasks_staged` event
bound to the source PLAN and mapping hashes. Preserved field occurrences are
stored separately from authoritative task records; excluded fields are never
copied into target task context. Secret-like preserved content fails closed.
The target supervisor must be parked. Source-completed IDs are kept in a
separate historical-dependency table: they can satisfy a dependency but never
appear as CODEXDEVTEAM-reviewed `DONE` tasks.

The CLI entry point is:

```text
codexdevteam handover-stage --project <incumbent> --mapping <reviewed-map.json> --registry <target-registry.json> --state-db <target-state.sqlite>
```

It creates or updates only the explicitly named CODEXDEVTEAM state database,
prints `source_process_fenced: false` and `activation_authorized: false`, and
leaves the incumbent PLAN and installation marker unchanged. The CLI releases
its temporary target HEAD lease after staging; the imported supervisor remains
parked. Protect the state database with local repository excludes or place it
outside the checkout.

This is inactive state staging only. It does not inspect or fence the incumbent
process tree, modify either installation marker, create an activation grant, or
authorize either supervisor to run. Process-fenced activation and reverse
transfer remain open. The Wave E baseline's unmodeled PLAN fields now require
human dispositions in the version-2 mapping; no default field decision is
inferred.

### Wave E process-control evidence

Read-only inspection of the pinned implementation confirms that `STOP` is a
request to the supervisor loop: `scripts/supervisor.py` passes its presence to
`decide()`, which returns a `HALT` action, and the main loop exits after that
tick. This does not cancel already-launched builders. Dispatch children have
per-unit records under `.devteam/inflight/<unit>.json`; `_reap_durable_inflight()`
checks whether those PIDs are alive and removes dead records, but it does not
stop a live child. The records do not identify the supervisor process or prove
that remote-control listeners, push/sync work, review sessions, or other
children have stopped. Therefore a `STOP` file and an empty inflight directory
are insufficient fence evidence; a host-specific process-manager stop and
independent child-process reconciliation remain mandatory.
