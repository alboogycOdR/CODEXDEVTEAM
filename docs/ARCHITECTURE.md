# Architecture

## Layer model

CODEXDEVTEAM is designed as five logical layers.

### 1. HEAD plane
High-reasoning control plane responsible for architecture, decomposition, arbitration and judgment. Initial policy targets a GPT-6-class Codex model, but model identity is configurable.

### 2. Policy and routing
Maps work to a role and capability floor, then filters workers by runtime health, capacity, quota, territory, machine affinity and measured evidence.

### 3. Workforce
Default Codex implementation worker plus optional Claude Sonnet, Grok and additional runtime identities. Registry entries describe runtime/model/auth/worktree/branch/briefing/control/capacity properties.

### 4. Deterministic verification
Mechanical pre-review gates establish territory cleanliness, exact SHA, build/typecheck/full-test state and configured reachability/mutation/baseline checks. Results are SHA-bound artifacts.

### 5. Durable project state
PLAN/task protocol, REVIEW/verdict history, dossiers, worktrees, ledgers, CONTROL queue, evidence memory, supervisor state and HEAD lease.

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

## Configuration principle

Configuration expresses policy. Engine code implements invariants. Concrete model IDs, quotas and runtime availability must not be spread through scripts.
