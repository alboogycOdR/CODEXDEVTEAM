# CODEXDEVTEAM Roadmap

## Phase 0 — Constitution and lineage

- [x] Create independent CODEXDEVTEAM repository.
- [x] Pin stable DEVDEPARTMENT Wave E reference baseline.
- [x] Define exclusive HEAD invariant.
- [x] Define DEVDEPARTMENT interoperability posture.
- [x] Define three onboarding modes.
- [x] Define provider-neutral role/capability/runtime/model architecture.

## Phase 1 — Shared kernel extraction

- [x] Inventory Wave E files and tests by mechanism.
- [x] Define CODEXDEVTEAM package/sync ownership manifest.
- [x] Port task protocol + validator.
- [x] Add lossless monthly PLAN archival projection with dependency-preserving stubs.
- [x] Add bounded, idempotent orchestrator-note rotation to project handovers.
- [x] Add lease-fenced SQLite/PLAN projection outbox and recovery for direct transitions, CONTROL/review, and optional assignment projection.
- [x] Integrate verified completed-task PLAN archival with SQLite archive and recoverable PLAN projection intent.
- [x] Add disabled-by-default, lease-fenced interval scheduling for PLAN archival.
- [x] Port builder registry with provider-neutral schema.
- [x] Port worktree/dispatch primitives.
- [x] Port CONTROL/single-writer state path.
- [x] Adapt push scheduling into a provider-neutral, opt-in publication decision reducer.
- [x] Add builder CONTROL outbox and HEAD-side validation/application.
- [x] Expose defined/active/available/eligible/assigned worker-state projection.
- [x] Port supervisor durable state, park/resume and escalation ledgers.
- [x] Add lease-fenced escalation notification outbox with retry/cancel lifecycle and provider-neutral delivery adapter.
- [x] Consume supplied stagnation samples in dispatch cycles and route exhausted breakers into the escalation outbox.
- [x] Persist bounded runtime cancellation method/result/exit evidence; keep maker liveness held when termination cannot be verified.
- [x] Persist signed, one-use process-tree cancellation evidence across HEAD lease loss; verify it under the successor lease before clearing maker liveness.
- [x] Verify the runtime territory capability set with isolated task worktree, host commit boundary, CONTROL drain, post-run gate, and strict-receipt dispatch; keep manifest-deferred GateGuard prompts out. The Codex pre-write hook remains optional and its latest headless activation attempt was inconclusive.
- [x] Configure Windows + Ubuntu CI matrix.
- [x] Verify the current kernel suite on both platforms locally (Windows and Ubuntu 24.04 container pass; Ubuntu WSL startup timed out while Docker Desktop was running).
- [x] Obtain a hosted CI run before enabling autonomous supervision (all four
  Ubuntu/Windows × Python 3.11/3.12 jobs passed; see `docs/RUNTIME_SMOKE.md`).

### Implementation status — 2026-10-02

The first provider-neutral kernel increment is underway in `src/codexdevteam_kernel/`:
versioned task records/read-only PLAN projection, worker identities, role policies
and registry, task transitions, territory decisions, leased SQLite state/events,
typed CONTROL messages, atomic task assignment, Git worktree primitives, secret detection, a SHA-bound gate,
typed maker/checker review policy, deterministic registry dispatch planning,
lease-protected durable park/resume state, deduplicating escalation ledger with
retryable notification outbox,
provider-neutral stale/stagnation policy, bounded Codex CLI adapter, invocation
receipts, atomic gate/checker-bound review application, read-only onboarding
detection, conservative framework sync, and inactive lease-fenced handover
state staging are implemented. The latest 243-test contract suite passes on Windows (two symlink-permission skips,
one POSIX-only process-group test, and three Linux-only reaper tests). WSL last passed at 157 tests before the
current gate-to-CONTROL and PLAN projection integrations. Two full Ubuntu WSL
retries on 2026-10-02 failed before Python startup with
`HCS_E_CONNECTION_TIMEOUT`; the second was after the Ubuntu test container had
exited. An earlier full suite passed in a disposable Ubuntu 24.04 container
with Git installed and the workspace mounted read-only: 231 tests ran with one
platform-specific skip, before the PIDFD reaper was added. The current Debian
and Ubuntu 24.04 suites both pass all 243 tests against the installed wheel
(4 platform-specific skips). The prior Debian-container run passed 224 tests before
strict-receipt validation and continuous-runner keepalive changes.
Ubuntu container evidence is recorded in `docs/RUNTIME_SMOKE.md`. This includes
the latest handover context/schema-v7
additions, the fast-tier corpus evaluator, continuous notifier delivery, and
POSIX process-group coverage.
Earlier runs passed 210 tests as root and as an unprivileged UID, before those
latest additions; a focused current cancellation/schema slice also passed 24
tests with one Windows-only skip. Debian container evidence is supplementary
Linux evidence; WSL startup timed out during exploratory checks and is not a
release prerequisite. Hosted CI passed on Ubuntu and Windows with Python 3.11 and 3.12 (branch-head
run [37185171206](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37185171206)).

