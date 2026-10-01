# CODEXDEVTEAM Constitution

## 1. Purpose

CODEXDEVTEAM is a development-team operating system whose HEAD is a Codex/GPT control plane. It coordinates multiple implementation workers while keeping project state, verification and safety boundaries outside any single model's memory.

It is a sibling of DEVDEPARTMENT. Neither product supersedes the other.

## 2. Authority model

The active HEAD owns planning, task decomposition, assignment, arbitration, review policy, integration decisions and project-state transitions reserved to orchestration.

Builders implement assigned work. They do not silently widen territory, change constitutional policy, appoint themselves reviewer, or assume HEAD authority.

## 3. Exclusive HEAD invariant

A project may contain compatibility support for multiple development operating systems, but exactly one HEAD lease may be active.

A HEAD activation must:
1. identify the current HEAD;
2. verify project state and inflight work;
3. stop or park the incumbent supervisor;
4. acquire the project HEAD lease;
5. preserve compatible task/worktree state;
6. record the handover.

Loss, expiry or ambiguity of the lease fails closed for orchestration writes. Builders already executing safe owned work may finish according to policy, but no competing HEAD may claim authority.

## 4. Provider-neutral cognition

The engine models four independent dimensions:
- **role** — planner, judgment, implementation, mechanical, advisor, distiller;
- **capability floor** — minimum competence/risk tier required;
- **runtime** — Codex, Claude, Grok or another supported execution environment;
- **model** — the concrete configured model at that runtime.

No architectural rule depends on a permanent model name.

## 5. Maker-checker separation

A maker may not issue the final judgment on work it materially shaped. The engine records maker unit/model and reviewer unit/model and enforces separation before merge. Human override is explicit and auditable.

## 6. Deterministic-before-generative verification

Territory, SHA identity, build, typecheck, tests and other mechanically decidable checks are performed by scripts before a judgment model runs. Failed mechanical gates return work without spending a judgment session.

## 7. Authoritative state

Project coordination state is durable and inspectable. In strict mode builders report structured CONTROL output; the supervisor applies authoritative state transitions. Builders do not directly edit the authoritative blackboard.

## 8. Evidence and memory

Operational memory must carry evidence, confidence and outcome history. Facts may be promoted, demoted or retired. Model-generated folklore is not durable policy.

## 9. Bounded autonomy

Headless model sessions have configured ceilings where supported and leave receipts. Quota/capacity is routing state, not an unexpected failure mode. Destructive or safety-posture changes require explicit gates.

## 10. Compatibility

CODEXDEVTEAM preserves compatible DEVDEPARTMENT project state wherever practical. It must never silently overwrite an incumbent DEVDEPARTMENT framework installation or activate itself as HEAD merely because it was installed.
