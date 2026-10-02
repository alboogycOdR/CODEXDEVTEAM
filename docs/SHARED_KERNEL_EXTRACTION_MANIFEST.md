# Phase 1 — Shared Kernel Extraction Manifest

**Status:** Extraction analysis complete; selective CODEXDEVTEAM implementation
has proceeded after approval. This manifest records extraction decisions and
rationale; current implementation status is tracked in `ROADMAP.md`. No
DEVDEPARTMENT code has been ported or changed.

**Reference inspected:** `C:\Projects\DEVDEPARTMENT` at `2d4202c9c70300f3f5cdf97a0b866c1a15b4c760` (`2026-10-01`, clean checkout; Wave E parked). This is the baseline named by CODEXDEVTEAM's README. The three dated specs were treated as forward design input, not as implemented behavior: verification gates/capacity (G), Claude-native bounded sessions/typed outputs (F), and tiered routing/evidence memory (H).

## Reading key

Each mechanism below has exactly one extraction classification. “Claude-specific” covers CLI invocation, Claude Code hooks/context, identity, model/effort flags and account telemetry. “DEVDEPARTMENT-specific” covers ORCH/GB/CX/S5 identities, PLAN.md conventions, pack ownership, default branch/layout, historical commands and deployment assumptions. Port order numbers are dependency order, not authorization to implement.

## Classification summary

| Classification | Count |
|---|---:|
| PORT AS-IS | 1 |
| ADAPT | 14 |
| REIMPLEMENT | 7 |
| DEFER | 5 |
| DO NOT PORT | 2 |
| **Total** | **29** |

Counts are by manifest row; cross-cutting requirements such as maker/checker and exclusive HEAD are called out separately and are not double-counted as code subsystems.

## Subsystem manifest

### 1. Task protocol, PLAN schema and validator — **ADAPT** (order 1)

- **Source / tests:** `docs/COORDINATION_PROTOCOL.md`, `AGENTS.md`, `PLAN.md`, `scripts/validate_plan.py`, `scripts/plan_health.py`; `tests/test_validate_plan.py`, `test_plan_health.py`, `test_plan_archive.py`, `test_wave_e_exit.py`; PLAN fixture coverage in `tests/fixtures/`.
- **Responsibility / dependencies:** Defines task block fields, legal state transitions, dependencies, acceptance/test evidence, parse and lint behavior, ownership overlap checks, timestamps and review-state rules. It is the schema foundation for dispatch, CONTROL, supervisor and review. Validator also consumes registry, config, review history and repository files for optional checks.
- **Claude assumptions:** Low in parsing; higher in surrounding protocol prose (ORCH is Claude Code; `/devteam-*` commands, CLAUDE.md and session continuity).
- **DEVDEPARTMENT assumptions:** High: Markdown `PLAN.md`, `TASK-*` IDs, `ORCH` and legacy unit enum, `master`/`main` defaults, exact task field spelling and branch conventions.
- **Decision / replacement:** Preserve the proven Markdown protocol and parser semantics initially, but adapt the schema around an explicit versioned task document/API. Unit IDs must be opaque identities; actor authority comes from role/capability and the active HEAD lease, not literal ORCH/GB/CX/S5 checks. Do not turn Markdown into the engine's only internal representation.
- **Abstraction, order, risks/tests:** Add a provider-neutral task protocol package with parse/validate/serialize interfaces and versioned adapters. Depends on configuration identity and filesystem primitives. Risk: schema drift silently loosens territory or permits illegal transitions. Port fixtures and test every transition, malformed/unknown state, cross-version input, timestamps, dependency cycle, missing evidence, and disjoint/overlapping paths. Golden round-trip tests must preserve unknown fields or explicitly reject them.

### 2. Task-state vocabulary and transition authority — **ADAPT** (order 1)

- **Source / tests:** Protocol §2 and `scripts/validate_plan.py`; `scripts/control.py`; `tests/test_validate_plan.py`, `test_control.py`, `test_supervisor_control.py`.
- **Responsibility / dependencies:** `pending → claimed → in_progress → needs_review → done`, with `blocked` exits and authorized unblock/reassignment; prevents builders from self-approving. Depends on actor identity and authoritative state writer.
- **Claude assumptions:** Actor names imply Claude's ORCH is the only reviewer/integrator.
- **DEVDEPARTMENT assumptions:** Exact strings, permissions, and a `done` verdict controlled by ORCH.
- **Decision / replacement:** Keep the lifecycle's intent, adapt vocabulary only through a versioned mapping. Authority derives from role plus single active HEAD; review/merge authority is a capability, not a provider identity. Avoid hidden “unknown means pending” behavior.
- **Abstraction, order, risks/tests:** A versioned state machine and transition policy. Same foundation as row 1. Regression tests enumerate all allowed/forbidden transitions and verify replay/idempotence and explicit human override audit.

### 3. `Owned_Paths`, overlap validation and `Protected_Grants` — **ADAPT** (order 2)

- **Source / tests:** `scripts/validate_plan.py` (`parse_owned_paths`, glob intersection, grants); `scripts/control.py`; `hooks/territory-firewall.js`, `hooks/lib.js`; `docs/COORDINATION_PROTOCOL.md`; `tests/test_validate_plan.py`, `test_control.py`, `test_gateguard.js`, `test_dispatch_worktree.py`.
- **Responsibility / dependencies:** Exclusive task territory at planning, dispatch, and write time. Strict-mode grants are bounded and supervisor-applied; task globs must not conflict with active tasks. Depends on task schema, current actor/task, repository root and hook/runtime integration.
- **Claude assumptions:** The existing firewall is Claude hook-event driven and reads Claude settings/environment. Filesystem path rules themselves are neutral.
- **DEVDEPARTMENT assumptions:** PLAN task fields, builder IDs, `.devteam`/hook configuration and legacy self-report grant forms.
- **Decision / replacement:** Keep the territory invariant and mature glob semantics, adapt enforcement to a host-neutral policy decision API with runtime-specific hook shims. CODEXDEVTEAM's grant schema must be explicit and supervisor-authorized; never silently accept a model-authored widening.
- **Abstraction, order, risks/tests:** `TerritoryPolicy` takes repository, actor, task, operation and proposed paths; adapters map Codex/Claude/runtime events to it. Depends on rows 1–2. Test path traversal, case/separator behavior on Windows and Linux, symlink escapes, glob semantics, unowned writes, protected grants, concurrent task overlap, and fail-closed unknown identity.