The verification gate now runs configured commands on both the task SHA and a
temporary detached baseline worktree, attributes exact matching failures, and
requires an open task owner before inherited failures can proceed to review.
Gate artifact registration and review apply the same evidence rules. Task-set
validation rejects missing, completed, or self-referential failure owners.
The provider-neutral TerritoryPolicy now authorizes builder write requests
against assignment, task state, owned paths, Protected_Grants, conflicting
active tasks, and resolved repository boundaries. A Codex `PreToolUse` adapter
now checks structured file-edit events and rejects malformed or unverified
context with an explicit denial. The post-run gate requires the current full
task snapshot, rejects paths reserved by other active tasks, checks both sides
of renames, and includes active territory owners in its result fingerprint.
Strict registry receipts require verified CONTROL, host commit boundary,
worktree-isolation, and post-run territory capabilities. Historical disposable
Codex smoke evidence for the structured-edit firewall does not satisfy the
current host-commit-boundary receipt. The local single-store disposable Codex
smoke records the earlier capability set, with maker edit, hook, CONTROL drain,
and HEAD gate evidence in `docs/RUNTIME_SMOKE.md`. The maker left its file uncommitted; the fixture
HEAD harness supplied that commit and a redundant progress report, which HEAD
rejected because the task was already in progress. This does not satisfy the
deferred real-project pilot. Claude adapters remain unverified, and Codex shell
writes still rely on worktree containment and the post-run gate.
Maker/checker calls now renew the HEAD lease while running; lease loss signals
the Codex adapter to terminate its process tree, and invocation timeouts use the
same cleanup. This fences CODEXDEVTEAM's synchronous runtime calls, not the
incumbent DEVDEPARTMENT supervisor or processes launched outside the adapter.
Maker lease renewals now also refresh a durable per-invocation liveness record.
Health sampling returns `hold_running` while that invocation lacks a terminal
receipt, preventing a second maker proposal. Dispatch cycles now report fresh
versus stale invocation heartbeats and enqueue a deduplicated escalation for a
stale invocation. Runtime cancellation now records the platform kill method,
verification result, and exit code in the invocation receipt. If lease loss
prevents that receipt from reaching SQLite, the host adapter writes an HMAC-
signed one-use sidecar using a per-invocation key stored only in the local state
database and process memory; it never enters the provider subprocess environment.
A successor HEAD verifies the signature under its lease before completing
liveness. Tampered or unverifiable evidence leaves the maker held. Automatic
orphan redispatch remains disabled.
The explicit continuous runner accepts caller-supplied task-health and fast-tier
observations and can drain the durable escalation outbox between the dispatch
decision and maker launches. Batch size, claim lease, and retry delay are
configurable; transient notifier failures retry on later cycles with the same
notification ID. Delivery remains disabled unless a notifier is explicitly
supplied. Explicit lease-fenced stale-invocation reaping is now available through
`StateStore.reap_stale_invocation()`; it does not run automatically. Windows
maker processes start suspended inside a named kill-on-close Job Object, with
the Job identity persisted under the HEAD lease before resume. A successor HEAD
can reacquire the Job and verify whole-tree termination.
The continuous runner also keeps its exclusive HEAD lease alive across idle
intervals and cycle callbacks; lease loss is surfaced before another cycle.
Cross-platform process identity now captures PID creation fingerprints on
Windows and Linux and stores PID, start token, process group, and (on Windows)
the named Job Object reference with maker liveness under the HEAD lease. The
Linux PIDFD backend verifies only process-group termination; detached descendants
may escape, so it keeps invocation liveness held. Windows Job Objects provide
whole-tree containment for managed makers. Automatic reaping and redispatch
remain disabled.
Stale-invocation escalation now adds a read-only observation of that recorded
identity (`matches`, `absent`, `pid_reused`, or `unverifiable`); legacy rows
without identity are marked explicitly. Distinct observations are persisted as
lease-fenced, idempotent audit events with the start token represented only by
its SHA-256 fingerprint. The explicit Linux backend pins the group leader with
a pidfd, stops it before signaling its group, verifies group exit, and holds the
HEAD SQLite writer lock while performing the bounded effect. Process-group
termination does not prove that descendants did not escape the group, so it
keeps invocation liveness held. Automatic reaping and redispatch remain
disabled.
These changes do not complete the corresponding roadmap items: Markdown
write-back/interoperability, automatic cross-platform orphan reaping and
lifecycle management, external capacity telemetry and automatic failover, and
live interoperability/pilot work remain outstanding. Hosted CI passed on Ubuntu
and Windows with Python 3.11 and 3.12. The HEAD lease/state database
is a local-filesystem prototype and is not verified for multi-host or network
filesystems. It now records SQLite schema version 7 transactionally, upgrades
the additive v1-to-v7 handover provenance, opaque-context, process-termination,
signed cancellation-recovery, and maker process-identity
evidence fields, validates required table
layouts, adopts compatible unversioned state without rewriting
records, and rejects future or incompatible schemas. Forward schema upgrades
still require explicit migrations.

