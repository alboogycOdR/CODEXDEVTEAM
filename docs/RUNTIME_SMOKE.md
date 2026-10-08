# Runtime and CI evidence

## Hosted CI matrix — 2026-10-02

- Run: [37024696603](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37024696603)
  at commit `ba39acc4d7590b411e00e8f0941de2ef71fda8db`.
- Result: all four jobs passed: Ubuntu and Windows on Python 3.11 and 3.12.
  Each job built and installed the package, checked all command entry points,
  exercised the fail-closed Codex hook, imported the package, and passed the
  full 243-test contract suite.
- The first run exposed a Windows test assertion comparing an 8.3 temp path to
  its resolved path. The assertion now compares resolved paths; the full
  matrix passed on the rerun.
- GitHub emitted informational runner notices for Node.js 20 compatibility in
  the pinned actions and the scheduled `ubuntu-latest` migration to Ubuntu 26.

This is hosted bootstrap CI evidence. It does not establish real-project
interoperability, process-fenced handover, or pilot readiness.

## Current Windows contract suite — 2026-10-02

- Command: `$env:PYTHONPATH='src'; python -m unittest discover -s tests` from
  `C:\Projects\CODEXDEVTEAM`.
- Results: 243 tests ran; the suite passed with 6 skips (two symlink-permission
  limitations, one POSIX-only process-group test, and three Linux-only reaper
  tests).
- Scope: current Windows contract suite, including real whole-tree Job Object
  containment and schema-v7 maker process identity and signed cancellation
  recovery. This is not Ubuntu/WSL or hosted CI
  evidence.

## Windows Job Object containment and schema v7 — 2026-10-02

- Tests: full contract suite, including a real maker child/grandchild Job Object
  containment test, successor Job Object recovery, lease-fenced state recovery,
  and schema migration preservation tests.
- Windows: 243 tests passed with 6 expected platform/permission skips.
- Linux: the full 243-test suite passed in a disposable `python:3.12-slim`
  container with Git installed; 4 Windows/permission-specific tests were
  skipped. The workspace was mounted read-only. This is supplementary Debian
  Linux evidence, not Ubuntu WSL or hosted CI.
- Contract: managed Windows maker processes start suspended, are assigned to a
  named kill-on-close Job Object, and resume only after their Job identity is
  persisted under the HEAD lease. A successor may reacquire the named Job and
  release liveness only after whole-tree termination is verified. Linux PIDFD
  reaping continues to prove only process-group termination and does not
  release liveness.

## Built package install and command entry points — 2026-10-02

- Built a wheel from the current checkout with `python -m pip wheel
  --no-deps --no-build-isolation`; installed it into a disposable Windows
  virtual environment and a disposable Debian Python 3.12 container. Both
  imports resolved to the installed package locations, not the source tree.
- Ran the installed `codexdevteam`, `codexdevteam-test`,
  `codexdevteam-control`, and `codexdevteam-fast-eval` entry points. Their CLI
  parsers started successfully. The installed Codex hook entry point also ran
  and returned its expected fail-closed deny response when invoked without
  hook context.
- Windows: ran `python -m unittest discover -s tests` from the installed
  virtual environment with `PYTHONPATH` unset; all 243 tests passed with 6
  expected Windows permission/platform skips.
- Debian Linux: ran the same full suite from the installed wheel with the
  workspace mounted read-only and Git installed in the container; all 243
  tests passed with 4 expected platform-specific skips.
- Ubuntu 24.04: repeated the installed-wheel suite in a disposable container
  with the workspace mounted read-only and Git installed; all 243 tests passed
  with 4 expected platform-specific skips.
- The virtual environment and wheel were outside the repository. This checks
  cross-platform package build/install wiring and command registration; it does
  not replace the pending live trusted-hook smoke.

## Earlier Debian full contract suite — 2026-10-02

- Host: Windows with Docker Desktop; no pre-existing container was stopped.
- Image: `python:3.12-slim` (Debian), with Git installed in the disposable
  container for Git worktree tests.
- Workspace: mounted read-only; suite ran with bytecode writes disabled.
- Command: `python3 -m unittest discover -s tests` after installing Git.
- Results: 224 tests ran successfully, with one Windows-only process-tree test
  skipped. This includes the current schema-v5 and signed cancellation-recovery
  changes, fast-tier corpus-evaluator and reset-time scoring, continuous notifier delivery, and POSIX descendant-process coverage.
- Scope: supplementary Linux evidence. This does not count as Ubuntu/WSL,
  hosted CI, or live Codex hook/runtime capability verification.

## Current Ubuntu contract suite — 2026-10-02

- Host: Windows with Docker Desktop; disposable Ubuntu container, workspace
  mounted read-only.
- Image: `ubuntu:24.04`; installed Python 3 and Git in the container for the
  Git worktree tests.
- Command: `python3 -m unittest discover -s tests` after installing the
  required packages.
- Results: the earlier 231-test suite passed with one platform-specific test
  skipped.
- Scope: Ubuntu 24.04 userspace verified in a local container. This does not
  verify Ubuntu WSL startup, hosted CI, or live Codex hook/runtime capabilities.

## Continuous runner lease keepalive — 2026-10-02

- Test: `tests.test_kernel_contracts.SupervisorTests.test_continuous_runner_renews_head_lease_during_idle_interval`.
- Results: passed on Windows; the complete 231-test contract suite also passed
  in Windows and Ubuntu 24.04 containers.
- Coverage: a 150 ms HEAD lease remains renewable after two continuous cycles
  separated by a 300 ms idle interval. The runner's keepalive spans callbacks
  and idle waits and reports lease fencing before starting another cycle.

## Maker process identity persistence — 2026-10-02

- Tests: `RuntimeAdapterTests.test_maker_runtime_reports_os_identity_immediately_after_spawn`, `SupervisorTests.test_running_maker_process_identity_is_lease_fenced_and_idempotent`, and `StateSchemaMigrationTests.test_schema_v5_adds_process_identity_columns_without_losing_live_invocation`.
- Results: focused Windows checks passed; the complete 231-test suite passed
  in Windows and Ubuntu 24.04 containers.
- Coverage: maker startup records PID, platform creation fingerprint, and
  process group under the current HEAD lease. Repeated identical reports are
  idempotent, identity replacement is rejected, and failed recording triggers
  tree termination; unverified termination keeps maker liveness running.
- Scope: process identity is now durable, but stale-process termination and
  orphan redispatch remain disabled pending a lease-fenced host reaper.

## Stale invocation identity diagnosis — 2026-10-02

- Tests: process identity observation for a matching process, PID reuse,
  absence, and inaccessible identity; stale-invocation supervisor escalation.
