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
