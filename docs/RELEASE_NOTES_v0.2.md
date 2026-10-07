# CODEXDEVTEAM v0.2 — Release Notes

**Release status:** not ready for release. These notes describe the current
verified development state; they do not declare v0.2 complete or authorize a
release. An uninterrupted three-task strict Windows acceptance run completed.
Capacity was seeded manually before activation, and configured model spend is
still unknown because no pricing rates were configured. An automatic capacity
source and hosted CI for the latest uncommitted change remain outstanding.

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
- Allow project owners to configure narrow ignored-artifact patterns in the
  protected verification policy. Matching ignored files are excluded from the
  commit; other ignored paths still refuse the commit.
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

- A live, uninterrupted three-task brief-to-integration run completed with
  strict Codex maker/checker identities, exact-SHA gates, independent approvals,
  and integration receipts. The host parked at its configured cycle bound and
  released the HEAD lease after all tasks were done.
- The pilot recorded three first-pass approvals, three passing gates, no gate
  rejections, no rework, and no escalations. All six calls retained input and
  output token counts. Configured spend remains unknown, and capacity was
  manually seeded once before activation; a connected automatic capacity source
  is still a release blocker.
  Optional fast-tier routing remains disabled until representative evidence
  supports enabling it.
- Restarted tasks are escalated and left for deliberate operator review; the
  host does not automatically replay uncertain work.
- The host supports fresh-project activation only. DEVDEPARTMENT sidecar
  activation, handover, reverse transfer, push, and deployment are not included
  in v0.2.

## Verification recorded during development

- Windows PowerShell full suite: **295 tests passed, 8 skipped** on 2026-10-07,
  including the configured ignored-artifact allowlist regression.
- Latest hosted CI run [37674230702](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37674230702)
  passed for commit `798c4ef7a07ad42d3f145030dfcb50927992d131` on Windows and
  Ubuntu compatibility jobs with Python 3.11 and 3.12.