- Results: five focused tests passed on Windows.
- Results: the three platform-specific process-observation tests also passed
  in the cached Python 3.12 Linux container; the supervisor stale-cycle test
  is covered by the Windows suite. A full Ubuntu refresh could not be repeated
  because the disposable Ubuntu container stalled downloading Git/Python via
  `apt`.
- Coverage: stale escalation reports a read-only host observation when durable
  identity exists, or `identity_not_recorded` for legacy/incomplete rows. The
  observation is persisted under the HEAD lease with a SHA-256 identity
  fingerprint and does not release liveness or authorize termination or
  redispatch. A lease-fenced explicit Linux process-group cleanup backend is
  covered separately below; whole-tree verification remains open.

## Explicit Linux process-group cleanup — 2026-10-02

- Tests: PIDFD-backed group termination and PID-reuse refusal; lease-fenced
  `StateStore.reap_stale_invocation()` with a real isolated test process group;
  Windows mock coverage that only whole-tree evidence releases liveness.
- Results: three Linux tests passed in the cached Python 3.12 container; the
  Windows state-transition contract test passed.
- Coverage: the backend pins the group leader with pidfd, stops it before
  signaling, and verifies no live member remains in the recorded group. The
  HEAD SQLite writer lock is held for the bounded termination. Group exit does
  not prove detached descendants are absent, so Linux group evidence keeps
  invocation liveness running. Windows orphan cleanup and whole-tree
  containment remain unsupported; reaping is not automatic.
## Earlier Linux kernel contract suite — 2026-10-02

- Host: Windows with the existing Docker Desktop engine; no running container
  was stopped.
- Image: cached `python:3.12-slim` (Debian), with Git installed in each
  disposable container for the Git worktree tests.
- Workspace: mounted read-only; suite ran with bytecode writes disabled.
- Results at that code state: all 210 tests passed with no skips, first as root and then as UID
  65534 (`nobody`).
- Scope: Python/Linux kernel behavior and POSIX process-group coverage. This is
  not an Ubuntu distribution run, hosted CI, or a live Codex hook/runtime
  capability receipt.

## Current runtime cancellation and schema slice — 2026-10-02

- Image: cached `python:3.12-slim` (Debian), read-only workspace mount, bytecode
  writes disabled.
- Command: `python3 -m unittest tests.test_kernel_contracts.RuntimeAdapterTests tests.test_kernel_contracts.SupervisorTests.test_unverified_process_tree_cancellation_is_persisted_and_keeps_maker_live tests.test_kernel_contracts.SupervisorTests.test_new_head_recovers_signed_cancellation_after_lease_loss_before_health_decision tests.test_kernel_contracts.SupervisorTests.test_modified_cancellation_sidecar_cannot_release_maker_liveness tests.test_kernel_contracts.StateSchemaMigrationTests -v`.
- Results: 24 tests ran; the suite passed with one Windows-only process-tree
  test skipped. Coverage includes real POSIX descendant termination, signed
  lease-loss recovery, tamper rejection, held liveness on failed verification,
  handover context protections, and schema v1/v2/v3/v4 migrations.


## Fast-tier corpus evaluator packaging smoke — 2026-10-02

- Built a wheel from a temporary copy of the source with local build tooling,
  installed it into a disposable isolated Windows virtual environment, and ran
  the generated `codexdevteam-fast-eval --help` entry point successfully.
- Ran the evaluator against the six-case synthetic fixture with matching
  predictions, including the quota reset timestamp; the current installed CLI
  reported 100% for those synthetic labels. This validates the CLI/package path
  only and is not model or field-corpus accuracy evidence.
- The existing global Python package was restored from the current checkout
  after an initial `pip --prefix` experiment unintentionally uninstalled it;
  the successful smoke used an isolated virtual environment and did not alter
  the repository source tree.
## Continuous notifier retry integration — 2026-10-02

- Test: `tests.test_kernel_contracts.SupervisorTests.test_continuous_runner_delivers_cycle_escalations_before_maker_launches`.
- Results: passed on the full Windows suite and in a focused Debian-container
  run. It verifies that the continuous loop attempts an escalation before
  launching makers, retains a failed delivery for retry, and delivers it on a
  later cycle using the same notification ID.
- The retry interval was shortened only in the fixture. Production delivery
  remains opt-in and uses the configured claim and retry intervals.
## Ubuntu WSL retry — 2026-10-02

- Command: `wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/c/Projects/CODEXDEVTEAM && PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests'`.
- Result: WSL failed before launching the command with
  `Wsl/Service/CreateInstance/HCS_E_CONNECTION_TIMEOUT` after reporting that
  the virtual machine or container did not respond.
- This is an environment startup failure, not a test failure. The current
  Debian-container full-suite pass above remains supplementary Linux evidence;
  Ubuntu WSL remains unverified. Hosted CI subsequently passed as recorded
  above.

## Ubuntu WSL retry after Ubuntu container run — 2026-10-02

- The same full-suite command was retried after the disposable Ubuntu 24.04
  container had exited. WSL again failed before starting Python with
  `Wsl/Service/CreateInstance/HCS_E_CONNECTION_TIMEOUT`.
- No test ran during this attempt. The successful Ubuntu 24.04 container run
  above verifies Ubuntu userspace; WSL startup remains unverified. Hosted CI
  subsequently passed as recorded above.
## Codex CLI adapter — 2026-10-01

- Host: Windows, repository checkout `C:\Projects\CODEXDEVTEAM`.
- CLI: Codex CLI 0.159.3.
- Runtime/model: `codex` / `gpt-6-luna` from local configuration for this probe only.
- Adapter: `CodexExecAdapter`, purpose `triage`, read-only sandbox, ephemeral session.
- Prompt: request the fixed response `CODEXDEVTEAM_ADAPTER_SMOKE_OK`; no tools requested.
- Result: succeeded, exit code 0; the expected response was present in the adapter output.
- Metering: Codex JSONL reported 19,800 input tokens; pricing was not configured for
  this smoke, so no USD amount is claimed.

An initial adapter attempt from a non-repository temporary directory was rejected
by Codex because the directory was not trusted. The successful probe used the
trusted CODEXDEVTEAM checkout. This confirms the bounded read-only CLI launch
path in a trusted checkout; it does not verify project write containment,
PreToolUse hook activation, CONTROL emission, strict-mode capabilities, or a
fresh-project trust flow. Those checks remain required before production
activation.

## Disposable fresh-project integration pilot — 2026-10-01

The `FreshProjectPilotTests.test_fresh_install_firewall_control_gate_and_review_vertical_slice`
test created a temporary Git project, ran the actual `codexdevteam init` CLI,
committed the inactive framework defaults, and configured local Git excludes
for SQLite state and the CONTROL outbox. It then exercised an allowed and denied Codex hook event,
submitted and applied two CONTROL reports, registered SHA-bound test evidence,
passed a mechanical gate, registered a distinct checker receipt, and completed
the review transition. The task reached `done`; the installation marker
remained inactive. The temporary project was removed by the test harness.

