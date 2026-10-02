# Architecture

## Layer model

CODEXDEVTEAM is designed as five logical layers.

### 1. HEAD plane
High-reasoning control plane responsible for architecture, decomposition, arbitration and judgment. Initial policy targets a GPT-6-class Codex model, but model identity is configurable.

### 2. Policy and routing
Maps work to a role and capability floor, then filters workers by runtime health, capacity, quota, territory, machine affinity and measured evidence. Optional `Task_Class` labels map through project policy to a minimum capability floor; a supervisor-wide floor remains a lower bound, and unknown class labels fail closed.

`Task_Class` is optional in the version-1 task protocol and PLAN projection. A
supervisor can receive a `TaskClassPolicy` with a provider-neutral mapping such
as `{"mechanical": "standard", "critical": "frontier"}`. Capability floors
must appear in the registry's explicit `capability_order`; class keys never
select a runtime or concrete model. Existing unclassified tasks keep using the
supervisor-wide floor.

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
the runtime exits. Real maker/checker invocations renew the exclusive HEAD lease and
propagate lease loss to the Codex adapter, which cancels and reaps the runtime
process group; invocation timeouts use the same tree cleanup. The post-run gate
requires a snapshot of all authoritative tasks and includes active territory
owners in its fingerprint, rejecting changes that overlap another claimed,
in-progress, or needs-review task. It applies the same protected-path/grant
rules and rejects changed symlinks resolving outside the worktree. Arbitrary
shell writes remain a post-run gate responsibility, so
strict worker receipts now require explicit control, structured-edit firewall,
task-worktree, and post-run territory capabilities.
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
but has no daemon/process lifecycle manager. Platform process identity records
now fingerprint Windows creation time and Linux boot ID plus process start
ticks to detect PID reuse. Identity is stored in the schema-v7 maker liveness
row under the HEAD lease. Windows maker processes are created suspended,
assigned to a named kill-on-close Job Object, and resumed only after identity is
persisted under the HEAD lease. A successor can reacquire the job and verify
whole-tree termination while holding the HEAD SQLite writer lock. The Linux
PIDFD backend can terminate the recorded process group but cannot prove
detached descendants are gone, so it does not release maker liveness. Reaping
remains explicit and is not automatic.
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