Routing now accepts optional time-bounded capacity snapshots. A supplied
snapshot filters active workers with stale observations, no free slot, exhausted
quota, or an active cooldown; omitted snapshots preserve the pre-capacity
behavior. Capacity remains an adapter-supplied observation, not configuration
embedded in worker identity. Registry configuration also supports an explicit
capability ordering, used to enforce task capability floors without comparing
labels lexically. `worker_readiness` reports defined, active, available,
eligible, and assigned dimensions independently. Optional PLAN `Task_Class`
labels map to configured capability floors; unknown or unmapped classes fail
closed and class floors cannot lower the supervisor-wide floor. `EvidenceMemory`
now provides a local SQLite fact store that
requires citations, scopes retrieval to project/stack and task subjects, records
bounded injections, scores approval/rework outcomes, demotes low-confidence
facts, and retires facts after probation. The supervisor can attach an
already-ledgered, task-bound injection to maker context with citations and
advisory framing, then bind it to HEAD review events and score approval or
explicitly cited rework facts. Deterministic miners cover registered passing
test runs, repeated verified territory conflicts, in-territory review catches,
and repeated new gate failures. Shared-fact export, legacy `INSTINCTS.md`
migration/rendering, and measured field-corpus accuracy remain open.

Invocation receipts now retain registry role attribution, Codex-reported turn
token counts, durations, outcomes, and optional adapter-reported costs.
`codexdevteam usage` aggregates invocation and task-review outcomes by role/runtime/model/purpose; explicit
time-effective rates can price known token counts. Unknown usage and unpriced
runs remain visible as unmetered, never as zero-cost.

A bounded supervisor dispatch and launch coordinator is now present. It remains parked until
lease-authorized resume, dispatches dependency-ready work only to strict-verified
workers with fresh capacity by default, and rechecks running mode atomically at
claim time. `run_dispatch_and_launch_cycle()` caps maker launches per cycle and
validates prompt/invocation inputs before claiming work. Each launch atomically
starts a claimed task only while supervision is running, creates and validates
its task worktree, atomically transitions the task and optional PLAN projection
to `in_progress` before invoking the configured runtime, then records its maker
receipt under HEAD. Maker CONTROL remains queued for gate-led draining; this coordinator
does not parse free-form model output, run gates, or perform reviews. The
worktree manager can copy configured files from the primary
checkout on create/refresh and remove them on clean teardown, after validating
safe paths and requiring Git to confirm each destination remains ignored.
Lease renewal and process-tree timeout/cancellation now cover bounded maker and
checker invocations. An explicit continuous runner now sequences bounded
dispatch-and-launch cycles until stopped, parked, or its cycle limit; it is not
started implicitly and does not supervise daemon/process lifecycle. Per-worktree
data-source/resource bundles and health-driven remediation beyond persisted
action proposals remain. Maker
hooks now receive an ephemeral read-only SQLite snapshot containing only the
current task and active territories. Codex invocations use an invocation-local
temporary directory; richer project resource templates beyond configured
ignored-file copying remain open. A
durable escalation notification outbox supports bounded lease-fenced claims,
provider-neutral transport adapters, retry backoff, cancellation on resolution,
and stable idempotency keys. No external notification transport is configured.

