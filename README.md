# CODEXDEVTEAM

A GPT-led development-team operating system derived from the proven DEVDEPARTMENT architecture, with a provider-neutral engine and an exclusive, swappable development HEAD.

## Status

**Bootstrap / architecture phase.** Not production-ready yet.

## Lineage

CODEXDEVTEAM is a sibling of DEVDEPARTMENT, not a replacement for it.

Initial reference baseline:

- DEVDEPARTMENT Wave E complete.
- Canonical reference commit: `2d4202c9c70300f3f5cdf97a0b866c1a15b4c760`.
- Baseline date: 2026-10-01.
- Reference state: parked after Wave E / stable feature-complete baseline.

CODEXDEVTEAM selectively ports proven mechanisms. It does not blindly fork DEVDEPARTMENT and rename Claude concepts.

## Product intent

Default topology:

- **HEAD / control plane:** configured high-reasoning GPT model through Codex.
- **Default builder:** configured implementation-tier GPT/Codex model.
- **Optional builders:** Claude Sonnet, Grok, additional Codex identities, and future runtimes.
- **Mechanical verification:** deterministic gates establish facts before expensive model judgment.

Concrete model IDs are configuration, not architecture.

## Core invariants

1. Exactly one active HEAD per project.
2. Project state is portable between compatible development HEADs.
3. Role, capability, runtime/provider and concrete model are separate concepts.
4. Maker != checker unless a human explicitly overrides.
5. Mechanical facts are established mechanically before model judgment.
6. Strict CONTROL-style single-writer orchestration is preferred for verified builders.
7. Switching HEADs is an explicit validated handover; onboarding never silently takes control.
8. Safety/autonomy changes use an ask-and-verify posture.
9. Significant model sessions are bounded and ledgered where runtime data permits.
10. Existing DEVDEPARTMENT projects are adapted, never overwritten.

## Bootstrap documents

- `docs/CONSTITUTION.md`
- `docs/ARCHITECTURE.md`
- `docs/INTEROPERABILITY.md`
- `docs/ONBOARDING.md`
- `ROADMAP.md`
