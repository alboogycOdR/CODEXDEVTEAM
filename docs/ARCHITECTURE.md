# Architecture

## Layer model

CODEXDEVTEAM is designed as five logical layers.

### 1. HEAD plane
High-reasoning control plane responsible for architecture, decomposition, arbitration and judgment. Initial policy targets a GPT-6-class Codex model, but model identity is configurable.

### 2. Policy and routing
Maps work to a configured role and capability floor, then filters workers by runtime health, capacity, quota, territory, machine affinity and measured evidence. Optional `Task_Class` labels map through project policy to a minimum capability floor and, when configured, a logical worker role; a supervisor-wide floor remains a lower bound, and unknown class labels fail closed.

`Task_Class` is optional in the version-1 task protocol and PLAN projection. A
supervisor receives a `TaskClassPolicy` from the project-owned
`.codexdevteam/framework/task-routing.json`. Capability floors must appear in
the registry's explicit `capability_order`; optional role mappings select only
logical roles present in the registry and cannot select configured checker
roles. Class keys never select a runtime or concrete model. Existing
unclassified tasks keep using the supervisor-wide floor and maker role.

### 3. Workforce
Default Codex implementation worker plus optional Claude Sonnet, Grok and additional runtime identities. Registry entries describe runtime/model/auth/worktree/branch/briefing/control/capacity properties.

### 4. Deterministic verification
Mechanical pre-review gates establish territory cleanliness, exact SHA, build/typecheck/full-test state and configured reachability/mutation/baseline checks. Results are SHA-bound artifacts.

### 5. Durable project state
PLAN/task protocol, REVIEW/verdict history, dossiers, worktrees, ledgers, CONTROL queue, evidence memory, supervisor state and HEAD lease.

Optional fast workers run only bounded schema-validated jobs. The current
run-log classifier is read-only, retries invalid output once, and returns a
durable escalation signal rather than changing task state. See
[`FAST_TIER.md`](FAST_TIER.md); it remains disabled for production routing until
a representative redacted accuracy corpus exists.

## Proven mechanisms to port from DEVDEPARTMENT Wave E

Port selectively and preserve behavior with tests:
- task lifecycle and validator;
- Owned_Paths and Protected_Grants;
- worktree/branch isolation;
- builder registry;
- CONTROL/single-writer mode;
- supervisor park/resume and durable ledgers;
- stale and stagnation handling;
- GateGuard, territory firewall and secret scan;
- push policy and cross-platform runner lifecycle;
- pack/project sync ownership;
- plan archival;
- remote command/control and board surfaces where still useful.

## Native CODEXDEVTEAM improvements

Build these as first-class rather than later retrofits:
- exclusive HEAD lease and controlled handover;
- provider-neutral role/capability/runtime/model configuration;
- scripted pre-review verification gate;
- SHA-tagged test evidence;
- capacity-aware routing;
- bounded/ledgered model invocations;
- evidence-backed memory;
- cost/outcome reporting by role/model;
- explicit separation of defined, active, available, eligible and assigned worker states.

Publication scheduling is a separate provider-neutral decision reducer for
`every`, `batch`, and `merge_only`. It returns a decision only; publication is
disabled unless explicitly enabled, and the reducer never commits or pushes.
See [`PUBLISHING.md`](PUBLISHING.md) for the adapter boundary and remaining
remote-effect requirements.

## Configuration principle

Configuration expresses policy. Engine code implements invariants. Concrete model IDs, quotas and runtime availability must not be spread through scripts.

## Initial implementation boundary