The maker runtime now exposes `codexdevteam-control` with launcher-provided
task/worker identity and an immutable task-worktree outbox. After a maker
invocation, HEAD drains reports under the lease: accepted reports use the typed
CONTROL validator and authoritative state transaction; invalid reports are
quarantined; expired-lease reports remain pending. This is an untrusted
request channel, not builder authority. The PLAN adapter now has a
hash-compare-and-swap state patch primitive that
changes only `Status` and `Assigned_To`, preserving line endings, unknown task
fields, and unrelated blocks. Direct task transitions now combine lease
validation, atomic replacement and SQLite/PLAN recovery. The
`StateStore.transition_task_with_plan()` path commits a hash-bound projection
intent with a task transition, atomically replaces PLAN while holding the HEAD
database writer lock, and supports fenced idempotent recovery through
`apply_pending_plan_projections()`. Dispatch cycles recover pending writes
before considering parked/running mode. This path is tested under injected
failures and lease takeover. CONTROL and review completion can use this bridge
when the caller supplies the main project root; the maker-gate finalizer,
checker-result path, assignment operation, and dispatch cycle expose that option.
`archive_older_plan_tasks()` validates candidates against authoritative done
state, writes exact archive blocks idempotently, archives payloads in SQLite,
and projects PLAN stubs through the recovery outbox. Dispatch, assignment,
maker launch/gate, CONTROL, checker/review, and archival paths expose the same
projection and recovery mechanism. Production lifecycle callers must pass the
main `project_root` consistently; lower-level APIs retain an optional root for
standalone use, so omitting it intentionally leaves PLAN projection disabled.
Archive maintenance supports an opt-in running-cycle interval and remains
disabled by default.

The supervisor now has a bounded checker launch path. It requires a clean
worktree at the passed gate SHA, an unchanged gate receipt registered by HEAD,
an active configured reviewer/judgment worker, and an atomic one-time launch
reservation under the HEAD lease. Maker/checker identity and concrete
runtime/model independence are checked before launch and again when recording
the checker receipt. Strict JSON output is normalized using the invocation's
configured checker identity, then applied through the existing HEAD-leased,
gate-bound review transaction. Provider identity in model output is never
trusted. Live provider execution remains open.

The verification gate accepts per-worktree environment templates with task,
worker, worktree, and task/baseline scope tokens. Template values are injected
into the scrubbed environment, hashed into the gate fingerprint, and redacted
from logs; baseline checks receive a separate scope. A cross-platform,
filesystem-locked test-run cache binds command results to SHA, environment, and
command, and the gate shares its full-test run with callers using the same key.
HEAD can register passed test artifacts to an active task, and CONTROL cannot
move a task to `needs_review` without a registered passed receipt for its exact
reported SHA. The `codexdevteam-test` CLI runs named argv configurations and
returns a safe SHA-bound evidence reference. Deferred CONTROL draining lets
HEAD register the matching test receipt and gate before applying a maker's
review request.
The `codexdevteam init` CLI packages inactive fresh-project defaults or a
DEVDEPARTMENT compatibility sidecar. It selects from read-only onboarding
detection, refuses conflicts, preserves incumbent files, and never activates a
HEAD. Disposable smoke coverage now includes real Codex maker file writes,
normal hook review/trust allow/deny, a single-store HEAD-side CONTROL drain,
task-worktree isolation, the post-run territory gate, and strict-receipt
dispatch. The runtime smoke uses a one-file temporary fixture, with the
harness supplying the maker's omitted commit; it is not the full pilot.