### 4. Worktree lifecycle and branch isolation — **ADAPT** (order 3)

- **Source / tests:** `scripts/worktree.ps1`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`; `tests/test_worktree_ps1.py`, `test_dispatch_worktree.py`; `docs/COORDINATION_PROTOCOL.md` isolation section.
- **Responsibility / dependencies:** Creates/reuses/removes task worktrees, maps task/unit to branch and sibling path, guards against unrelated directories, handles detached task branches and runner shutdown. Depends on Git, task identity, registry suffixes, base branch and platform process semantics.
- **Claude assumptions:** None intrinsic; launch lifecycle includes Claude process-specific flags in dispatch.
- **DEVDEPARTMENT assumptions:** Project-folder-based sibling naming; branch prefix/suffix `task/TASK-...`; `autopilot.json` base branch; PowerShell 5.1 and Bash/Git Bash details.
- **Decision / replacement:** Preserve proven Git safety checks but adapt path and branch policy into configured `WorktreeBackend`/`GitWorktreeManager`. Worktree paths must be collision-safe across projects and identify owning repository; no path is trusted solely by its name.
- **Abstraction, order, risks/tests:** Provider-neutral worktree interface; OS-specific implementations can share policy. After protocol/registry identity, before dispatch. Test path collisions, wrong-repo registration, existing non-worktree collision, stale runner cleanup, branch/worktree drift, spaces/non-ASCII paths, interrupted create/remove, and Windows/Ubuntu parity.

### 5. Dispatch and bounded runtime launch — **REIMPLEMENT** (order 6)

- **Source / tests:** `scripts/dispatch.sh`, `scripts/dispatch.ps1`; registry access through `scripts/builder_registry.py`; `tests/test_dispatch_worktree.py`, `test_worktree_ps1.py`, `test_test_env_scrub.py`.
- **Responsibility / dependencies:** Selects/resumes work, resolves a unit, prepares its worktree, constructs prompts/environment/CLI args, launches foreground/background sessions, records process identity and output. Depends on routing, task protocol, worktree, runtime adapter, briefing/context construction and later ledger.
- **Claude assumptions:** Significant: `claude -p`, `--agent`, permission mode, config-dir auth, Claude context files and output behavior. Codex/Grok are currently special-case rows, not a general runtime contract.
- **DEVDEPARTMENT assumptions:** Unit CLI aliases, briefings, task branch naming, PLAN claim route and dispatch command interface.
- **Decision / replacement:** Reimplement behind one provider-neutral dispatcher. Runtime adapters own argv, prompt/context, auth, session resume, typed output, ceilings, cancellation and result parsing. Keep Codex/GPT concrete model IDs in configuration. GPT/Codex starts as HEAD; role, capability floor, runtime and model are independent fields.
- **Abstraction, order, risks/tests:** `RuntimeAdapter` plus `DispatchService`, with no provider CLI flags in policy code. Depends on rows 1–4 and registry. Test argument boundary/quoting, secret environment scrubbing, cancellation/reaping, time/budget limits, resume semantics, unsupported output, runtime failure, and non-dispatch under stale/ambiguous HEAD lease.

### 6. Builder registry / worker identity — **ADAPT** (order 2)

- **Source / tests:** `scripts/builder_registry.py`, `autopilot.json`, `docs/BUILDER_REGISTRY.md`; `tests/test_builder_registry.py`, registry fixture `tests/fixtures/smoke/codex-read-only-registry.json`.
- **Responsibility / dependencies:** Normalizes legacy and object registry forms; distinguishes defined/active units and resolves CLI family, model, auth, branch suffix, briefing, usage and identity. Fail-open roster listing but fail-closed specific unit resolution is deliberate.
- **Claude assumptions:** Registry supports `cli: claude`, `CLAUDE_CONFIG_DIR`, native agent identity and Claude usage provider.
- **DEVDEPARTMENT assumptions:** Unit IDs and fields encode a CLI-family table; active/defined roster and defaults reflect ORCH/GB/CX/S5.
- **Decision / replacement:** Adapt schema and preserve fail-closed specific resolution. Make identity ID, role, capability floor, runtime/provider, model, auth, host/machine affinity, control mode and capacity distinct. Store model IDs only in config; policy names roles/capabilities, not vendors. “Defined, active, available, eligible, assigned” stay separate states per H design.
- **Abstraction, order, risks/tests:** Versioned `WorkerRegistry` and `WorkerIdentity` types. After task identity, before territory binding/dispatch. Tests cover malformed config, duplicate IDs, unavailable versus inactive, role/model separation, runtime adapter lookup, eligibility floors, and unknown identity fail-closed.

### 7. CONTROL, strict mode and single-writer state — **ADAPT** (order 4)

- **Source / tests:** `scripts/control.py`, `docs/CONTROL.md`, dispatch scripts, `hooks/territory-firewall.js`; `tests/test_control.py`, `test_supervisor_control.py`, `test_supervisor.py`.
- **Responsibility / dependencies:** Parses a structured builder report, validates task/unit correspondence, and applies state transitions atomically; dispatch claims tasks and supervisor alone writes authoritative PLAN in strict mode. Queue and inflight files coordinate events. Legacy mode lets builders edit their own blocks.
- **Claude assumptions:** Current transport is fenced `devteam-control` output in a prompt/log; future F3 spec proposes Claude JSON schema CLI output.
- **DEVDEPARTMENT assumptions:** PLAN.md target, `.devteam/control` file paths, `SV` writer name, legacy/strict switch and existing unit IDs.
- **Decision / replacement:** Adapt the proven strict single-writer rule as the default target for builders verified live. Reimplement transport parsing as schema-validated messages; the authoritative state store is the kernel. Loss/ambiguity of exclusive HEAD lease blocks orchestration writes. Installation never activates HEAD.
- **Abstraction, order, risks/tests:** `ControlMessage` schema and transactional `StateRepository`; runtime adapters produce typed messages, not state mutations. Depends on protocol, registry and lease. Test malformed/forged message, wrong unit/task, duplicate/out-of-order events, crash between queue/apply, concurrent claim, idempotent replay, lease loss, and no direct builder write in strict mode.

### 8. HEAD lease and handover — **REIMPLEMENT** (order 4)

- **Source / tests:** No Wave E implementation; conceptual basis only in CODEXDEVTEAM `docs/CONSTITUTION.md` §§3–4, `docs/INTEROPERABILITY.md` and `docs/ONBOARDING.md`.
- **Responsibility / dependencies:** Ensures exactly one control plane per project, durable acquisition/heartbeat/release, fail-closed writes on lease ambiguity, and explicit state-preserving handover between DEVDEPARTMENT and CODEXDEVTEAM. Depends on atomic filesystem/Git coordination and protocol-version inspection.
- **Claude assumptions:** None in the new invariant; incumbent adapter must recognize Claude as DEVDEPARTMENT runtime.
- **DEVDEPARTMENT assumptions:** Handover must discover its running supervisor, CONTROL queue and inflight work without claiming ownership of its framework files.
- **Decision / replacement:** New subsystem. Lease identity includes OS, runtime, instance, protocol, time/heartbeat and fencing generation. Acquire must be atomic and resistant to stale owner writes; install is distinct from activation.
- **Abstraction, order, risks/tests:** `HeadLease`/handover service independent of model runtime. Before CONTROL writes and autonomy. Test simultaneous acquisition, expired/ambiguous lease fail-closed, stale generation write rejection, crash/park/reacquire, inflight task preservation, and DEVDEPARTMENT→CODEXDEVTEAM→DEVDEPARTMENT round trip without duplicate claim/review.

### 9. Supervisor decision engine and autonomy loop — **DEFER** (order 12)

- **Source / tests:** `scripts/supervisor.py`; `tests/test_supervisor.py`, `test_supervisor_once_inbox.py`, `test_supervisor_control.py`, `test_supervisor_telegram.py`, `tests/tick_harness.py`.
- **Responsibility / dependencies:** Derives actions from task/state snapshots, launches/reaps dispatch/review/triage, owns retries, timers, maintenance, notifications, routing and control queues. Deeply coupled to nearly every other subsystem.
- **Claude assumptions:** Review/triage command templates may invoke Claude headless; model discipline and usage assumptions flow through config.
- **DEVDEPARTMENT assumptions:** `ORCH`, `/devteam-*`, PLAN/REVIEW Markdown, Telegram/Slack, PM2/Tower and exact config shape.
- **Decision / replacement:** Defer until kernel, HEAD lease, gate and runtime adapter are proven. Do not port monolithic supervisor wholesale. Later split pure policy/decision reducer from effectful runtime, storage, notification and scheduling services.
- **Abstraction, order, risks/tests:** Durable event/state machine plus injectable effect ports. After all kernel contracts, verification gate, bounded invocations and lease. Replay existing `decide()` fixtures, tick harness, duplicate-action tests, crash/restart, fairness and no action under invalid lease.

### 10. Park/resume, inflight recovery and session continuity — **ADAPT** (order 5)

- **Source / tests:** `scripts/supervisor.py` durable inflight and park logic; `scripts/worktree.ps1`; protocol §10; `tests/test_supervisor_park.py`, `test_supervisor_once_inbox.py`, `test_dispatch_worktree.py`.
- **Responsibility / dependencies:** Records running jobs, reaps after restart, stops/parks cleanly, resumes same task/worktree first, and avoids duplicate assignment. Depends on process identity, durable state, lease and runtime-specific resume support.
- **Claude assumptions:** Spec F6 proposes `claude --resume`; current continuity prose refers to Claude compaction/MEMORY.md. That mechanism cannot be generalized as model memory.
- **DEVDEPARTMENT assumptions:** Status names, task branch format, supervisor PID, PLAN progress notes and one ORCH supervisor.
- **Decision / replacement:** Preserve durable task/worktree recovery; adapt conversation resumption to runtime adapter capability and treat it as optional. Dossier/task state is source of truth; cold-start remains valid fallback.
- **Abstraction, order, risks/tests:** Runtime reports `resume_token` as opaque metadata; orchestrator decides resume versus cold start. Depends on registry/dispatch/lease. Test supervisor crash while child lives, orphan cleanup, resumed task before new assignment, stale-token fallback, stagnant cold restart, and handover with inflight work.

### 11. Escalation and review/session ledgers — **ADAPT** (order 8)

- **Source / tests:** `scripts/supervisor.py` escalation de-duplication/backoff and review locks; `scripts/team_stats.py`; `REVIEW.md`; tests `test_supervisor_ledgers.py`, `test_supervisor.py`, `test_supervisor_telegram.py`, `test_team_stats.py`, `test_notify_needs_review.py`.
- **Responsibility / dependencies:** Dedupe/renotify escalations, bound retries, serialize reviews, record verdict/session/unit/task events, derive first-pass/rework/cost metrics. Depends on stable worker identity, typed verdict schema, durable event storage and notifier adapters.
- **Claude assumptions:** F2 ledger's `model`, `effort`, Claude session ID and cost fields are source-runtime shaped; Claude has richer cost telemetry than other CLIs.
- **DEVDEPARTMENT assumptions:** `P0/P1/P2`, REVIEW.md rows and Telegram/Slack delivery conventions.
- **Decision / replacement:** Adapt events to a provider-neutral append-only ledger: role, worker identity, runtime, configured model, session ref, task, input/output evidence, bounded cost/usage fields and exit. Missing cost is `unknown/null`, not zero. Human override is explicit and auditable.
- **Abstraction, order, risks/tests:** `RunReceipt`, `ReviewVerdict`, `EscalationEvent` schemas with notifier ports. Depends on registry, dispatch and review gate. Test idempotent dedupe, competing review locks, invalid/missing telemetry, model identity history, maker/checker mismatch refusal and ledger recovery.

### 12. Stale/stagnation detection and circuit breaker — **ADAPT** (order 9)

- **Source / tests:** `scripts/circuit_breaker.py`, supervisor `_stagnation_signal`, `scripts/plan_health.py`; `tests/test_circuit_breaker.py`, `test_stagnation_signal.py`, `test_plan_health.py`, `test_supervisor.py`.
- **Responsibility / dependencies:** Detects no-progress tasks from timestamps/diff/heartbeats, chooses repair action, limits repeated failures and builder denials. Depends on state timestamps, dossiers, worktree diff, per-unit runtime events and supervisor policy.
- **Claude assumptions:** GateGuard denial count is derived from Claude hook output; error classifiers may be CLI strings.
- **DEVDEPARTMENT assumptions:** Progress_Notes and dossier mtime, `Updated_At`, unit-local reset counters and chosen action labels.
- **Decision / replacement:** Adapt the deterministic streak/reset logic; replace provider-text matching with structured runtime exit/error categories and pluggable adapters. Avoid inferring stagnation from a single model message.
- **Abstraction, order, risks/tests:** `HealthSignal`/`FailureClass` API; after state/receipt ledger. Test clock boundaries, malformed dates, no-diff-but-heartbeat, diff-without-note, quota versus auth versus tool failure, repeated resets, and that breaker opens to human escalation rather than looping.

### 13. GateGuard first-touch prompting — **DO NOT PORT** (order — none)

- **Source / tests:** `hooks/gateguard.js`, `docs/GATEGUARD.md`; `tests/test_gateguard.js`.
- **Responsibility / dependencies:** Prompts builders for impact facts on first edit/new file and destructive commands, memoizing routine gates per session. Depends on Claude PreToolUse hook event, session memory, shell parsing and task territory.
- **Claude assumptions:** Essential: Claude Code hook protocol, tool names, JSON response shape and per-session hook lifecycle.
- **DEVDEPARTMENT assumptions:** Builder-only defaults, environment names and Owned_Paths prompt content.
- **Decision / rationale:** Do not port the interactive prompting mechanism. It is not deterministic verification and would reproduce a vendor-specific intervention layer. Reconsider only if CODEXDEVTEAM identifies an equivalent runtime-neutral user approval API; keep deterministic path firewall/gates separately.
- **Replacement / order / risks/tests:** User approval service and runtime policy adapter if later required. Risk is relying on a prompt as a safety gate or making unattended runs hang. No initial kernel dependency; future tests must cover denial, timeout, unattended mode and no bypass via alternate tool names.

### 14. Territory firewall and session hooks — **ADAPT** (order 10)

- **Source / tests:** `hooks/territory-firewall.js`, `hooks/lib.js`, `hooks/hooks.json`, `hooks/session-start.js`, `hooks/session-end.js`, `hooks/pre-compact.js`; tests `test_gateguard.js`, `test_harness_smoke.py`, `test_test_env_scrub.py` (runtime-specific portions).
- **Responsibility / dependencies:** Enforces write-deny outside Owned_Paths and protected files, injects session context, captures session events, and integrates hook registration. Depends on host event API, actor identity, active task, territory policy and configuration.
- **Claude assumptions:** Hook names/event JSON, `.claude/settings.json`, tool input shapes and transcript lifecycle.
- **DEVDEPARTMENT assumptions:** `DEVTEAM_UNIT`, PLAN.md protection and dossier exception.
- **Decision / replacement:** Adapt core decisions into the provider-neutral territory policy; keep thin adapters per supported runtime. A missing/unknown actor must fail closed. Do not assume all runtimes can intercept all writes; record verified enforcement capability per runtime before strict eligibility.
- **Abstraction, order, risks/tests:** Runtime capability interface for pre-write checks plus independent filesystem/worktree review as backstop. After territory policy, before strict-mode worker verification. Test each event adapter contract, command/path aliases, unknown identity, protected grants, unavailable hook and post-run diff firewall.

### 15. Secret scanning — **PORT AS-IS** (order 10)

- **Source / tests:** `hooks/secret-scan.js`, shared `hooks/lib.js`, `hooks/hooks.json`; `tests/test_gateguard.js` (inspect existing test coverage before extraction; a dedicated scanner suite is required).
- **Responsibility / dependencies:** Scans changed content for common credential patterns, with allowlist/false-positive handling and hook response. It is a useful low-cost hygiene check independent of model behavior.
- **Claude assumptions:** Scanner patterns are neutral; hook invocation/response wrapper is Claude Code-specific.
- **DEVDEPARTMENT assumptions:** Hook registration location and project-level exceptions.
- **Decision / rationale:** Port scanner rules and pure detection behavior unchanged as a provider-neutral utility; adapt only the input/output shell. It is the sole direct port recommendation because its narrow pure responsibility has no ORCH/task semantics.
- **Abstraction, order, risks/tests:** `SecretScanner.scan(diff/content) -> findings`; runtime adapters can report/block. After shared path/config infrastructure. Add regression fixtures for supported token patterns, false-positive allowlisting, binary/large input, redaction (never print matched secret), and both hook and CLI invocation.

### 16. Review workflow, maker/checker and verification gate — **REIMPLEMENT** (order 7)

- **Source / tests:** `scripts/supervisor.py` `_run_review`; `REVIEW.md`; review command `.claude/commands/devteam-review.md`; `tests/test_supervisor.py`, `test_supervisor_ledgers.py`, `test_notify_needs_review.py`; forward spec F3 typed verdict and G-A/G-B verification gate.
- **Responsibility / dependencies:** Evaluates task/spec/diff, records approved/rework, merges only after approval; current model review is preceded by review-lock and includes its own test judgment. Future spec requires mechanical gate first, SHA-bound test evidence, maker≠checker by recorded unit and model, and typed review output.
- **Claude assumptions:** Current invocation uses Claude review command/CLI and review prompt format. F3 JSON schema invocation is also Claude CLI-specific.
- **DEVDEPARTMENT assumptions:** ORCH reviewer, REVIEW.md row format, `main` merge policy, exact review labels.
- **Decision / replacement:** Reimplement review orchestration to satisfy CODEXDEVTEAM constitution: run deterministic checks first; only then spend judgment session; typed provider-neutral verdict; mechanically refuse same maker/checker unit or model unless human override. The reviewer checks judgment questions, not mechanically provable facts.
- **Abstraction, order, risks/tests:** `VerificationGate` artifact bound to exact SHA, `ReviewProvider` typed verdict and `MergePolicy`. Depends on kernel, dispatch, ledgers, and gate config. Tests must include failing territory/build/typecheck/test, skipped checks never treated as passed, stale SHA cache, unowned baseline failures, maker unit/model collision and auditable override, malformed verdict, and no model session after mechanical failure.

### 17. Push policy and PLAN-only commits — **ADAPT** (order 11)

- **Source / tests:** `scripts/push_policy.py`, `scripts/plan_commit.sh`, `scripts/plan_commit.ps1`, `scripts/plan_guard.py`, `scripts/plan_stamp.py`; `tests/test_push_policy.py`, `test_plan_commit.py`, `test_plan_guard.py`.
- **Responsibility / dependencies:** Prevents code from reaching base branch via coordination commit; CAS-replays task block changes; pushes bookkeeping/batch commits only under configured policy and quiet period. Depends on Git refs, base branch, task markdown and remote availability.
- **Claude assumptions:** None material.
- **DEVDEPARTMENT assumptions:** PLAN.md as coordination state, `main`/`master`, Conventional Commit suffixes, auto-push event labels.
- **Decision / replacement:** Adapt safety properties, not scripts wholesale. Kernel state writes should use transactional storage; Git publishing remains explicit policy and must not be coupled to acquiring HEAD. Keep batch push configurable; never infer permission to publish from install or task completion.
- **Abstraction, order, risks/tests:** `GitPublisher`/`CoordinationStore` interfaces. After authoritative state schema. Test only-path commits, concurrent CAS/replay, detached/main branch behavior, network failure, quiet-period threshold, bookkeeping vs integration, and refusal to push unreviewed code.

### 18. Plan archival and notes rotation — **ADAPT** (order 11)

- **Source / tests:** `scripts/plan_archive.py`, maintenance integration; `tests/test_plan_archive.py`, fixture `tests/fixtures/plan_archive/sample_plan.md`, `test_maintenance.py`.
- **Responsibility / dependencies:** Moves completed task blocks to monthly archives and caps orchestrator notes to keep active plan manageable. Depends on PLAN Markdown block semantics and date/config.
- **Claude assumptions:** “Orchestrator notes” is the state of a Claude-like HEAD persona, but archive rules are otherwise neutral.
- **DEVDEPARTMENT assumptions:** PLAN.md frontmatter and `plan/archive/YYYY-MM.md` layout.
- **Decision / replacement:** Adapt after choosing durable task-store representation. Preserve data and resolvable history; archival must not erase interoperability evidence or referential links. If first kernel uses Markdown, utility may port later with an adapter.
- **Abstraction, order, risks/tests:** `TaskArchive` over versioned store. After protocol storage and sync ownership. Tests must prove all completed tasks recover byte/field-accurately, active tasks never archive, repeat operation idempotent, and dates/time-zone boundaries.

### 19. Pack/project sync ownership and synchronization — **REIMPLEMENT** (order 13)

- **Source / tests:** `sync-manifest.json`, `scripts/sync_from_pack.py`, `docs/SYNC.md`, `scripts/retire_unit.py`; `tests/test_sync_from_pack.py`, `test_sync_adopt.py`, `test_retire_unit.py`.
- **Responsibility / dependencies:** Three-way sync uses framework/project/merge-special classifications plus prior baseline to update clean pack files, report conflicts, merge selected configuration add-only, and leave project-owned data intact. It also manages registry rendering and retired-unit cleanup.
- **Claude assumptions:** Special merge rules for CLAUDE.md and `.claude/settings.json`; some generated roster/briefing conventions are Claude-specific.
- **DEVDEPARTMENT assumptions:** “Pack” and “project” are distinct, file list embodies DEVDEPARTMENT ownership, marker names encode ORCH instructions, adoption can be pack-ward.
- **Decision / replacement:** Reimplement CODEXDEVTEAM's ownership manifest and sync engine rather than reusing the DEVDEPARTMENT manifest. Keep three-way/conflict behavior as design evidence. For an existing DEVDEPARTMENT project, DEVDEPARTMENT retains root framework ownership; install only CODEXDEVTEAM adapter/handover artifacts in manifest-proven nonconflicting paths. Never overwrite incumbent files, silently merge both frameworks, or activate on install.
- **Abstraction, order, risks/tests:** Versioned `OwnershipManifest` with explicit framework-owned, project-owned, merge-special, and incumbent-owned paths; sync emits plan/report then applies only authorized writes. Depends on onboarding/protocol version. Test clean upgrade, local conflict, mixed ownership, missing baseline, DEVDEPARTMENT sidecar install, no overwrite, rollback, and no implicit HEAD acquisition.

### 20. Onboarding and upgrade/install flows — **ADAPT** (order 13)

- **Source / tests:** `onboard.md`, `scripts/sync_from_pack.py`, `sync-manifest.json`, `scripts/harness-audit.sh`/`.ps1`; tests `test_sync_from_pack.py`, `test_sync_adopt.py`, `test_harness_smoke.py`, `test_wave_e_exit.py`.
- **Responsibility / dependencies:** Fresh install, update, registry initialization, hooks and commands setup, compatibility audit. Current onboarding combines filesystem edits with Claude-led decisions and human-gated settings changes.
- **Claude assumptions:** Opens Claude Code at project root, edits `.claude/settings.json`, asks for Claude/Grok/Codex CLIs and prompts for manual CLI usage.
- **DEVDEPARTMENT assumptions:** Installs the entire pack and may append ORCH content to CLAUDE.md.
- **Decision / replacement:** Adapt to three explicit modes in CODEXDEVTEAM ONBOARDING.md: fresh, existing DEVDEPARTMENT sidecar, existing CODEXDEVTEAM upgrade. Separate detect/plan/verify/apply; never equate install with activation. Live launch/write/CONTROL checks must be reported as verified versus inferred.
- **Abstraction, order, risks/tests:** `InstallPlan` from compatibility probe and ownership manifest; activation is a separate explicit HEAD handover workflow. Depends on sync manifest, runtime adapters and lease. Test dry-run/apply agreement, preserve local tuning, missing prerequisite fails (not empty success), no DEVDEPARTMENT overwrite, no auto-activation, and upgrade rollback.

### 21. Board, notifications and remote control — **DEFER** (order 15)

- **Source / tests:** `scripts/board_publisher.py`, `board/index.html`, `scripts/notify.py`, `scripts/commands.py`, `scripts/tg_listener.py`, `scripts/tg_commands.py`, Slack/Tower modules; `tests/test_board_publisher.py`, `test_commands.py`, `test_tg_commands.py`, `test_tg_listener.py`, `test_notify.py`, `test_slack_listener.py`, `test_slack_notify.py`, `test_tower_sync.py`, `test_supervisor_telegram.py`.
- **Responsibility / dependencies:** Human-visible board/digests and inbound remote commands (`/approve`, `/rework`, `/stop`, etc.), with allowlists, offset persistence and PLAN microtransactions. Depends on authoritative state API, auth/secrets, transport and notifier configuration.
- **Claude assumptions:** Board data includes unit names/avatars and status presentation; otherwise transports are generic.
- **DEVDEPARTMENT assumptions:** Telegram grammar, ORCH command authority, PLAN file patch semantics, Slack/Tower channels and deployment assumptions.
- **Decision / replacement:** Defer. First prove local kernel and lease, then choose a product-neutral event/API. Remote approval must be authenticated, scoped, replay-safe and auditable; do not port a transport before command authority exists.
- **Abstraction, order, risks/tests:** Notification and command adapters over state/lease API. Later than supervisor core. Tests: unauthorized sender silent/rejected safely, replayed offsets, stop remains effective under malformed state, approval maker/checker override audit, stale data labeling, secrets never reach board/log.

### 22. Learning, instincts and distillation — **DEFER** (order 16)

- **Source / tests:** `INSTINCTS.md`, `scripts/instincts.py`, `scripts/distiller.py`, `scripts/retro.py`; `docs/LEARNING.md`; `tests/test_instincts.py`, `test_instincts_lifecycle.py`, `test_distiller.py`, `test_retro.py`, `test_supervisor_learning.py`.
- **Responsibility / dependencies:** Injects territory-matched advice, tracks confidence/wins/losses, mines review outcomes, drafts amendments and retros. Deterministic lifecycle is separated from model-drafted text, and constitutional amendments require approval.
- **Claude assumptions:** `distiller.call_model` invokes Claude; prompting/output parsing assumes its prose format.
- **DEVDEPARTMENT assumptions:** INSTINCTS Markdown vocabulary, `AMEND-*`, ORCH applies constitutional edits and Claude project files are amendment targets.
- **Decision / replacement:** Defer until outcomes are reliably measured. The H spec is useful future direction: evidence citations, promotion/demotion/retirement, privacy and rent tests; no unaudited generated folklore in policy. Retain no model-generated rule as authority.
- **Abstraction, order, risks/tests:** Evidence-backed memory service with source event references, confidence, scope, wins/losses and retirement. Depends on ledgers, review schema, and routing. Test fabricated evidence rejection, loss/retirement, cross-project privacy, secret scan, and no autonomous policy mutation.

### 23. ATLAS project graph / code-memory subsystem — **DEFER** (order 16)

- **Source / tests:** `scripts/atlas.py`, `atlas_core.py`, `atlas_cards.py`, `atlas_episodes.py`, `atlas_pack.py`; `docs/ATLAS.md`, `specs/DEVDEPARTMENT_ATLAS_SPEC.md`; `tests/test_atlas_core.py`, `test_atlas_cards.py`, `test_atlas_episodes.py`, `test_atlas_pack.py`.
- **Responsibility / dependencies:** Builds project graph/facts, impact queries, episode/card storage and cross-project pack/import. Has DB lifecycle, embedding/index or parser assumptions and optional operation paths.
- **Claude assumptions:** Card drafting and episode summaries call Claude/model commands; prompt/output shape is provider-specific.
- **DEVDEPARTMENT assumptions:** `.devteam` SQLite, pack ownership, current ATLAS CLI/data schema and DEVDEPARTMENT learning concepts.
- **Decision / replacement:** Defer: not necessary for smallest shared kernel and introduces derived data migration/privacy complexity. Future H memory facts may use its useful evidence/source model, but re-evaluate against a neutral project-index interface.
- **Abstraction, order, risks/tests:** Optional `ProjectIndex`/`EvidenceMemory` provider. After gate and durable ledger. Test DB corruption/rebuild, stale index, cross-project privacy boundary, source SHA provenance and graceful disabled mode.

### 24. Usage windows, budgets, capacity and routing — **REIMPLEMENT** (order 14)

- **Source / tests:** `scripts/usage_probe.py`, `scripts/budget.py`, `scripts/builder_registry.py`, `scripts/supervisor.py`; `docs/USAGE.md`; `tests/test_usage.py`, `test_budget.py`, `test_builder_registry.py`, `test_supervisor.py`. Forward specs G-E and H-A/B/F.
- **Responsibility / dependencies:** Meters CLI-specific rolling usage, limits dispatch rate/quiet hours, classifies quota failures, probes/retries/failovers units, selects by task heuristics and reports rent/outcomes.
- **Claude assumptions:** Usage probe parses Claude telemetry and Codex telemetry separately; F/H proposals name Claude flags, models and CLI error strings.
- **DEVDEPARTMENT assumptions:** `UNIT_TO_PROVIDER`, named builders, hourly ceiling, priority vocabulary and assignment heuristics.
- **Decision / replacement:** Reimplement policy around provider-neutral capacity signals and explicit role/capability floors; adapters may supply verified usage/error telemetry. Quota is routing state, not surprise; concrete model IDs/limits stay in config. Tier routing and evidence memory remain future stages after base dispatch correctness.
- **Abstraction, order, risks/tests:** `CapacityProvider` and `RoutingPolicy` consume available/defined/eligible/assigned states and outcome evidence. After registry, bounded invocation and ledger. Tests for unknown telemetry, stale cache, failover allowlist, reviewer-family exclusion, machine affinity, unavailable unit and optional-tier rent/regression.

### 25. Verification gate and SHA-bound test evidence — **REIMPLEMENT** (order 7)

- **Source / tests:** Wave E pieces `scripts/preflight_paths.py`, `scripts/test_env_scrub.py`, `hooks/run-tests.js`, CONTROL test evidence checks; `tests/test_preflight_paths.py`, `test_test_env_scrub.py`, `test_control.py`; forward G-A–G-D.
- **Responsibility / dependencies:** E baseline offers path preflight and environment scrubbing but not the requested complete review gate. G specifies territory/build/typecheck/full tests, reachability/mutation, baseline failure ownership, SHA cache, isolated worktree resources, visible skipped checks and pre-review ordering.
- **Claude assumptions:** Hook-based test trigger is Claude-specific; gate requirements are otherwise neutral. F3 typed verdict transport has a Claude-specific proposal.
- **DEVDEPARTMENT assumptions:** `autopilot.json`, task block naming, `.devteam` cache paths and supervisor review sequence.
- **Decision / replacement:** New neutral service informed by G, not a claimed existing Wave E subsystem. Every configured check reports passed/failed/skipped, absent command is skipped, never success. Mechanical rejection must happen before judgment. Bind artifacts to SHA, command/config/environment fingerprint.
- **Abstraction, order, risks/tests:** `GateRunner` writes immutable `GateResult`; adapter supports project commands. After task/worktree/CONTROL baseline, before review workflow. Test red fixtures (cross-package failure, wrong SHA, missing command, unowned baseline failure, leaked env secret, worktree resource collision, mutation/reachability failure) and assert no reviewer launches on failed gate.

### 26. Cross-platform process/runtime portability — **ADAPT** (order 3–6)

- **Source / tests:** `scripts/dispatch.sh`, `dispatch.ps1`, `worktree.ps1`, `plan_commit.sh`, `plan_commit.ps1`, `scripts/autopilot-tick.ps1`, hooks; `tests/test_dispatch_worktree.py`, `test_worktree_ps1.py`, `test_plan_commit.py`, `test_harness_smoke.py`, `test_wave_e_exit.py`.
- **Responsibility / dependencies:** Bash/Linux and Windows PowerShell 5.1 implementations aim for behavioral parity; shared Python logic is used where possible. Handles process trees, Git Bash/native Python path mismatch, UTF-8 bytes, detached windows, quoting and path normalization.
- **Claude assumptions:** `claude` executable and Claude hooks on both platforms.
- **DEVDEPARTMENT assumptions:** Current Windows 11 and Ubuntu 24.04 deployment targets, PM2/Tailscale Watchtower and chosen shell versions.
- **Decision / replacement:** Adapt the proven parity discipline, not the exact deployment assumptions. Establish Windows + Ubuntu CI before autonomy. Keep policy/data logic shared; isolate process, hook and path differences behind tested platform adapters. Declare supported runtime capabilities rather than implying parity.
- **Abstraction, order, risks/tests:** Cross-platform `ProcessRunner`, `PathPolicy` and adapter contract. Required from first worktree/dispatch slice. CI tests path spaces/unicode, quoting, environment, process termination, locks, timestamps and Git common-dir resolution on both OSes.

### 27. Tests, fixtures and CI/audit harness — **REIMPLEMENT** (order 0)

- **Source / tests:** Reference `tests/` suite and fixtures; `scripts/harness-audit.sh`, `scripts/harness-audit.ps1`, `.github/workflows/*` (if present at the pinned commit), `tests/test_wave_e_exit.py`, `tests/test_harness_smoke.py`, `tests/tick_harness.py`.
- **Responsibility / dependencies:** Regression suite protects historical edge cases; harness runs Python and Node checks, plan validation and optional external security scan. Tests are valuable specifications, but broad legacy tests assume existing layout and behavior.
- **Claude assumptions:** Node hook tests and live CLI smoke checks target Claude/Codex executables.
- **DEVDEPARTMENT assumptions:** Pack layout, PLAN/REVIEW fixtures, script entrypoints and external AgentShield availability.
- **Decision / replacement:** Reimplement CI around CODEXDEVTEAM packages and supported OS/runtime matrix. Port behavior-focused fixtures selectively; do not bulk-copy tests that validate deprecated names or coupling. Require Windows and Ubuntu CI before adding supervisor autonomy per roadmap.
- **Abstraction, order, risks/tests:** CI starts before extraction code. Kernel gates: protocol, path policy, worktree, control/lease and secret scanner contract suites. Include static checks and offline fake-runtime tests; separate live integration evidence from unit-test claims. Risk: green copied tests may encode the old product rather than the new invariants.

### 28. Deployment, maintenance and scheduled self-audit — **DEFER** (order 17)

- **Source / tests:** `scripts/maintenance.py`, `scripts/scheduling.py`, `scripts/autopilot-tick.ps1`, `deploy/ecosystem.config.js`, `docs/DEPLOY_CLAWSRV.md`; `tests/test_maintenance.py`, `test_scheduling.py`, `test_supervisor_maintenance.py`, `test_tower_sync.py`.
- **Responsibility / dependencies:** Nightly harness/validator/test/hygiene/backup and archive checks; PM2 long-running deployment and remote Watchtower notification. Depends on stable install layout, supervisor and operational support.
- **Claude assumptions:** Scheduled audit runs selected framework commands; actual dispatch/review authentication is tied to installed CLIs on hosts.
- **DEVDEPARTMENT assumptions:** PM2, Ubuntu VPS, Tailscale and DEVDEPARTMENT audit task format.
- **Decision / replacement:** Defer until manual fresh-project and interoperability pilots show the kernel stable. Revisit scheduled maintenance with platform-neutral scheduler/health interfaces.
- **Abstraction, order, risks/tests:** Scheduler adapter and `HealthReport`; after supervisor and onboarding. Test duplicate schedule suppression, unavailable worker escalation, backup restore, audit cannot mutate safety posture and missing checks visible.

### 29. Historical DEVDEPARTMENT-only identity, prompt and command surface — **DO NOT PORT** (order — none)

- **Source / tests:** `CLAUDE.md`, `AGENTS.md`, `briefings/*`, `.claude/agents/*`, `.claude/commands/*`, legacy command assumptions across dispatch and docs.
- **Responsibility / dependencies:** Assigns ORCH, GB, CX, S5 roles; Claude Code slash commands and prompt hierarchy; historical identity override/native agent behavior.
- **Claude assumptions:** Fundamental: Claude Code instructions, slash commands, native agents, CLAUDE.md and `claude -p`.
- **DEVDEPARTMENT assumptions:** Fundamental: DEVDEPARTMENT ownership and ORCH as canonical HEAD.
- **Decision / rationale:** Do not port or mechanically rename these assets. Create CODEXDEVTEAM-native HEAD instructions, runtime adapters and command surface. Claude may later be an optional builder runtime with its own context adapter. GPT/Codex initial HEAD does not hard-code model IDs in prompts or policy.
- **Abstraction / order / risks/tests:** Role/capability policy and runtime-neutral briefing/context API; Codex adapter is a Phase 2 deliverable. Risk: inheriting prompt injection patterns, dual authority or stale inherited provider instructions. Verify installed prompts do not confer HEAD authority absent the lease.

## Forward-design findings to carry into Phase 1

1. **Verification Gates (G):** G-D.0 path preflight is a small existing mechanism worth adapting early. The larger G-C/G-A/G-B design adds missing essentials: exact-SHA evidence cache, isolated test resources, no secret env leakage, explicit skipped status, baseline failure ownership, reachability/mutation checks, and gate-before-review. These are new CODEXDEVTEAM requirements, not Wave E behavior.
2. **Tiered Routing and Memory (H):** separate defined/active/available/eligible/assigned states; route by capability floor and verified capacity; keep memory evidence-backed with promotion/demotion/retirement and privacy; prove optional tiers pay rent before keeping them. This belongs after reliable receipts and review outcomes.
3. **Claude-native leverage (F):** bounded sessions, typed outputs, model/effort centralization, resume tokens and per-unit strict mode are valuable patterns. Do not port Claude-specific argv, JSON schema flags, permission modes, `/advisor`, auto mode or Claude usage parsers into kernel policy. Map each to a runtime adapter and capability declaration; leave concrete IDs in config. Ship safety-posture changes disabled until live-verified.

## Smallest coherent first kernel slice

Build an offline, independently testable **coordination kernel** before any supervisor or autonomous loop:

1. Versioned task schema/parser and deterministic validator, initially with a Markdown PLAN adapter to preserve state interoperability.
2. Provider-neutral worker identity/config schema that separates role, capability floor, runtime/provider and concrete model.
3. `Owned_Paths` policy with strict unknown-identity rejection and protected-grant validation.
4. Git worktree create/inspect/remove primitives with collision and ownership checks; no model launch.
5. A durable state repository and typed CONTROL event application with atomic single-writer semantics, backed by the exclusive HEAD lease contract (lease acquisition itself must be implemented before permitting coordination writes).
6. Pure secret scanning utility and cross-platform CI for Windows/Ubuntu.

Test this slice with fixtures and fake runtimes only. Its pass condition is: two independent actors cannot both acquire HEAD or claim conflicting territory; valid state can round-trip; malformed or stale writes fail closed; worktrees are repository-bound; no provider executable is needed to run the suite. Defer actual dispatch, model review and supervisor until this passes.

## Recommended dependency order (analysis recommendation)

| Order | Increment | Exit evidence |
|---:|---|---|
| 0 | Windows + Ubuntu CI skeleton; kernel fixtures; package boundaries | Same offline suite passes on both OSes; no copied legacy assumptions in core imports. |
| 1 | Task protocol/state vocabulary parser + validator | Versioned parse/round-trip; exhaustive transition and invalid-input tests. |
| 2 | Worker registry schema + territory/Protected_Grants policy | Role/runtime/model separation; unknown actor and overlap fail closed. |
| 3 | Git worktree manager + platform path/process primitives | Collision, wrong-repo and interrupted lifecycle tests. |
| 4 | Exclusive HEAD lease + transactional CONTROL/state store | Contention/fencing/replay tests; no write without lease. |
| 5 | Secret scanner + runtime enforcement capability contracts | Pure scanner regression suite; verified runtime capabilities recorded. |
| 6 | Runtime adapter and bounded dispatch | Fake-runtime contract tests; only verified builders eligible for strict mode. |
| 7 | SHA-bound mechanical gate, then typed review and maker/checker enforcement | Red-gate fixtures prove no judgment invocation; reviewer unit and model mechanically differ. |
| 8 | Receipts, escalations, park/resume and recovery | Restart/replay and duplicate-prevention fixtures. |
| 9 | Minimal supervisor decision reducer and manual pilot | Pure decision tests first; autonomy remains disabled absent explicit activation. |
| 10 | CODEXDEVTEAM ownership/sync and onboarding modes | Dry-run/conflict tests; DEVDEPARTMENT untouched; install does not acquire lease. |
| 11+ | Capacity routing/usage, board/remote control, learning/ATLAS, scheduled autonomy | Each enabled only after reliability, privacy and value evidence. |

## Top architectural adaptations

1. Replace ORCH/unit literals with role + capability + runtime + model identities and an exclusive, fenced HEAD lease.
2. Make task state and CONTROL a versioned, provider-neutral single-writer API; keep Markdown as a compatibility adapter, not the authority contract.
3. Put write enforcement behind a runtime-neutral territory policy with honest per-runtime capability checks; retain deterministic enforcement, drop GateGuard prompts.
4. Rebuild dispatch/review around adapters and a scripted SHA-bound gate; mechanically enforce maker != checker before merge.
5. Design ownership/sync and onboarding for sibling coexistence: DEVDEPARTMENT-owned files remain untouched, and installation never activates CODEXDEVTEAM.

## Top risks

1. **Split-brain control:** two supervisors or a stale owner writes after lease expiry. Mitigate with atomic lease + fencing generation + fail-closed state writes.
2. **False territory safety:** path/glob checks differ on Windows, symlinks or alternate runtime write APIs. Test adapters and verify final Git diff independently.
3. **Green-but-unverified gate:** missing checks or a stale SHA result treated as pass. Make `skipped` explicit and SHA/config/environment bind every artifact.
4. **Incumbent overwrite/state loss:** pack sync or onboarding mishandles mixed ownership. Use ownership manifest, dry-run, baseline/conflict detection, and DEVDEPARTMENT sidecar fixtures.
5. **False maker/checker independence or runtime parity:** aliases map to the same model/account, or an optional builder lacks a live-verified enforcement capability. Record identity facts and capabilities; refuse merge/strict activation when unknown.

## Scope boundary

This manifest is analysis, not implementation approval. No commit/push is part of the proposed sequence. Phase 1 code work should begin only after review of this manifest and the CODEXDEVTEAM abstractions it recommends.