The first source package is `src/codexdevteam_kernel/`. It currently contains
provider-neutral worker identity/registry, versioned task records, typed
builder-to-HEAD CONTROL messages, task-state transition policy,
territory/grant decisions, an exclusive HEAD lease with fencing, atomic task
assignment, Git worktree lifecycle primitives, secret detection, a SHA-bound
mechanical gate, bounded Codex CLI invocation, invocation receipts, review
policy, and durable park/resume, escalation, and stagnation state. Task records
can snapshot maker unit/runtime/model. The worktree manager can copy configured
primary-checkout resources on create and refresh only when those paths are
gitignored, and removes those copies on clean worktree teardown. The
provider-neutral `TerritoryPolicy` authorizes builder write intents against the
assigned active task, protected paths/grants, other active territories, and
resolved repository paths. A Codex `PreToolUse` adapter covers structured
file-edit events. Maker launches provide a per-invocation read-only SQLite
snapshot containing only the current task and other active task territory
fields; it is placed under the task worktree's Git metadata and removed after
the runtime exits. Real maker/checker invocations renew the exclusive HEAD lease
and propagate lease loss to the Codex adapter, which cancels and reaps the
runtime process tree; on Windows this uses a Job Object. Supervised `codex exec`
sets `approval_policy="never"` through its configuration override, so
approval-dependent actions fail promptly while the configured sandbox remains
enabled. On Windows, the adapter launches Codex through native PowerShell with
literal-quoted arguments; the prompt remains on stdin and the PowerShell/Codex
process tree stays in the same Job Object. Invocation timeouts use the same
tree cleanup. The post-run gate
requires a snapshot of all authoritative tasks and includes active territory
owners in its fingerprint, rejecting changes that overlap another claimed,
in-progress, or needs-review task. It applies the same protected-path/grant
rules and rejects changed symlinks resolving outside the worktree. Arbitrary
shell writes remain a post-run gate responsibility, so
strict worker receipts require explicit CONTROL, host commit boundary,
task-worktree, and post-run territory capabilities. Structured-edit firewall
evidence is an optional pre-write defense and does not replace the host commit
boundary required for strict dispatch.
Approved completion requires a passed
gate artifact registered under the HEAD lease and an independent successful
checker invocation receipt bound to the same task SHA and gate fingerprint.
The HEAD-recorded checker result must also exactly match the identity and gate
reserved by `checker.started`, and its reported start time must not precede
that reservation; direct, substituted, or pre-reservation receipts cannot
bypass the launch reservation.
Dispatch filters active registry entries and orders role candidates by
configured model preference. An optional provider-neutral capacity observation
can further filter candidates by availability, free slots, quota, cooldown, and
freshness; the registry itself remains static identity/configuration. Capability
floors use an explicit configured ordering. A bounded dispatch cycle is
parked-by-default, checks dependencies and strict/capacity policy, and claims
tasks under the HEAD lease. `worker_readiness` reports defined, active,
capacity availability, policy eligibility, and current task assignments as
separate dimensions for diagnostics. One-shot maker launches can be composed
with `run_dispatch_and_launch_cycle()` under a caller-configured task limit.
The coordinator validates prompts and invocation IDs before it claims any task
and leaves maker CONTROL queued for gate-led draining. Each launch atomically
moves a claimed task to in-progress only while supervision is running,
creates/validates its worktree, runs the configured runtime adapter with task
identity and state snapshot context, and records a completion/failure receipt
under HEAD. It does not run a gate, parse model output into CONTROL, or approve
work. Makers can submit typed
CONTROL requests through `codexdevteam-control`; the CLI writes immutable
reports to the task worktree outbox, and HEAD validates and applies them after
the invocation. Outbox content is untrusted, rejected messages are quarantined,
and lease failures leave pending reports available for a later HEAD cycle.
Long-running scheduling, host-level orphan-process reaping, cost-aware ranking,
quota telemetry adapters, and automatic failover remain future work. Bounded
health sampling, stale-invocation escalation, and a retryable notification
outbox/delivery API are implemented. `run_continuous()` can accept a configured
notifier and drains due escalation notifications after each dispatch decision,
before launching makers. Batch size, claim lease, and retry delay are
configurable; failed deliveries remain in the outbox for a later cycle. The
transport remains disabled unless explicitly supplied. Invocation outcomes
and configured cost reports
are available without automatic routing changes. Project inspection is read-only; sync only
updates explicitly managed files below `.codexdevteam/framework/` when their
last-managed hash still matches, preserving project state and conflicts.
Park/resume mode is persisted transactionally with HEAD-authorized events, and
the typed review is applied atomically with task state and its event. There is
now a persistent escalation ledger with dedupe/reminder intervals and explicit
resolution. Stale/stagnation policy is present as a reducer with persisted
samples. Escalations enqueue durable notifications transactionally; the
lease-fenced `deliver_escalation_notifications()` path uses a provider-neutral
adapter contract, bounded claims, retry backoff, safe error codes, and stable
idempotency keys. Secret-like escalation messages are replaced before ledger
or transport persistence. Delivery is at-least-once, and callers remain
responsible for configuring a real transport. The bounded dispatch cycle can
consume caller-supplied task-health samples, persist streak/reset decisions, and
enqueue a circuit-breaker escalation when the reset budget is exhausted.
Maker lease renewals refresh a durable invocation-liveness row, and a terminal
invocation receipt closes it. Stagnation evaluation now returns `hold_running`
instead of a redispatch recommendation whenever a maker remains without a
terminal receipt. Dispatch cycles flag stale heartbeats and enqueue an
escalation; a stale row alone does not prove the process tree was cancelled.
The host runtime writes a one-use HMAC-signed cancellation sidecar after it
verifies process-tree termination. A successor HEAD checks that signature under
its lease before recording the evidence and clearing liveness. Failed or
tampered evidence leaves the invocation held. Automatic orphan redispatch
remains disabled.