Publishing now has a pure, provider-neutral decision reducer for `every`,
`batch`, and `merge_only` schedules. Publishing remains disabled unless
`git.enabled` is explicitly true; install, HEAD activation, task completion,
and merge/park events do not enable it. Commit/CAS handling, remote operations,
retry/outbox behavior, and process lifecycle remain future publisher-adapter
work; see `docs/PUBLISHING.md`.

Completed kernel task records are retained in SQLite. `archive_older_plan_tasks()`
verifies older-wave PLAN candidates against authoritative `DONE` state, writes
exact monthly blocks idempotently, archives task payloads in SQLite, and applies
the PLAN stub change through the lease-fenced recovery outbox. A running
dispatch cycle now supports opt-in interval scheduling, disabled by default.
See
`docs/PLAN_ARCHIVE.md`.

## Phase 2 — Codex-native HEAD

- [x] Implement Codex HEAD runtime adapter.
- [x] Smoke-test the read-only Codex adapter against the configured live CLI in a trusted checkout.
- [x] Centralize role -> model/effort policy.
- [x] Implement bounded headless invocation + ledger.
- [x] Implement exclusive HEAD lease.
- [deferred beyond v0.2] Implement controlled DEVDEPARTMENT <-> CODEXDEVTEAM handover.
- Handover source/target matrix, operational evidence requirements, an incomplete hash-bound map-template command, a read-only task-map preview, and lease-fenced inactive staging into an empty target state store are documented in `docs/HANDOVER_MATRIX.md`; execution still lacks incumbent process fencing, durable activation, and reverse transfer.
- [x] Prove maker != checker enforcement with mixed runtimes.

## Phase 3 — Verification-first review

Derived from VERIFICATION_GATES_2026-09:
- [x] SHA-bound test-run cache.
- [x] scripted territory/build/typecheck/full-test gate;
- [x] baseline-failure ownership;
- [x] reachability checks;
- [x] mutation checks where applicable;
- [x] worktree-local test resources/config copy;
- [x] mechanical gate before judgment session.
- [x] strict provider-neutral review receipt normalization and HEAD-side application.

## Phase 4 — Routing and evidence memory

Derived from TIERED_ROUTING_AND_MEMORY_2026-09:
- [x] capability-floor routing;
- [x] task-class policy mapping with strict unknown-class handling;
- [x] defined/active/available/eligible/assigned worker-state projection;
- [x] capacity/quota-aware routing;
- [x] bounded run-log fast job with strict schema/evidence validation, one retry, and escalation event;
- [x] optional fast-tier production dispatch integration that can only restrict independently observed capacity;
- [deferred beyond v0.1] representative redacted field-log corpus and measured accuracy before routine enablement;
- Synthetic parser-contract fixtures now cover six categories; they are explicitly not field accuracy evidence.
- Added a read-only labeled-corpus evaluator reporting coverage, kind/evidence/reset-time accuracy, exact combined accuracy, per-class precision/recall/F1, and confusion counts; no representative field corpus or accuracy result is claimed. Synthetic six-case scoring returns 100% against its own labels only and is not field accuracy evidence.
- [x] cited fact storage, scoped retrieval, injection ledger, and lifecycle kernel;
- [x] deterministic miners for artifact-verified test runs, repeated territory conflicts, review catches, and repeated new gate failures;
- [x] automatic memory lifecycle scoring from HEAD-bound review receipts;
- [x] inject already-ledgered, task-bound cited facts into maker context;
- [x] per-role/model invocation outcome and configured cost reporting;
- [x] minimum-sample, first-pass-rate, and cost-per-approval rent comparison for optional tiers.

## Phase 5 — Onboarding and sync

- [x] Fresh-project installer with packaged inactive defaults.
- [x] Existing DEVDEPARTMENT sidecar/adapter installer.
- [x] Existing CODEXDEVTEAM hash-manifest sync/upgrade.
- [x] trusted-checkout read-only live Codex adapter launch smoke;
- [x] disposable fresh-project live Codex maker smoke for owned/unowned structured writes (hook-trust bypass scoped to the disposable project) and CONTROL outbox emission;
- [x] normal Codex hook review/trust and owned-path allow/unowned-path deny smoke in a trusted disposable project.
- [x] live HEAD-side CONTROL drain, task-worktree isolation, post-run territory gate, and strict-capability receipt dispatch smoke in a disposable fixture (maker follow-up supplied by HEAD harness).
- [x] document the current CODEXDEVTEAM/DEVDEPARTMENT compatibility-version matrix;
- [x] add versioned executable metadata-compatibility gates that fail closed without implying activation;
- [x] define the safe handover migration matrix and the pinned Wave E refusal reasons;
- [deferred beyond v0.2] verify process-fenced handover, state translation, and reverse transfer on a real project.