This pilot used fixture runtime and checker receipts. It validates integration
of the installer, filesystem/HEAD policy, CONTROL queue, test receipts, gate,
and review ledgers; it does not verify a real model writing in a newly trusted
project, live strict capabilities, runtime-produced CONTROL emission, or a
measured end-to-end operational pilot. The installer does not configure these
Git excludes automatically; the onboarding guide records this setup step.

## Normal-trust hook activation attempt — 2026-10-02

- In the explicitly designated disposable project
  `codexdevteam-hook-review-6ltpmq53`, a normal `codex exec` run created
  `src/owned.txt` as requested. A subsequent out-of-scope `outside.txt` edit
  also completed, and the file was readable afterward.
- The disposable hook command was temporarily wrapped to record each incoming
  event and the policy adapter response. No trace was produced. Therefore this
  attempt does **not** establish that Codex invoked the configured hook; the
  successful owned-path edit is not evidence of a policy allow, and the
  out-of-scope edit demonstrates that this run did not enforce the intended
  policy.
- The trace wrapper and test files exist only in the disposable project. No
  source implementation was changed. No trust-bypass flag was used.
- At that point, normal project-hook activation remained unverified and the
  live firewall capability gate stayed open. The earlier disposable smoke
  below was explicitly run with the hook-trust bypass and is not evidence for
  normal hook review/trust.

## Trusted disposable hook review and allow/deny smoke — 2026-10-02

- Opened Codex's normal `/hooks` review for the disposable project's current
  hook definition, inspected the `PreToolUse` command, and trusted that hook
  only. Codex then showed one active hook for `PreToolUse`.
- With no trust-bypass flag, the hook trace recorded an `apply_patch` event for
  `src/owned-after-trust.txt`; the adapter returned allow and the file was
  created with the expected content.
- The trace then recorded an `apply_patch` event for
  `outside-after-trust.txt`; the adapter returned
  `permissionDecision: deny` with reason `outside owned paths`. Codex reported
  `Command blocked by PreToolUse hook`, and the target file was absent.
- This verifies normal Codex hook review/trust and the structured-edit
  owned-path allow/unowned-path deny behavior for this disposable fixture. It
  does not verify live HEAD-side CONTROL drain, task-worktree isolation,
  post-run territory enforcement, strict-capability receipts, or a real pilot.
- The trace and edited files remain in the disposable project. No
  CODEXDEVTEAM source code or DEVDEPARTMENT files were changed for this smoke.

## Single-store disposable supervisor runtime smoke — 2026-10-02

- Run: `731013145a`, under
  `C:\Users\Nuburo\AppData\Local\Temp\codexdevteam-runtime-smoke-r01al2ef`.
  The configured `codex` / `gpt-6-sol` maker invocation succeeded and created
  the requested file only in its supervisor-created task worktree. Primary
  checkout HEAD stayed at `2ee352a64428247dd6fe4f550d8133d1b1285b0b`; the file
  is absent there. The task-only SQLite snapshot was removed after invocation.
- The maker left the file uncommitted. The same single-store HEAD harness
  committed it in the disposable task worktree so the post-run gate could
  inspect the diff. This was a local commit in the temporary fixture, not a
  CODEXDEVTEAM commit.
- The mechanical gate passed at worktree SHA
  `8b1ca740aac355b8ca06a205b93822104e5c171c`; SHA, clean-worktree, territory,
  secret-scan, baseline, and test checks passed. Build and typecheck were
  fixture no-ops. `test_full` checked the exact smoke file content; the
  baseline check allowed the file to be absent on the base commit.
- `finalize_maker_gate` registered the passed test receipt
  `testrun:8b1ca740aac355b8ca06a205b93822104e5c171c:test_full:e53c5413b56e994602e26c0875fbe75277f1410d7e2f4083a2afd37b415f4db5:7e8858ea528eae1577b1a2e521eca64db90190b609e5ab8c41d3a454d6ae274d`
  before HEAD applied the gate-backed `needs_review` CONTROL report. The
  harness's redundant `in_progress` report was rejected because the maker
  invocation had already moved the task to `in_progress`; the task reached
  `needs_review` through the applied report.
- This historical smoke used a strict receipt naming the then-required
  structured-edit firewall capability. That receipt does not satisfy the
  current strict registry floor, which requires `host_commit_boundary`.
- After the first lease expired, the same SQLite store was reacquired at
  generation 2. With that lease, a strict receipt matching `codex` /
  `gpt-6-sol` and naming all four required capabilities was accepted; strict
  dispatch assigned a separate, non-overlapping probe task. No second state
  database was used for this run. The structured-edit firewall evidence comes
  from the separately reviewed and trusted Codex hook smoke above.
- An earlier harness attempt on another temporary fixture used several state
  databases while its first lease remained valid. Its HEAD-drain and strict
  dispatch results are excluded from evidence. This section records only the
  later single-store run.
- This is a local disposable capability smoke, not the deferred real-project
  pilot, representative redacted run-log corpus, hosted CI, or process-fenced
  handover evidence. The temporary project, worktree, databases, gate
  artifacts, and summaries remain under Temp; CODEXDEVTEAM and DEVDEPARTMENT
  source files were not modified by the smoke.

## Disposable live maker smoke — 2026-10-01

A second temporary Git project ran the real Codex CLI with an invocation-only
`projects.<pilot-path>.trust_level="trusted"` override. The project was
installed using `codexdevteam init`; the installation marker stayed inactive.
A leased HEAD seeded an in-progress task assigned to `pilot-builder`, owning
only `pilot.txt`. The live maker created the owned file through `apply_patch`.
The live maker's attempt to create the new repository-root `unauthorized.txt`
was rejected by the `PreToolUse` hook with `outside owned paths`. No such file
was created. A third edit attempt using `../` was rejected as path traversal.

For these hook checks, Codex was invoked with
`--dangerously-bypass-hook-trust` because the disposable project hook definition
had not been reviewed through the normal Codex trust UI. This flag was limited
to the disposable pilot, and the workspace-write sandbox remained enabled. A
separate invocation emitted one syntactically valid CONTROL progress report
through `codexdevteam-control` into the injected outbox. The report remained
untrusted and pending; this smoke did not drain it under a live HEAD lease.
The user-level trust store was not changed, and the temporary project was
removed after evidence collection.

This proves live Codex maker invocation, allow/deny decisions from the
structured-edit hook when hook trust is bypassed for the disposable project,
and runtime-produced CONTROL outbox emission. It does not prove the normal
hook-review/trust path, HEAD-side draining in the same invocation lifecycle,
task-worktree isolation, post-run territory verification, strict capability
receipt issuance, or end-to-end activation. The worker must remain unverified
for strict mode until those capabilities are verified without the bypass.