The initial lease/state prototype uses SQLite transactions so lease checks and
event writes share one serialization boundary. Its database must remain on a
filesystem with validated SQLite locking semantics; multi-host/network-storage
use is not yet supported or verified. Lease expiry alone does not authorize a
new HEAD: takeover requires explicit confirmation, increments the fencing
generation, and invalidates the prior capability token. SQLite state now
records a schema version transactionally, validates required table layouts,
adopts compatible unversioned databases without rewriting records, and rejects
unknown future or incompatible schemas before initialization commits. Forward
migrations use explicit additive versioned steps through schema v7. Schema v2 adds an inactive,
lease-fenced handover staging record and a separate store of historical source
dependency IDs; historical IDs satisfy dependencies without being represented
as CODEXDEVTEAM-reviewed DONE tasks. Schema v3 stores explicitly preserved
legacy field occurrences separately from authoritative task records and binds
them to source PLAN hashes. Staging requires an empty, parked target store and
never grants activation authority. Schema v4 records bounded process-tree
cancellation evidence; schema v5 adds per-invocation receipt verification keys
and a durable cancellation-recovery ledger. Schema v6 records the maker PID,
OS creation fingerprint, and process-group ID in invocation liveness before the
provider runtime proceeds. Schema v7 adds the optional Windows Job Object
reference to maker liveness.
A read-only Markdown
PLAN projection, hash-bound surgical state patch primitive, and deterministic
monthly task archive plus bounded notes-rotation adapters exist alongside the
SQLite task/event store. Archived PLAN stubs retain completed dependency IDs in
the read projection. `StateStore.archive_older_plan_tasks()` validates
candidates against completed SQLite tasks, writes exact monthly archive blocks,
archives their task records, and queues the PLAN stub through the same
lease-fenced recovery outbox.
`StateStore.transition_task_with_plan()` atomically
commits the state event, task update and projection intent; the leased recovery
operation replaces PLAN only when its old or new content hash matches. Dispatch
cycles recover committed projections before making parked/running decisions.
CONTROL and review state mutations use the bridge when the caller supplies the
main `project_root`; maker launch, maker-gate, and checker-result supervisor
paths expose that option. Maker startup writes its in-progress transition and
PLAN projection intent together before invoking a runtime. Assignment and
dispatch cycle also expose opt-in PLAN projection
through the same outbox. A running dispatch cycle can schedule archival at a
configured interval; it is disabled by default, requires `project_root`, and
records successful maintenance under HEAD.
Forward schema migration and live provider verification remain future
increments. An
explicit continuous runner sequences bounded dispatch-and-launch cycles until
an event, parked mode, or configured cycle limit stops it; it is not started
implicitly. It renews its HEAD lease through cycle callbacks and idle waits,
and stops with a durable `closeout_incomplete` event if a launched maker task
is not done, blocked, or pending after the cycle callback. This prevents it
from dispatching a later batch while the prior maker is still awaiting gate or
checker review. The callback still has to perform the actual gate/checker/review
work; `closeout_maker_with_checker()` provides that ordered single-attempt
path for committed makers and leaves failed or changes-requested work open for
recovery. `requeue_changes_requested_task()` enforces a configurable rework
cap (one retry by default), journals exhaustion, and refuses another claim
once the cap is reached. The runner does not call these helpers automatically,
drive the retry loop, or provide a daemon/process lifecycle manager. Before
dispatch, it also stops for pre-existing `claimed` or `needs_review` tasks that
require explicit restart recovery;
`in_progress` tasks continue through the existing invocation-liveness and
stagnation path. Platform process identity records
now fingerprint Windows creation time and Linux boot ID plus process start
ticks to detect PID reuse. Identity is stored in the schema-v7 maker liveness
row under the HEAD lease. Windows maker processes are created suspended,
assigned to a named kill-on-close Job Object, and resumed only after identity is
persisted under the HEAD lease. A successor can reacquire the job and verify
whole-tree termination while holding the HEAD SQLite writer lock. The Linux
PIDFD backend can terminate the recorded process group but cannot prove
detached descendants are gone, so it does not release maker liveness. Reaping
remains explicit and is not automatic.