## Phase 6 — Pilot

- [x] Complete a fresh disposable Windows project through maker, host commit, exact-SHA gate, and independent checker. The TextTidy pilot reached `done`; the host commit boundary provides territory enforcement at commit time. Pre-write hook activation is optional and remains unverified in the latest headless attempt; see `docs/RUNTIME_SMOKE.md`.
- Disposable Git-project integration coverage now spans inactive install, hook decisions, CONTROL, test evidence, gate, and review, but uses fixture runtime/checker receipts and does not satisfy the live pilot.
- A live Codex disposable TextTidy utility pilot reached `done` at `a664233e4b5470ebe53a47b0a05ed32849e944b7` with a passing exact-SHA gate and independent checker approval. The Windows host committed the maker's owned-path change and ran the gate because the Codex sandbox could not write shared `.git` metadata. The pilot and deterministic Windows lifecycle regression are evidence for host commit, exact-SHA gate, and independent review; direct hook activation tracing remains open. Details are in `docs/RUNTIME_SMOKE.md`.
- The Windows host-commit boundary is wired into supervisor cycles, gate finalization requires the exact committed SHA, and refused CONTROL is invocation-bound and archived. Refused out-of-scope files are quarantined in host-owned Git metadata and restored out of the worktree; one bounded retry is allowed, then the task is blocked with `OWNERSHIP_CONFLICT`. Windows regressions cover refusal, quarantine, retry, stale-report rejection, and cross-task isolation. The full local Windows contract suite passes (**275 passed, 8 skipped**). Hosted branch-head CI run [37185171206](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37185171206) passed all Ubuntu/Windows × Python 3.11/3.12 jobs. Supported supervised maker commits are Windows-only; Linux is CI coverage and commits fail closed without a verified Windows Job Object proof.
- [deferred beyond v0.2] Hand an existing DEVDEPARTMENT project to CODEXDEVTEAM and back.
- [x] Verify no duplicate claims/reviews under lease contention across two store connections.
- [deferred beyond v0.1] Measure first-pass rate, review sessions, gate rejection rate and model spend.
- A `measure_pilot()` report now exposes these metrics from verified review/gate receipts; real model spend and operational sample data are still required to complete the measurement.
- [x] Tag v0.1 after the fresh-project core was reviewed and merged: `v0.1.0` points to merge commit `bfd2f29a5bf08927b57228209617e94b88ddf0b2` (2026-10-04). DEVDEPARTMENT handover and reverse transfer, including process-fencing evidence, are deferred beyond the standalone v0.2 objective.

## Phase 7 — v0.2 standalone unattended development

v0.2 is complete when a fresh Windows project can start from a project brief,
have Codex produce and mechanically validate its own task plan, select eligible
configured builders, and run a bounded development workflow to reviewed
completion or durable escalation without an operator advancing each task.
DEVDEPARTMENT remains the behavioral reference for supervisor and dispatch
semantics. Existing-project handover, sidecar activation, and reverse transfer
are not v0.2 requirements.

The acceptance pilot uses a newly initialized disposable Git project. It does
not modify a DEVDEPARTMENT checkout or rely on a pre-seeded task plan. The
operator supplies the brief, project boundary, budget, and one-time activation
authorization; after activation, the system owns planning and task progression.

- [x] Harden the continuous runner to refuse dispatch when pre-existing
  `claimed` or `needs_review` tasks need restart recovery, and to journal
  `supervisor.continuous_closeout_incomplete` when a cycle callback leaves a
  maker unresolved. Focused regressions pass. A full Windows run found one
  interaction with the existing in-progress recovery path; the guard was
  narrowed and focused coverage now passes.
