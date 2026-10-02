# Local runtime smoke evidence

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
  Ubuntu and hosted CI remain unverified.

## Ubuntu WSL retry after Ubuntu container run — 2026-10-02

- The same full-suite command was retried after the disposable Ubuntu 24.04
  container had exited. WSL again failed before starting Python with
  `Wsl/Service/CreateInstance/HCS_E_CONNECTION_TIMEOUT`.
- No test ran during this attempt. The successful Ubuntu 24.04 container run
  above verifies Ubuntu userspace; WSL startup and hosted CI remain unverified.
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
