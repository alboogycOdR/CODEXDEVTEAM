# CODEXDEVTEAM v0.2 — Release Notes (Draft)

**Release status:** not ready for release. A three-task strict Windows
qualification completed, but capacity was refreshed manually and an initial
host defect required a safe park and restart. An uninterrupted unattended
acceptance run and an automatic capacity source are still outstanding.

## What v0.2 adds

- Start a fresh Git project from a UTF-8 brief. The configured Codex planner
  produces a task plan; the host validates task states, dependencies, acceptance
  criteria, ownership, protected grants, and control-path rules before creating
  `PLAN.md` and authoritative task state.
- Keep planning, installation, and HEAD activation as separate operations.
  The combined brief path requires separate confirmations for plan creation and
  activation, and acquires one exclusive project HEAD lease.
- Route planned task classes through configured logical roles and capability
  floors. Dispatch requires active strict-verified workers, fresh capacity, and
  distinct maker and checker identities.
- Run a bounded Windows host lifecycle with lease-safe dispatch, host-owned
  commits, exact-SHA mechanical gates, independent checker review, and bounded
  checker-requested rework. Incomplete closeout remains open for recovery and
  prevents the next dispatch.
- Recover interrupted runs through signed cancellation receipts and Windows
  Job Objects. If process-tree quiescence cannot be proved, invocation liveness
  stays held and dispatch remains parked. Interrupted task branches are
  preserved for operator review.
- Integrate independently approved task branches in a temporary worktree,
  verify the merged commit, and update the project branch only if its ref has
  not changed. Conflicts and failed or interrupted integration create durable
  recovery records.
- Refuse integration while tracked project changes outside host-projected
  `PLAN.md` remain uncommitted, preserving configured framework files from the
  host's branch reset.
- Report invocation usage and pilot metrics from recorded receipts. Missing
  usage and pricing remain unknown rather than being treated as zero.

## Supported environment

- Supervised maker execution and host recovery run on native Windows with
  PowerShell. Linux CI is compatibility coverage only; Linux and WSL are not
  supported for supervised maker execution.
- The host currently binds the Codex CLI runtime. Runtime and concrete model
  remain configured identities; capabilities are not inferred across models.
- Windows release CI has passed on Windows with supported Python versions and
  also runs Linux compatibility jobs.
- Codex structured-edit hooks may provide earlier territory feedback, but they
  are optional and are not the territory security boundary. The host commit
  check refuses out-of-scope changes after maker processes stop.

## Current limits and release gates

- A live three-task brief-to-integration qualification completed with strict
  Codex maker/checker identities, exact-SHA gates, independent approvals, and
  integration receipts. Its first host process parked after a PLAN integrity
  defect; the workflow resumed after a local fix and completed. This is not an
  uninterrupted unattended acceptance run.
- The qualification recorded three approvals and integrations, no rework, and
  no recorded escalation. It does not retain complete token usage or configured
  spend for all invocations, and capacity snapshots were manually refreshed.
  A connected automatic capacity source and complete pilot metrics remain open.
  Optional fast-tier routing remains disabled until representative evidence
  supports enabling it.
- Restarted tasks are escalated and left for deliberate operator review; the
  host does not automatically replay uncertain work.
- The host supports fresh-project activation only. DEVDEPARTMENT sidecar
  activation, handover, reverse transfer, push, and deployment are not included
  in v0.2.

## Verification recorded during development

- Windows PowerShell full suite: **294 tests passed, 8 skipped** on 2026-10-07,
  including PLAN-integrity and tracked-configuration integration regressions.
- Hosted `kernel-ci` run [37664978715](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37664978715)
  passed for pushed commit `9d8d693`, including the PLAN-integrity and
  tracked-configuration integration regressions.

These notes describe the current development state. They do not declare v0.2
complete or authorize release.