- [x] Add `Supervisor.closeout_maker_with_checker()` to sequence a successful
  host commit through the exact-SHA gate, CONTROL drain, independent checker,
  and review ledger. A Windows integration regression reaches `DONE` only
  after the checker approves. Failed gates do not launch a checker; failed or
  changes-requested work remains open for recovery. This performs one review
  attempt and does not automate bounded rework or continuous host lifecycle.
- [x] Bound checker-requested rework in `requeue_changes_requested_task()`.
  The configurable cap defaults to one retry; exceeding it journals
  `supervisor.rework_limit_reached` and refuses another claim. Regression
  confirms the task stays open for human recovery. The Windows host now drives
  the bounded maker/review loop around this policy.
- [ ] Implement the Windows unattended host entrypoint and lifecycle around
  the existing supervisor APIs: explicit activation, continuous lease-safe
  operation, park/resume, clean stop/status, and restart recovery. It must run
  the complete maker -> host commit -> exact-SHA gate -> independent checker ->
  review path, apply a bounded rework policy, and persist or escalate every
  cycle before starting another. A callback that omits closeout cannot count as
  unattended operation. Keep the host parked until explicit activation.
  - [x] Define the inactive, project-relative Windows host configuration
    contract and `host-config` inspection command. The runner and controls are
    tracked in the following items.
  - [x] Define strict timestamped capacity snapshot ingestion; the host must
    still connect a real configured capacity source before dispatch.
  - [x] Add deterministic maker/checker prompt rendering from authoritative
    task records, including ownership/acceptance and exact-SHA review binding.
  - [x] Add a side-effect-free host binding loader for strict worker registry,
    Codex runtime adapters, and concrete verification commands; unsupported
    runtimes fail closed.
  - [x] Add a read-only host preflight command for binding and capacity readiness.
  - [x] Set supervised Codex CLI approval handling to `never` so actions that
    need operator approval fail promptly; keep the configured sandbox active.
  - [x] Allow closeout to render the checker prompt from the passed gate's
    exact SHA and fingerprint.
  - [x] Compose bounded host cycle inputs and mandatory maker gate/checker
    closeout helpers. Restart recovery is tracked below.
  - [x] Add a Windows-only bounded host loop that requires an already-running
    lease and performs closeout before the supervisor advances.
  - [x] Add explicit fresh-project activation that acquires the exclusive HEAD
    lease and refuses DEVDEPARTMENT sidecars.
  - [x] Add `host-run` as the Windows command entrypoint; it requires explicit
    confirmation and an existing task database, and returns parked on normal
    stop or completed bounded run.
  - [x] Add inactive fresh-project PLAN bootstrap with atomic task seeding,
    PLAN-hash provenance, historical archive IDs, and refusal of active or
    completed legacy tasks without CODEXDEVTEAM review receipts. This consumes
    an existing PLAN and does not yet satisfy brief-to-plan setup.
  - [x] Verify between cycles that PLAN's kernel task fields and archive IDs
    still match authoritative state, while allowing only host-projected state
    and assignment changes.
  - [x] Add a lease-authorized stop request that lets current closeout finish,
    then parks mode and releases the lease. The request is local to this host;
    cross-project incumbent fencing remains outstanding.
  - [x] Add a read-only `host-status` report for supervisor mode, lease expiry,
    running maker records, and task-state totals.
  - [x] Drive checker-requested work through the existing durable review ledger
    and bounded requeue policy, passing verified rationale/evidence to the
    same maker. Restart recovery is tracked below.
- [x] Add brief-to-plan bootstrap: Codex drafts a complete task plan, and
  mechanical validation checks task vocabulary, dependencies, acceptance
  criteria, Owned_Paths, and Protected_Grants before creating authoritative
  state. No task-by-task operator advancement is required after authorization.
  - [x] Add `host-plan`: invoke the configured read-only planner candidate,
    validate its strict task JSON with the canonical parser and protocol, reject
    control-path ownership/dependency cycles, and create `PLAN.md` exclusively.
    This writes no task state and does not activate HEAD.
  - [x] Embed durable plan-generation provenance in `PLAN.md`, binding the
    planner identity/runtime/model and invocation ID to brief, response, and
    validated-plan hashes.
  - [x] Connect brief planning, exclusive task-state bootstrap, explicit HEAD
    activation, and supervised execution behind `host-run --brief` with separate
    plan-write and activation confirmations. Contract tests cover this chain
    with a fixture planner and host loop.
  - [x] Capture an actual isolated Windows planner invocation using the
    configured Codex model and bootstrap its two validated tasks while remaining
    parked (`codexdevteam-v02-item2-pilot-ef0d1b8a583448838134514189cfa3f3`, 2026-10-06).