## Oikonomos inactive DEVDEPARTMENT sidecar check — 2026-10-02

- Candidate: `C:\CLAUDECODE_TOOLSETS\oikonomos`. Read-only inspection reported
  `mode=devdepartment_sidecar`, `devdepartment_present=true`, and
  `activation_allowed=false`.
- The candidate was already on `task/TASK-365-cx`; its PLAN records
  `TASK-365` as `in_progress` and assigned to `CX`. The checkout also had
  pre-existing edits to `AUTOPILOT_LOG.md` and `apps/mobile/pubspec.lock`,
  plus untracked `.codex/` configuration, backups, retrospectives, and updates.
  These were preserved. This checkout is not a clean handover source.
- Ran `python -m codexdevteam_kernel.onboarding_cli init --project
  C:\CLAUDECODE_TOOLSETS\oikonomos` with CODEXDEVTEAM's source package on
  `PYTHONPATH`. The installer added only `.codexdevteam/installation.json`
  and the two files plus managed-files manifest under
  `.codexdevteam/framework/sidecar/`.
- Re-inspection recognized both systems and returned
  `activation_allowed=false`; the installation marker has
  `active_head=null`, `activated=false`, and
  `integration_mode=devdepartment_sidecar`. No HEAD lease was acquired.
- This verifies inactive sidecar installation and coexistence metadata only.
  It does not verify a CODEXDEVTEAM-controlled pilot, task migration, runtime
  dispatch, or process-fenced handover. DEVDEPARTMENT remains the incumbent;
  do not activate CODEXDEVTEAM on this checkout while its active task and
  local changes remain unresolved.

## Supervised disposable utility pilot — 2026-10-03

- A fresh standalone `TextTidy` utility project was created under
  `C:\Users\Nuburo\AppData\Local\Temp\codexdevteam-pilot-rerun-20261003`.
  The installation marker remained inactive. The supervised task ran in a
  separate Git worktree on branch `codexdevteam/TASK-TEXTTIDY-001`.
- The live Codex maker implemented and corrected the text normalizer and CLI.
  An independent Codex checker reviewed three committed SHAs, requested two
  focused corrections, then approved final SHA
  `dbdad75f927f6ee93a539ae059c003537beecc13`. The task reached `done`; the
  supervisor was parked and its HEAD lease released.
- The final SHA-bound gate passed SHA/clean-worktree, territory, secret scan,
  build, typecheck, full tests, baseline build/typecheck/tests, and baseline
  analysis. Reachability and mutation checks were not configured for this
  Python utility. The maker's focused suite passed 9 tests; the independent
  checker also reports 97,656 read-only exhaustive input cases passed.
- Windows sandboxing prevented the maker from writing the linked worktree's
  shared `.git` metadata, so the host controller committed only the task's
  authorized owned paths. The maker-side `codexdevteam-test` cache also could
  not write under the shared `.git/codexdevteam` directory. The host gate ran
  the configured tests and registered their exact-SHA evidence before the
  maker submitted CONTROL. This is supervised end-to-end evidence with a
  host-side commit/gate step, not evidence that the sandboxed maker can write
  shared Git metadata or test cache files.
- The project contained the normal Codex hook configuration and no trust-bypass
  flag was used, but the hook had not been trusted at this project path and
  therefore very likely did not run. This run did not capture an active-hook
  trace or repeat the `/hooks` allow/deny proof here. Territory enforcement
  therefore relied on the host commit and gate; the host commit staged only
  owned paths, silently filtering any out-of-scope changes instead of refusing
  the commit. This pilot does not establish hook activation or host-side
  refusal of out-of-territory edits. Use the separately recorded trusted
  disposable-hook review only as evidence for that other project path.
- The pilot summary and per-run metrics remain in the disposable Temp
  artifacts. Usage was recorded, but model spend is unpriced because no rate
  configuration was supplied. This utility pilot does not verify DEVDEPARTMENT
  handover, reverse transfer, process fencing, or representative field-log
  accuracy, and does not authorize activation on an existing project.

## v0.2 fresh-brief planner invocation — 2026-10-07

- Project: `C:\Users\Nuburo\AppData\Local\Temp\CODEXDEVTEAM-v02-pilot-20261007`,
  a newly initialized disposable Git project with inactive CODEXDEVTEAM
  metadata and no DEVDEPARTMENT files.
- The current source CLI ran `host-plan --confirm-write` from a brief. Its
  configured planner identity was `codex-head` / `codex` / `gpt-6.1-sol`.
  The live invocation completed successfully and created a mechanically
  validated, unassigned three-task PLAN with provenance:
  invocation `planner-8ea9a11bce764a1b9d8ef20e3ca92709`, brief SHA-256
  `facff97ec886e4d2152361897676f93b16fa883d73defe50bfa119594ce42e93`,
  response SHA-256
  `33624ce58c61988c0e6e0db5d31346cfacf1b2a4c66c2324344ee6df14ec6173`, and
  validated plan-body SHA-256
  `36fb0a62713ac0e10efde63138d8cdf074f33d519af4c90e65987b9bfb4e9bfb`.
- The project remained parked; no task database, active HEAD lease, maker
  dispatch, host commit, gate, or checker run was created. This verifies only
  live brief-to-plan generation. It is not a strict-worker receipt or the
  v0.2 unattended pilot. The generated project and PLAN remain in Temp for
  continuation. Invocation usage was not retained by the planner command, so
  no cost or usage metric is claimed for this run.

## Windows live worker qualification — 2026-10-07

- Qualification project:
  `C:\Users\Nuburo\AppData\Local\Temp\CODEXDEVTEAM-v02-qual3-20261007-gpt61`.
  It is a disposable clone of the fresh brief-generated pilot. The acceptance
  pilot itself remained parked and unmodified.
- This qualification used the real Codex runtime adapter, supervisor
  invocation, Windows Job Object quiescence, host commit, mechanical gate,
  CONTROL drain, and independent checker. Strict dispatch was temporarily
  disabled in this one-task qualification harness to generate model-bound
  evidence before strict workers are registered; it is not evidence that the
  final host-run or unattended acceptance pilot has passed strict dispatch.
- Maker `codex-maker-gpt61` / `codex` / `gpt-6.1-sol` completed `TASK-1` in
  115.8 seconds. The host committed only its three owned paths at
  `dec8018170b1da76a0d94eae3d154502f9c49028`. The maker submitted CONTROL
  report `control-2720b70136614c82a5a94e425a122590`; HEAD applied its progress
  note after the gate. Maker usage was 113,317 input and 1,292 output tokens
  (102,400 cached input); cost is unknown because no price rate was configured.
- The exact-SHA gate passed at that commit with fingerprint
  `b28e1c7d7230167469fbf0f38849d382349f13db1dbf899dce9daec0e7552e7d`. Build,
  typecheck, full tests, secret scan, territory, and clean-worktree checks
  passed. The baseline full-test check failed because the baseline did not
  contain the task's new tests; baseline analysis treated those as newly
  introduced failures, so the current gate passed.
