# CODEXDEVTEAM

A GPT-led development-team operating system derived from the proven DEVDEPARTMENT architecture, with a provider-neutral engine and an exclusive, swappable development HEAD.

## Status

**v0.1.0 released 2026-10-04.** This is the Windows-first fresh-project core.
**v0.2.0 is the current release candidate** for standalone unattended
development of a new project from a brief. The candidate's brief-to-plan,
supervised workflow, and hosted CI have passed; release publication remains
open. See [v0.2 release notes](docs/RELEASE_NOTES_v0.2.md). The supervisor
remains parked until an operator explicitly activates it. DEVDEPARTMENT remains
the behavioral reference; taking control of an existing DEVDEPARTMENT project
and returning it are outside the v0.2 release scope.

The initial source package lives under `src/codexdevteam_kernel/`. It contains
provider-neutral coordination policy, a bounded Codex runtime adapter, a
host-owned commit boundary, and a parked-by-default, lease-fenced dispatch
cycle. Supervised maker commits are currently supported on Windows; Ubuntu is
CI compatibility coverage only, and maker commits fail closed without a
verified Windows Job Object proof. The v0.2.0 candidate passed hosted CI on
Windows and Ubuntu compatibility jobs for Python 3.11 and 3.12 at
[run 37691384320](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37691384320).
The matrix builds and installs the package, smoke-checks its installed CLI
entry points, imports the package, and runs the contract suite.

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

Concrete model IDs are configuration, not architecture. The orchestrator may
request roles and capability floors; dispatch selects only among configured,
strict-verified workers that satisfy those requirements and current capacity.

## Install into a project

Install the package, inspect the target, then create an inactive installation:

```powershell
python -m pip install .
codexdevteam inspect --project C:\Projects\MY-PROJECT
codexdevteam init --project C:\Projects\MY-PROJECT
codexdevteam upgrade --project C:\Projects\MY-PROJECT
codexdevteam usage --state-db C:\Projects\MY-PROJECT\.codexdevteam\project\state.sqlite
```

`init` supports fresh projects and non-destructive DEVDEPARTMENT sidecars. It
does not take HEAD authority or activate supervision. Existing installations
and ambiguous dual installations require an explicit upgrade or handover path.

## Core invariants

1. Exactly one active HEAD per project.
2. Project state is portable between compatible development HEADs.
3. Role, capability, runtime/provider and concrete model are separate concepts.
4. Maker != checker unless a human explicitly overrides.
5. Mechanical facts are established mechanically before model judgment.
6. Strict CONTROL-style single-writer orchestration is preferred for verified builders.
7. Initial HEAD activation is explicit; onboarding never silently takes control. Cross-system handover remains a separately gated future capability.
8. Safety/autonomy changes use an ask-and-verify posture.
9. Significant model sessions are bounded and ledgered where runtime data permits.
10. Existing DEVDEPARTMENT projects are adapted, never overwritten.

## Bootstrap documents

- `docs/CONSTITUTION.md`
- `docs/ARCHITECTURE.md`
- `docs/INTEROPERABILITY.md`
- `docs/ONBOARDING.md`
- `docs/TEST_RUNNER.md`
- `docs/CODEX_HOOKS.md`
- `docs/CONTROL.md`
- `docs/EVIDENCE_MEMORY.md`
- `docs/USAGE_REPORTING.md`
- `docs/PUBLISHING.md`
- `docs/PLAN_ARCHIVE.md`
- `docs/FAST_TIER.md`
- `docs/COMPATIBILITY_MATRIX.md`
- `docs/RUNTIME_SMOKE.md`
- `ROADMAP.md`