- [x] Add orchestrator task-class routing over configured logical roles and
  capability floors. The planner can request only classes in project policy;
  host loading rejects absent/inactive roles, unknown floors, and checker-role
  assignments. Dispatch still filters for strict verification, fresh capacity,
  and independent maker/checker identities. The complete kernel contract suite
  passes after this change.
- [x] Implement restart recovery for the standalone Windows host using the
  existing lease, invocation-liveness, and Windows Job Object evidence.
  Interrupted work must be safely resumed or durably escalated before dispatch.
  - [x] Add lease-authorized startup scanning, signed cancellation receipt
    recovery, Windows Job Object reaping, durable escalation for interrupted
    claimed/in-progress/review tasks, and park-before-dispatch behavior.
  - [x] Add Windows lifecycle regressions for process-tree reaping success and
    refusal when quiescence cannot be proved; verify preserved task branches.
- [x] Define controlled integration of independently reviewed task branches
  into the project branch, with post-integration mechanical verification and
  rollback or escalation on conflicts.
  - [x] Add exact-approved-SHA integration through a temporary worktree, a
    post-integration configured gate, compare-and-swap project ref update,
    PLAN preservation, and a recoverable host integration journal.
  - [x] Connect approved closeout to integration and startup journal recovery;
    merge conflicts and failed gates create durable escalation and stop the run.
  - [x] Cover stale approvals, merge conflicts, failed post-integration gates,
    successful integration, and recovery after a simulated stop between ref
    advancement and PLAN restore.
  - [x] Cover a concurrent target-ref change during gate execution; the
    integration race regression confirms the external ref is preserved and
    interrupted recovery records a durable escalation.
- [ ] Demonstrate unattended operation on a bounded representative task set
  starting from a project brief: generate/validate the plan, dispatch
  configured makers, run exact-SHA mechanical gates before independent review,
  integrate accepted work, recover or escalate on failures, respect
  budgets/timeouts, and reach a defined terminal state without an operator
  advancing each task.
- [ ] Record live strict-verification evidence for every builder enabled in
  the pilot, bound to its runtime and configured model. Do not infer a model's
  capabilities from another model's receipt.
- [ ] Measure first-pass rate, review sessions, gate rejection, escalations,
  recovery outcomes, elapsed time, and configured model spend from verified
  standalone pilot receipts. Keep optional fast-tier routing disabled until representative
  redacted field logs show the agreed accuracy threshold.
- [x] Pass release CI on Windows with the supported Python versions; Linux
  remains compatibility CI only and is not part of supervised maker execution
  (branch-head run [37610157137](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37610157137)).
- [ ] Publish v0.2 release notes that state proven behavior, enabled runtimes,
  host limitations, recovery steps, and any remaining parity gaps.
  - [x] Draft release notes from current repository evidence; finalize them
    after the bounded live pilot and strict-worker receipts.

### v0.2 release gates

1. A new Git project bootstraps from a brief to a validated authoritative task
   plan without hand-authored task blocks.
2. The Codex orchestrator can request roles and capability floors while
   dispatch stays constrained to configured strict-verified workers, fresh
   capacity, and one active HEAD.
3. The unattended pilot completes the full maker -> host commit -> gate ->
   checker -> bounded rework -> integration workflow, or stops with durable
   recovery/escalation evidence, without an operator advancing tasks.
4. The pilot preserves maker/checker separation, respects configured
   budgets/timeouts, and attributes each run to its configured runtime/model.
5. The measured pilot report and Windows release CI pass; runbooks match the
   evidence collected. v0.2 makes no DEVDEPARTMENT handover or return claim.

## Deferred until evidence justifies them

- automatic safety-posture flips;
- unbounded autonomous model routing;
- memory facts without evidence;
- co-HEAD operation;
- vendor-specific features that cannot degrade cleanly on another runtime.