- Checker `codex-reviewer-gpt6` / `codex` / `gpt-6-sol` reviewed that exact SHA
  read-only and approved it. The task reached `done`, PLAN was projected, the
  host parked, and the HEAD lease was released. Checker usage was 85,109 input
  and 576 output tokens (66,944 cached input); cost is unknown.
- This supplies live capability qualification evidence for the configured
  runtime/model pair. It is one task, not the unattended brief-to-integration
  acceptance pilot; it did not exercise strict dispatch, bounded rework, or
  integration. The final pilot still needs strict registry receipts, a fresh
  capacity snapshot, and the full bounded host-run.
- Initial nested Codex runs launched directly from Python could not use their
  file tools (`helper_unknown_error: setup refresh had errors` / failed writes).
  A disposable PowerShell-launched write probe succeeded. `CodexExecAdapter`
  now uses a native PowerShell wrapper on Windows; the supervised maker run
  above verifies writes, Job Object quiescence, and host commit through that
  path.

## Strict fresh-brief three-task qualification — 2026-10-07

- Project: `C:\Users\Nuburo\AppData\Local\Temp\CODEXDEVTEAM-v02-pilot-20261007`.
  The live planner invocation documented above produced the three-task PLAN;
  `host-bootstrap` seeded it, and the project was activated only after strict
  worker and capacity preflight passed.
- Active maker: `codex-maker-gpt61` / `codex` / `gpt-6.1-sol`; active checker:
  `codex-reviewer-gpt6` / `codex` / `gpt-6-sol`. Both were configured with
  `control_mode=strict`, and the supervisor used `require_strict=true`. The
  exact-SHA checker receipts use a different worker, runtime/model identity,
  and role from each maker invocation.
- `TASK-1`, `TASK-2`, and `TASK-3` each completed through maker invocation,
  host-owned task commit, passing exact-SHA gate, independent checker approval,
  and post-integration gate. The project ended with all three authoritative
  tasks `done`, all three integration receipts present, the host parked, and no
  active HEAD lease. Integrated project SHAs were
  `aeab2f9388ea4d481d8a8f20c70c77fa62a06588` (`TASK-1`),
  `f35cb52f08ec599e5c3e175883e944b13f2f3661` (`TASK-2`), and
  `84e855bd35e7c6374857cc2a4777fbda1568f8c8` (`TASK-3`). There were three
  successful maker invocations, three successful checker invocations, three
  passed task gates, three approvals, no rework, and no recorded escalation.
  Maker durations were 159.9, 227.5, and 133.9 seconds; checker durations were
  57.6, 62.6, and 49.3 seconds. The two host processes ran for about 3m48s and
  8m17s, respectively (about 12m05s combined, excluding the pause between
  processes).
- This evidence came from two bounded host processes. The first integrated
  `TASK-1`, then parked after the next PLAN integrity check found that SQLite
  had appended gate evidence absent from PLAN. The host was resumed after a
  local fix; the second process completed and integrated `TASK-2` and `TASK-3`.
  The fix compares planned task content to the immutable `task.seeded` event
  while checking live status and assignment separately. The full Windows
  contract suite then passed 294 tests with 8 skipped.
- The qualification exposed another boundary: integration's `git reset --hard`
  restored the tracked default worker registry after `TASK-1`, because the
  pilot's configured registry had not been committed. The active host kept its
  already-loaded strict registry and completed the remaining strict dispatches,
  but the on-disk pilot registry returned to its inactive template. A new
  integration guard now refuses to advance the project ref when tracked files,
  including CODEXDEVTEAM configuration, are dirty outside host-projected
  `PLAN.md`. Commit configured framework files before activation.
- Capacity was refreshed during the run from the current Codex usage status;
  `quota_remaining` was left unknown. The host has no connected automatic
  capacity source yet, and the operator refreshed the snapshot between cycles.
  This was not a fully unattended capacity qualification. The run's invocation
  receipts retain status and output hashes but not token usage for all six
  maker/checker calls, so total model spend remains unknown. The run proves the
  bounded strict workflow and integration path with this recovery, but it does
  not close the unattended v0.2 release gate.

## Normal-trust hook activation experiment at the pilot path — 2026-10-03

- Experiment path: `C:\Users\Nuburo\AppData\Local\Temp\codexdevteam-pilot-rerun-20261003`.
  The pre-existing `PLAN.md` modification was preserved. Before the probes, the
  project root had no `src/` directory or hook-probe files.
- The project hook was reviewed through interactive `/hooks`. Codex showed one
  new `PreToolUse` hook, matcher `^(apply_patch|Edit|Write)$`, command
  `python <Temp>\trace_hook.py`, synchronous mode, and a 10 second timeout. The
  review screen warned that trusted hooks can run outside the sandbox. Only
  this one hook was trusted. No trust-bypass flag was used.
- The reviewed hook-definition SHA-256 was
  `522E5C0EAB1056575EF1FCE1A9E4A043D0D21AF72EBE67B4BEC471E64BD03167`.
  The tracer's final SHA-256 was
  `83CCE46DD9AEDF2C466D42F87B51D4F027E61EA3BDDC9FD40BE4E3EC7B791A19`.
  The tracer append target was
  `C:\Projects\CODEXDEVTEAM\.codexdevteam-hook-experiment.trace.jsonl`, outside
  the pilot CLI's writable roots (the pilot directory and `%TEMP%`). The trusted
  hook created and appended this file, demonstrating that the trusted hook
  process could write outside the maker sandbox.
- A trusted edit under the active task's actual `src/**` grant was allowed. Its
  `PreToolUse` event reached the tracer; the real hook exited 0 with empty
  stdout, and `src/__codexdevteam_hook_allow_probe__.py` was created. A root
  edit outside that grant produced this response and no file:
  `permissionDecision: deny` — `Write policy denied edit: outside owned paths`.
  This is direct same-path allow/deny evidence for normal hook trust.
- The hook received the enclosing supervised session's environment, not the
  synthetic values set by the experiment shell: worker `pilot-builder`, task
  `TASK-HOOK`, and an active-task snapshot granting `src/**`. This initially
  denied the experiment's root-level “owned” probe; the allow probe was moved
  under `src/**`. Future headless experiments must launch with an explicitly
  controlled runtime context rather than assume ad-hoc environment overrides
  survive the host runtime.
