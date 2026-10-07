# CODEXDEVTEAM v0.2 — Release Notes (Draft)

**Release status:** not ready for release. The bounded unattended Windows
pilot and live strict-worker evidence are still outstanding.

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

- A bounded, unattended fresh-project pilot from brief through reviewed
  integration or durable escalation has not yet been completed. Unit and
  fixture integration tests do not replace this live evidence.
- Live strict-verification receipts must be recorded for every builder enabled
  in the pilot, each bound to its configured runtime and model.
- Operational first-pass, review, gate rejection, escalation, recovery,
  elapsed-time, and configured spend metrics still need verified pilot data.
  Optional fast-tier routing remains disabled until representative evidence
  supports enabling it.
- Restarted tasks are escalated and left for deliberate operator review; the
  host does not automatically replay uncertain work.
- The host supports fresh-project activation only. DEVDEPARTMENT sidecar
  activation, handover, reverse transfer, push, and deployment are not included
  in v0.2.

## Verification recorded during development

- Windows PowerShell full suite: **291 tests passed, 8 skipped** on 2026-10-07.
- Hosted `kernel-ci` run [37611655141](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37611655141)
  passed for commit `ae7d23f`. The newer uncommitted recovery regressions have
  local Windows coverage; they have not yet been pushed for hosted CI.

These notes describe the current development state. They do not declare v0.2
complete or authorize release.