Fresh-project installation also writes `.codexdevteam/framework/supervisor.json`
with project-relative state, registry, worktree, CONTROL, and log paths plus
bounded polling policy. `WindowsHostConfig` validates this file, rejects
unknown or duplicate fields, symlink traversal, paths outside `.codexdevteam`
for host state, and any attempt to configure activation as running. The
worktree path resolves only to the configured project sibling required by
`GitWorktreeManager`. Strict workers and fresh capacity observations are
mandatory. This is configuration groundwork only:
`codexdevteam host-config --project <path>` validates and prints the inactive
configuration without creating state or acquiring HEAD. `codexdevteam host-run`
requires `--confirm-activation`, an existing initialized task database, and
fresh configuration; it only activates fresh projects. The default
`capacity_source` reads the local Codex app-server's read-only
`account/rateLimits/read` API before activation and on each dispatch cycle. It
accepts only the unambiguous shared `codex` quota bucket and refuses missing,
model-specific, or unknown buckets. A versioned timestamped JSON snapshot is
still available as an explicit `capacity_source: "snapshot"` compatibility
mode; its loader rejects stale-format data, future timestamps, unknown workers
or fields, and malformed availability. Neither source bypasses dispatch
freshness and quota checks.
`codexdevteam host-bootstrap` creates the parked task database from the exact
PLAN hash under a temporary lease. It imports only pending/blocked unassigned
tasks and archived IDs; in-progress, needs-review, and completed task records
are refused because no CODEXDEVTEAM gate/review receipt exists for them.
Provider-neutral prompt renderers now carry each authoritative task's
acceptance criteria, Owned_Paths, Protected_Grants, dependencies, and evidence
notes into maker input; the checker renderer binds review to one SHA and gate
fingerprint and requires the strict JSON receipt format. Missing maker
acceptance criteria fail closed.
The host runtime loader binds active strict workers to configured runtime
adapters and requires concrete build, typecheck, and full-test argv commands
plus protected paths. It currently supports the Codex CLI adapter; any other
active runtime fails closed until an adapter is explicitly implemented. Loading
these bindings launches nothing and acquires no lease. Host policy identifies
maker and checker roles explicitly; the checker must use the kernel's supported
`reviewer` or `judgment` role and an independent configured runtime/model.
Verification policy may also name narrowly scoped ignored-path patterns for
generated artifacts such as Python bytecode. Those matching ignored paths are
excluded from the host commit after quiescence; unlisted ignored paths still
refuse the whole commit. Broad patterns and Git/host metadata are rejected.
The closeout API accepts a checker-prompt builder evaluated only after the
mechanical gate passes, so the prompt can include the gate's actual SHA and
fingerprint.
Host cycle helpers now construct clean-checkout dispatch inputs from active
task records, fresh capacity observations, and configured runtime bindings;
they run each maker through the exact-SHA gate and independent checker before
returning from the cycle callback. The closeout callback receives the same
immutable base ref used for maker worktrees. These helpers require an
already-authorized lease and do not activate the project themselves.
Between cycles, the host permits only its own projected task status and
assignment changes in PLAN: it compares all kernel task fields and archived
IDs with SQLite and rejects other project dirt or task-content drift.
`activate_fresh_host()` is the explicit Windows-only activation path for
installed fresh projects; it refuses DEVDEPARTMENT sidecars and acquires the
exclusive HEAD lease before setting running mode. `run_configured_host_loop()`
provides bounded execution under that active lease. It requires running mode, keeps
the lease renewed, reloads capacity and task prompts every tick, and invokes
mandatory gate/checker closeout in the cycle callback. It refuses Linux and
does not acquire the lease itself. The CLI's `host-stop` writes a host-local
stop request which the running process polls; it journals the request and
signals the local loop, allowing the current cycle to finish gate and review
before the loop writes parked mode and releases the lease. Checker-requested
rework is driven through the existing durable review ledger and requeue cap;
verified checker rationale and evidence references are passed back to the
original maker. Exhausted rework remains open for human recovery. Windows
restart recovery verifies or reaps persisted Job Object trees, escalates
interrupted tasks, preserves their branches, and parks before further dispatch.
The host-specific DEVDEPARTMENT fence remains outstanding. An embedding host
can call `park_configured_host()` under the same lease for the same
graceful-stop behavior.
`codexdevteam host-preflight --project <path>` checks the bindings and queries
the configured capacity source without activating, dispatching, or creating
state. Its output is worker/capacity readiness evidence only and does not
constitute activation or pilot evidence. The Codex app-server query is bounded
by a timeout and fails closed when account auth, the API, or a known shared
quota window is unavailable.
Native PowerShell setup and operation steps, including current recovery and
handover limits, are recorded in `docs/WINDOWS_HOST_RUNBOOK.md`.
`codexdevteam host-status --project <path>` reads mode, lease, liveness, and
task-state counts through a read-only SQLite connection.
For a fresh inactive installation, `codexdevteam host-plan --project <path>
--brief <file> --confirm-write` invokes the configured `head_candidate` as a
read-only planner, captures its final response separately from CLI event
output, validates strict task JSON through the canonical Markdown parser and
task protocol, and exclusively creates `PLAN.md`. The command refuses an
existing task database or plan, assigns no builders, and does not acquire HEAD.
The generated plan includes a machine-readable provenance comment binding the
invocation and configured planner identity to the brief, response, and validated
task-plan hashes; the brief text itself is not copied into the plan. The separate
`host-bootstrap` and `host-run` remain available as staged controls. Alternatively,
`codexdevteam host-run --project <path> --brief <file> --confirm-plan-write
--confirm-activation` chains planning, task bootstrap, explicit activation, and
supervision. It still requires configured strict builders, checker, capacity,
and Windows host policy. Strict builder/checker activation and full supervised-run
evidence remain later standalone-pilot gates.
Each cycle can accept
caller-supplied task-health and fast-tier observations. When a notifier is
configured, dispatch-cycle escalations are delivered from the durable outbox
before maker runtime calls; failures remain retryable under the existing
idempotency contract. CODEXDEVTEAM installation/onboarding and the bounded
mechanical gate are implemented. The gate checks exact SHA, clean worktree,
task territory against a caller-supplied full task snapshot, and requires configured
build, typecheck, and full-test argv commands to pass before review; it also
scrubs the environment and records skipped checks. Reachability and mutation
commands can be configured as additional gate checks. For configured commands,
the gate runs the same argv against a temporary detached worktree at the merge
base, normalizes checkout paths, and records output hashes to conservatively
classify new versus inherited failures. An inherited failure can pass only when
the task names an existing, non-completed owner and that owner remains open at
gate time. HEAD gate registration and review validate the same artifact rules.
This comparison does not replace test-specific evidence or prove that differing
failure output has a different root cause. The review operation is an
authoritative persisted completion path; invocation records bind review to a
successful read-only checker launch. The bounded supervisor checker cycle now
requires a configured independent reviewer/judgment identity, a clean worktree
at the registered gate SHA, and a one-time HEAD-leased launch reservation.
Strict JSON verdicts are attributed to the configured invocation identity and
applied by the gate-bound HEAD transaction. Fake-adapter integration coverage
verifies mixed-runtime separation, duplicate-launch prevention, normalization,
and application; live provider execution remains open.
The read adapter was exercised against the pinned DEVDEPARTMENT Wave E `PLAN.md`
(45 tasks, zero parse or structural findings); that smoke check does not
establish full schema or handover compatibility.
Gate commands can receive explicit per-worktree environment templates. Task
and baseline runs get separate scopes; template values participate in cache
fingerprints and are redacted from output logs. A separate file-locked test-run
cache is keyed by exact SHA, argv fingerprint, and environment fingerprint,
without persisting raw argv in receipts; the gate reuses its `test_full`
receipts. The `codexdevteam-test` command runs a named configured argv and emits
a SHA-bound evidence reference. `GateResult` retains the matching trusted
`TestRunResult` in memory, while gate artifacts omit raw argv. With CONTROL
draining deferred, `Supervisor.finalize_maker_gate()` verifies the maker
worktree SHA, registers test and gate receipts under HEAD, then drains the
untrusted reports. A fresh-project installer stages bundled framework-owned
files and an inactive marker, then publishes them with a directory rename;
live normal-trust and strict-capability smoke checks remain open.