- A first tracer version failed before recording because it required tracer
  environment variables that the host did not pass through. That run created
  two root-level probes and is not hook evidence. The self-contained tracer
  produced the allow/deny records above. The tracer source was then changed
  without changing its configured command path; Codex did not request another
  review. Changing only the project hook's `statusMessage` changed the config
  SHA-256 to
  `9ADF16F9D37CF05DF6E6E3F0DDAC1B463A5067C0BC582C54CC842F1CDFD47C91` and
  caused `/hooks` to show `Active 0 / Review 1`. A new Codex session then showed
  the trust prompt again. This suggests trust is tied to the project hook
  definition/command, but not to the contents of a script referenced by that
  command. An already-running session continued using its cached trusted hook
  after the on-disk definition changed; trust must therefore be verified for
  each new run, and external hook sources should be hash-pinned.
- In that new session, “Continue without trusting (hooks won't run)” was
  selected. Two separate edits then created both an owned `src/**` file and an
  out-of-scope repository-root file. No additional hook trace was recorded.
  This demonstrates that the new, untrusted definition was inactive and that
  out-of-scope structured edits could proceed in that session without a hook.
- Headless `codex exec --json` attempts did not complete: captured JSONL reached
  `item.started` for a `file_change` and then stalled pending the host's file
  action/approval path. No hook event appeared before the stall. We did not
  bypass either hook trust or the sandbox to force completion.
- Evidence status: **headless trusted-hook activation is inconclusive** because
  the invocation did not finish.
- Evidence status: **JSONL hook-event reporting is inconclusive** because the
  invocation did not finish; hook traces remain the authoritative event record.
- Before cleanup, the probes present were the two files created by the failed
  tracer run, `src/__codexdevteam_hook_allow_probe__.py`, the owned file created
  under the untrusted definition, and `hook_untrusted_out_of_scope.txt`. The
  denied root-level file remained absent. These exact probes were removed, the
  original `.codex/config.toml` restored, and the `src/` directory removed only
  after it became empty. The pre-existing `PLAN.md` edit was preserved. The
  Phase 6 normal-trust milestone remains unchecked: this experiment validates
  hook operation at a disposable path, not an installed per-run receipt or a
  production pilot.

## Windows host commit boundary — 2026-10-03

- Added a standalone host committer that snapshots and validates the complete
  linked worktree, refuses mixed owned/unowned changes, creates the commit from
  the validated bytes, and publishes it with an expected-parent ref update.
- The Codex maker runtime now returns a Windows quiescence proof only after its
  live Job Object reports zero active processes. The supervisor uses that proof
  to commit successful maker output. Commit outcomes are journaled; a refused
  commit leaves CONTROL reports pending. Gate finalization requires the exact
  SHA returned by a successful host commit.
- Windows PowerShell verification: full suite **270 passed, 8 skipped**;
  focused host-commit, supervisor, and gate scenarios also passed.
- Hosted CI for implementation commit `e349da6ddcc4c92d8dc4fb1aa316a17f3c81c7be`
  passed all four Ubuntu/Windows × Python 3.11/3.12 jobs
  ([run 37131102400](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37131102400)).
  This hosted matrix validates the fixture suite; it is not Linux runtime
  quiescence evidence.
- The supervisor allow-lists only the reserved ignored `.codexdevteam/control`
  tree from maker snapshots, so CONTROL reports remain outside task commits.
  The Windows integration fixture verifies a maker report survives host commit,
  then is drained only after the gate passes on the exact committed SHA. A
  follow-up matrix first caught that this live-process integration fixture was
  running on Linux with a simulated Windows proof; it is now Windows-only, and
  [run 37147652350](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37147652350)
  passed all four OS/Python jobs.
- Still outstanding: a fresh disposable live Codex maker run through the
  supervisor's new commit and exact-SHA gate path, with normal hook trust
  established and hook events captured directly. No commit/push or deployment
  of a pilot artifact is implied by this test evidence.

## Exact-SHA maker-to-review closeout — 2026-10-04

- The disposable `TASK-TEXTTIDY-003` maker run committed only its owned test
  path at `a664233e4b5470ebe53a47b0a05ed32849e944b7`. HEAD reused the registered
  passing gate/test receipt, advanced the task to `needs_review`, and invoked
  the configured independent checker. The checker approved that exact SHA and
  HEAD recorded the task as `done`. The supervisor was parked and its lease
  released after the bounded cycle. The pilot's `PLAN.md` was not projected or
  changed in this closeout.
- The pilot's host-gate handoff initially exposed that a maker cannot report
  the host-created commit SHA or receipt. `finalize_maker_gate()` now lets HEAD
  attach registered passed test evidence and transition an in-progress task to
  review without a maker-authored SHA report. This does not treat model output
  as proof; the gate receipt and checker verdict remain SHA-bound.
- Windows lifecycle coverage in
  `tests/test_kernel_contracts.py::SupervisorTests.test_host_gate_moves_task_to_review_without_maker_sha_report`
  now exercises host commit, exact-SHA gate, automatic review transition,
  independent checker invocation, and final review application with fixture
  adapters. It is deterministic integration coverage, not hook activation
  evidence or a substitute for a fresh live pilot.
- Local Windows PowerShell suite: **272 passed, 8 skipped**. Hosted CI for
  `ff3913cfa5a29fd88c628fc6852a1e8859338a0e` passed all Ubuntu/Windows × Python
  3.11/3.12 jobs ([run 37158591080](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37158591080)).
  The prior run had two Windows Python 3.11 lease-test timing flakes with
  sub-150 ms expirations; the fixtures now use wider renewal windows.
- The 2026-10-04 plain-PowerShell headless attempt completed quiescently (exit
  0; zero active processes), but its nonce-bound trace was empty and both the
  owned and out-of-scope probes were created. The JSONL substring "hook" came
  from ordinary text, not an event record. The new fixture path had no hook
  trust entry; missing trust is plausible but unproven. This is
  **inconclusive / hook not observed**, not a pass. No second attempt was made.
  Hook activation is optional and is not the territory security boundary; see
  `docs/CODEX_HOOKS.md`.

## Host commit refusal handling — 2026-10-04

- Each maker invocation now has its own CONTROL outbox, and HEAD rejects a
  report whose embedded invocation ID differs from the maker cycle. On a
  quiescent host-commit refusal, HEAD archives that invocation's reports under
  host-owned Git metadata. An out-of-scope refusal also quarantines the refused
  files there and restores those paths to the recorded parent state.
- After successful quarantine, HEAD allows exactly one retry with a prompt that
  names the refused paths; owned edits remain available. A second refusal, or a
  refusal that cannot be safely quarantined, blocks the task and records the
  reason. The worktree is not automatically deleted.
- Windows regression coverage exercises stale-report isolation, quarantine and
  restore, preservation of owned edits, one successful retry, blocking after a
  second refusal, and a separate task worktree remaining uncontaminated. See
  `tests/test_kernel_contracts.py::SupervisorTests`.
- Full local Windows contract suite: **275 passed, 8 skipped**. Hosted branch-head
  CI run [37185171206](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37185171206)
  passed all four Ubuntu/Windows × Python 3.11/3.12 jobs, including the updated
  refusal-handling and strict-capability coverage.
- Supported supervised maker commits are Windows-only. Ubuntu/Windows hosted
  CI covers the portable contract suite; Linux maker commits fail closed because
  the supervisor currently requires a verified Windows Job Object proof.

## Uninterrupted strict fresh-brief acceptance — 2026-10-07

- Project: `C:\Users\Nuburo\AppData\Local\Temp\CODEXDEVTEAM-v02-unattended-final-20261007`.
  It was initialized as a fresh disposable Git project, installed inactive,
  configured with strict workers and protected verification policy, and run
  through one `host-run --brief` process. The Codex planner generated and
  validated `PLAN.md`; host bootstrap seeded task state; explicit confirmation
  activated one HEAD lease.
- The run used one strict maker identity, `codex-maker-gpt61` / `codex` /
  `gpt-6.1-sol`, and a distinct strict checker identity,
  `codex-reviewer-gpt6` / `codex` / `gpt-6-sol`. All three planned tasks
  completed maker execution, host-owned commit after Windows Job Object
  quiescence, exact-SHA gate, independent checker approval, and branch
  integration. The integrated SHAs were `aa8ea750daa48eb4448200d36e40e316790fcec1`
  (`TASK-1`), `3fbf87d2e512c00a2b281e34c27e70673b9c0692` (`TASK-2`), and
  `ac1eab2de9337acb02712f1021cc66c1c3879626` (`TASK-3`).
- Verified pilot metrics: 3 reviewed tasks, 3 first-pass approvals (100%),
  3 review sessions, 3 passed gates, 0 gate rejections, 0 changes requested,
  0 escalations, 0 interrupted/recovery invocations, and 0 live maker
  invocations at close. All six maker/checker receipts recorded token usage:
  727,363 input tokens, 11,273 output tokens, and 645,632 cached input tokens
  in aggregate. Invocation pricing was not configured, so model spend remains
  unknown. Total time from plan generation through final integration and park
  was about 9m25s.
- One manually written capacity snapshot was seeded before activation from the
  current account-level Codex usage status. It was not refreshed during the run;
  its one-hour freshness window was still valid at close. Per-model quota was
  unknown (`quota_remaining` was null). This proves an uninterrupted workflow
  under an initial manual observation, not a connected automatic capacity
  source or per-model capacity measurement.
- The host completed 50 bounded cycles with every task done, then parked and
  released the HEAD lease. `host-status` confirmed `lease_active: false`, all
  three tasks `done`, and no running maker. The project main branch contains all
  three integrations; its generated `PLAN.md` remains untracked at the disposable
  project root, while runtime state and capacity files are ignored.
- The two earlier attempts on this disposable fixture were excluded from
  acceptance: the first fixture lacked a Python bytecode cache policy, and the
  second was correctly refused because ignored `__pycache__` paths were not
  allowlisted. CODEXDEVTEAM now accepts only owner-configured narrow ignored
  patterns in its protected verification policy; matching ignored paths are
  excluded from the commit, and other ignored paths still refuse it. The final
  acceptance run used `**/__pycache__` and `**/__pycache__/**`.
- Local Windows PowerShell full suite after this policy change: **295 passed,
  8 skipped**. Hosted CI run [37672717256](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37672717256)
  passed for commit `9bd0469fcaf5e0b5f59075bf9eb1f8ec20e3aa34`. The subsequent
  token-coverage reporting change also passed local usage and pilot tests plus
  the full Windows suite (**295 passed, 8 skipped**); hosted CI run
  [37674230702](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37674230702)
  passed for commit `798c4ef7a07ad42d3f145030dfcb50927992d131`.

## Live Codex capacity-source preflight — 2026-10-07

- Command: `codexdevteam host-preflight --project <disposable-capacity-smoke>`
  using the default `codex_app_server` source and the installed Windows Codex
  executable. The preflight queried `account/rateLimits/read` through the
  app-server stdio protocol.
- Result: exit code 0, `worker_and_capacity_ready: true`, both configured
  worker IDs present, no findings, `activation_available: false`,
  `activation_state: parked`, and `dispatch_performed: false`. The output
  records readiness only; account usage values are intentionally not copied
  into this evidence log.
- The first transport implementation sent initialization and the capacity
  request in one batch and received no account response. The adapter was
  changed to wait for the initialize response before sending `initialized` and
  the read request. The bounded request/response, timeout, failure, and
  per-refresh behavior are covered by `tests/test_capacity_source.py`.
- This establishes live Windows adapter operation and read-only preflight. It
  does not replace the 2026-10-07 unattended acceptance run, which used a
  manually seeded capacity snapshot, or prove quota refresh across a live
  multi-cycle maker run.
- Validation after the adapter and refresh regression: Windows PowerShell full
  suite **309 passed, 8 skipped** on Python 3.11 and 3.12; `git diff --check`
  passed.
- The read-only `usage` CLI was also run against the acceptance project's
  authoritative `state.sqlite`. It reports 3 successful maker invocations and
  3 successful checker invocations, all with complete token counters; aggregate
  usage is 727,363 input, 11,273 output, and 645,632 cached input tokens.
  `cost_usd` is null for both configured models because no effective pricing
  rates were supplied. No API price was substituted for subscription usage.
- Packaging validation built and installed the current wheel into a disposable
  target. The five packaged entry points passed: `codexdevteam`,
  `codexdevteam-test`, `codexdevteam-control`, `codexdevteam-fast-eval`, and
  `codexdevteam-codex-hook` (which returned the expected fail-closed denial for
  an empty request).

## Live capacity-source supervised-run attempt — 2026-10-07

- A fresh disposable Windows project passed `host-preflight` with the live
  Codex app-server capacity source, then activated with explicit confirmation.
  The host generated one task and dispatched it to the configured Codex maker.
- The maker created the requested implementation and test files in its task
  worktree. A manual focused run passed all 8 task tests. The runtime adapter
  then reported `launch_failed`; the host made no commit, ran no gate or
  checker, and did not report task acceptance. The exact adapter exception was
  not retained by the earlier runtime code, so its root cause is unknown.
- The host closeout path also raised when it attempted to run a gate without a
  successful host commit. That prevented the continuous runner from recording
  its normal incomplete-closeout result. The code now skips gate/checker for a
  missing commit and journals callback exceptions as a sanitized
  `supervisor.continuous_closeout_failed` event before stopping the cycle.
  Maker failures retain the exception class while omitting the exception
  message.
- A successor invocation recovered the interrupted run using the Windows Job
  Object process-tree proof (`verified=true`, no active processes), preserved
  the task, and escalated it for human recovery. No retry was dispatched.
- Evidence: this attempt proves the live capacity source gated activation and
  dispatch in a supervised cycle, but it does not prove end-to-end maker,
  commit, gate, checker, or integration acceptance. The disposable task
  worktree is preserved for inspection. Later attempts below identified the
  closeout and fixture-ignore issues and completed a fresh end-to-end run.
- After the closeout regression fixes, the Windows PowerShell full suite passed
  **311 tests, 8 skipped** on Python 3.12 and **311 tests, 8 skipped** on
  Python 3.11. `git diff --check` also passed.
- Built the pre-version-bump source wheel with Python 3.11, installed it into a
  disposable target, and smoke-ran all five installed entry points. After
  setting package and framework version to `0.2.0`, rebuilt and installed
  `codexdevteam-kernel-0.2.0-py3-none-any.whl`; installed metadata and
  `codexdevteam_kernel.__version__` both report `0.2.0`, all five entry points
  start, and the Codex hook returns its expected fail-closed denial for an
  invalid PreToolUse event. Wheel SHA-256:
  `0e384ba6f35410f7615cf53f0b766fb0e76382693b2681fa5379663de0a570dc`.

## End-to-end live-capacity Windows acceptance — 2026-10-07

- A fresh disposable Windows project passed `host-preflight` with both strict
  workers ready and the default live Codex app-server capacity source. The
  project had a committed baseline, a one-task plan, and narrow Git ignore
  entries for Python bytecode and CODEXDEVTEAM runtime state, gates, control,
  and logs. Framework configuration remained tracked; these host paths were
  not added to the builder ignored-path allowlist.
- The bounded supervisor ran one maker invocation (`codex` / `gpt-6.1-sol`)
  and one independent checker invocation (`codex` / `gpt-6-sol`). The host
  committed maker SHA `2e03ed1795a44af8e37d44e7637c7eeaeddf5f01`; the exact-SHA
  gate passed; the checker approved that same SHA; and integration completed
  at `6ebf2173e4db3616715bb6835a7cc09f1e4d7d39`. The integrated diff contains
  only `src/estimate.py` and `tests/test_estimate.py`.
- Final state: task `DONE`, project branch `main`, no open escalations, no
  active maker invocations, no active HEAD lease, and supervisor parked at its
  configured 25-cycle bound. The verified invocation liveness record is
  `completed` under a Windows Job Object.
- Pilot metrics: 1/1 first-pass approval, one passing gate, one integration,
  zero gate rejections, zero rework, zero open escalations. The maker ran for
  95.672 seconds and checker for 38.812 seconds. Both receipts have complete
  token counters: 200,860 input, 2,149 output, and 169,472 cached input tokens
  combined. `cost_usd` remains null because no subscription cost or configured
  attribution rates were available.
- The preceding attempts found two setup-sensitive failures: `compileall`
  output must be ignored so the SHA-bound test cache sees a clean task tree;
  runtime state and gate artifacts must be ignored by Git so the integration
  cleanliness check sees a clean project checkout. The successful fixture
  applied narrow `.gitignore` entries for those generated paths. A broad
  `.codexdevteam/` ignore is not accepted by the host commit boundary.
- This proves one complete unattended task cycle using live capacity on
  Windows. It does not make one task a statistically representative field
  accuracy sample, provide subscription-dollar spend, or constitute hosted CI
  evidence for the current uncommitted CODEXDEVTEAM patch.

## Three-task live-capacity Windows acceptance — 2026-10-07

- A new disposable Windows project started from a validated three-task brief
  with disjoint source/test ownership. `host-preflight` found both strict
  workers ready through the live app-server source. The host ran the tasks
  sequentially under one HEAD lease and was asked to park after the final
  integration.
- All three makers completed under `codex` / `gpt-6.1-sol`; all three
  independent checkers used `codex` / `gpt-6-sol`. Each task passed its
  exact-SHA gate, received first-pass approval on that same SHA, and produced a
  completed integration receipt. The final project diff from its baseline is
  exactly six files: the three implementations and their three focused test
  files.
- The host parked on the requested stop with all three tasks `DONE`, no open
  escalations, no active maker invocations, no active lease, and all three
  Windows Job Object liveness records completed. There were no task retries,
  launch failures, gate rejections, checker changes, or review rejections.
- Receipt binding for each task:

  | Task | Gate and approved SHA | Integrated SHA |
  | --- | --- | --- |
  | `TASK-1` | `b62269828d524ad1bc52efe502215467186662b4` | `b26082f83a3a11e92f2159b5c9d59088bc452585` |
  | `TASK-2` | `f93d3c520e55a9ddd37b19805fb1b06305b6bb30` | `46a6844e316bd58ce70063156ff8ac1ab641d4ea` |
  | `TASK-3` | `068dd3baceb25fb39e284792b73089c1debbfefc` | `887eb6270d05bcf6d9a50f9fb34003c77fc8b429` |

  Each review receipt references the corresponding gate SHA shown above.
- Recorded metrics: 3/3 first-pass approvals, 3 passing gates, 3 integrations,
  0 rework, and 0 open escalations. Maker duration was 219.859 seconds total;
  checker duration was 216.234 seconds total. All six receipts had complete
  counters: 777,537 input, 9,995 output, and 691,456 cached input tokens.
  `cost_usd` remains null because subscription spend is not exposed and no
  attribution rates were configured. Activation-to-park elapsed time was 512
  seconds. The final integrated project HEAD was
  `887eb6270d05bcf6d9a50f9fb34003c77fc8b429`.
- Together with the preceding one-task live-capacity run, the live-source
  sample is 4/4 first-pass approvals and 4/4 integrations. The earlier separate
  three-task manual-snapshot pilot remains its own data point. These runs
  establish repeatable bounded Windows operation but do not set a statistical
  accuracy threshold or validate a broader task mix. Optional fast-tier
  routing remains disabled. The authoritative state database is preserved in
  the disposable pilot at
  `C:\Users\Nuburo\AppData\Local\Temp\CODEXDEVTEAM-live-capacity-acceptance-20261007-r7\.codexdevteam\state\state.sqlite`.

## v0.2 candidate hosted CI — 2026-10-07

- Candidate commit: `37765a273064fa506753cee17e3aec743bf72466` on
  `codex/v02-parity`.
- Hosted workflow [37691384320](https://github.com/alboogycOdR/CODEXDEVTEAM/actions/runs/37691384320)
  passed all four matrix jobs: Windows and Ubuntu compatibility on Python
  3.11 and 3.12. Each job compiled and installed the package, smoke-checked
  the installed command and hook entry points, imported the kernel, and passed
  the contract suite.
- Windows Python 3.11 completed in 2m38s and Windows Python 3.12 in 2m42s.
  Ubuntu compatibility jobs completed in 18s and 16s respectively.
- This is hosted evidence for the exact v0.2 candidate source. Ubuntu remains
  compatibility coverage; supervised maker execution remains Windows-only.
