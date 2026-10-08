# CODEXDEVTEAM v0.2 — Release Notes

**Released 2026-10-08.** v0.2.0 supports standalone, Windows-first supervised
development from a project brief through validated planning, bounded
maker/checker work, mechanical gates, and integration. The host stays parked
until an operator explicitly activates it. Ubuntu is compatibility CI only;
DEVDEPARTMENT handover is outside this release.

The three-task strict Windows pilot used a manually seeded capacity snapshot.
Two later runs used live Codex app-server capacity and completed maker, gate,
checker, and integration for one task and then three tasks. Subscription-dollar
spend remains unknown because the local account exposes no per-model charges
and no attribution rates were configured.

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
- Query Codex account rate limits automatically through the local app-server
  before activation and each cycle. The adapter accepts only one shared
  `codex` quota bucket and fails closed on model-specific or unknown buckets.
  Manual JSON snapshots remain an explicit compatibility option.

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
- Two separate bounded Windows runs used the live app-server capacity source
  through dispatch and every closeout stage: one task and then three tasks.
  All four tasks passed their exact-SHA gates, received independent first-pass
  approval, and integrated. The host parked with no active invocations or
  leases. The three-task run provides a second multi-task data point using live
  capacity.
- The original manual-snapshot pilot recorded three first-pass approvals and
  complete token counts. The two live-capacity runs recorded four more
  first-pass approvals, four passing gates, four integrations, no rework, and
  no open escalations. Each run parked and released its lease. Subscription
  dollar spend remains unknown; no API prices were substituted. The task sample
  is still small and uses simple utility work, so fast-tier routing remains
  disabled pending an agreed accuracy threshold and broader evidence.
- Restarted tasks are escalated and left for deliberate operator review; the
  host does not automatically replay uncertain work.
- The host supports fresh-project activation only. DEVDEPARTMENT sidecar
  activation, handover, reverse transfer, push, and deployment are not included
  in v0.2.

## Verification recorded during development

- Windows PowerShell full suite: **311 tests passed, 8 skipped** on both
  Python 3.11 and 3.12 on 2026-10-07, including live-capacity adapter and
  closeout failure handling regressions.
- Hosted CI run
  [37691384320](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37691384320)
  passed on candidate commit `37765a273064fa506753cee17e3aec743bf72466` for
  Windows and Linux compatibility jobs with Python 3.11 and 3.12. The Windows
  jobs validate the supported supervised runtime; Linux remains compatibility
  coverage only.
- Local Windows packaging check: the updated source built a wheel with Python
  3.11, installed into a disposable target, and all five packaged entry points
  passed their help/fail-closed smoke checks. The v0.2.0 wheel metadata and
  `codexdevteam_kernel.__version__` both report `0.2.0`; all five entry points
  passed against the installed wheel after the version bump. Rebuilding from
  merged commit `45fb2fc8b42f8934f47e0873fc5c6ba8139a6438` produced wheel
  SHA-256 `0b34b3771ec862c6601f332b9a7b78eca82e2cf74ae0f82638f59504c3cb8044`;
  the installed package and all five entry-point checks passed again.
- Disposable Windows `host-preflight` on 2026-10-07 used the default live
  Codex app-server capacity source, reported both configured workers ready,
  and left the project parked without dispatch. This verifies the local
  account-capacity query path; it is not another supervised acceptance run.
- The initial live task attempt on 2026-10-07 failed before acceptance and
  recovery verified the Windows Job Object process tree quiescent. A later
  corrected disposable fixture completed the full path; both outcomes and the
  fixture requirements are recorded in `docs/RUNTIME_SMOKE.md`.
- Hosted candidate run [37691384320](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37691384320)
  passed on all four Windows/Ubuntu compatibility × Python 3.11/3.12 jobs.
- Post-merge run [37728131521](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37728131521)
  passed on all four jobs for release commit
  `45fb2fc8b42f8934f47e0873fc5c6ba8139a6438`.
