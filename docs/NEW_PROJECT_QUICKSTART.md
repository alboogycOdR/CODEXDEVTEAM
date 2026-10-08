# CODEXDEVTEAM: the no-nonsense new-project guide

**For a brand-new Git project on native Windows PowerShell.** CODEXDEVTEAM is installed **parked**; the activation command below is the one that gives Codex control. This guide does not apply to a project already run by DEVDEPARTMENT.

1. **Install the current source in its own Python environment.** Use Python 3.11 or 3.12 and a signed-in Codex CLI. Current `main` includes the optional manual Wave O generation lane; the v0.2.0 release tag predates it.

   ```powershell
   $team = Join-Path $env:USERPROFILE 'CODEXDEVTEAM'
   git clone https://github.com/alboogycOdR/CODEXDEVTEAM.git $team
   Set-Location $team
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install .
   $cdt = (Resolve-Path .\.venv\Scripts\codexdevteam.exe).Path
   & $cdt --help
   codex --version
   ```

2. **Create a real project and commit its starting point.** Write `PROJECT_BRIEF.md` in plain language: desired product, scope, constraints, acceptance tests, and what counts as done. The host needs an existing Git `HEAD` before it can make task worktrees.

   ```powershell
   $project = Join-Path $env:USERPROFILE 'Projects\MY-PROJECT'
   New-Item -ItemType Directory -Force $project | Out-Null
   git -C $project init
   Set-Content -LiteralPath "$project\README.md" -Value '# MY-PROJECT'
   # Write $project\PROJECT_BRIEF.md now.
   git -C $project add README.md PROJECT_BRIEF.md
   git -C $project commit -m 'Initial project brief'
   ```

3. **Install the inactive framework.** `inspect` should identify a fresh project. `init` creates `.codexdevteam/framework/`; it does not start a supervisor or take HEAD control.

   ```powershell
   & $cdt inspect --project $project
   & $cdt init --project $project
   ```

4. **Configure three identities and real checks.** In `.codexdevteam/framework/registry.template.json`, replace the planner's placeholder model, define an `implementation` maker and a `reviewer` checker with **different** Codex model IDs, and put the maker and checker IDs in `active`. Give each active worker `control_mode: "strict"` only after a live launch, worktree-write, CONTROL, and host-boundary smoke has produced a truthful `strict_verification` receipt. Never invent a `passed` receipt. Set a unique `instance_id` and a small first-run `max_cycles_per_process` in `supervisor.json`. In `verification.json`, set `strict_supervision: true` and provide working argv arrays for `build`, `typecheck`, and `test_full`; replace every `null` command. The [Windows host runbook](WINDOWS_HOST_RUNBOOK.md) explains these gates.

5. **Keep runtime files out of Git, then commit the configuration.** Ignore `.codexdevteam/state/`, `gates/`, `control/`, and `logs/`, plus your project's build outputs. Track `.codexdevteam/framework/`; do not ignore all of `.codexdevteam/`.

   ```powershell
   @'
   .codexdevteam/state/
   .codexdevteam/gates/
   .codexdevteam/control/
   .codexdevteam/logs/
   __pycache__/
   '@ | Add-Content -LiteralPath "$project\.gitignore"
   git -C $project add .gitignore .codexdevteam/framework
   git -C $project commit -m 'Configure CODEXDEVTEAM'
   & $cdt host-preflight --project $project
   ```

   Continue only when preflight reports `worker_and_capacity_ready: true`. It checks configuration and live Codex account capacity; it does **not** replace the worker smoke in step 4. If it refuses, fix the reported finding before activation.

6. **Let Codex plan and run the work.** On a fresh project with no `PLAN.md` or task database, this one command creates a validated plan, seeds task state, takes the exclusive HEAD lease, and runs a bounded supervisor session:

   ```powershell
   & $cdt host-run --project $project `
     --brief "$project\PROJECT_BRIEF.md" `
     --confirm-plan-write --confirm-activation
   ```

7. **Watch and stop cleanly.** In another PowerShell window, use `host-status` and read `PLAN.md`. To pause, request a park; do not kill the builder's terminal. The host finishes its current closeout, then releases its lease. It integrates approved work locally, but does **not** push or deploy.

   ```powershell
   $cdt = Join-Path $env:USERPROFILE 'CODEXDEVTEAM\.venv\Scripts\codexdevteam.exe'
   $project = Join-Path $env:USERPROFILE 'Projects\MY-PROJECT'
   & $cdt host-status --project $project
   & $cdt host-stop --project $project --reason 'Operator requested pause'
   ```

   To continue a cleanly parked project later, run `host-status` first, then `host-run --project $project --confirm-activation --confirm-prior-lease-takeover`. If status shows an escalation or an unfinished invocation, inspect its evidence and the [recovery section](WINDOWS_HOST_RUNBOOK.md#restart-recovery-and-branch-integration) before resuming.

**Shortcut rule:** Use the normal maker path first. [Manual Wave O generation](GENERATION_LANE.md) is optional for a fully specified new-file task; Codex does not select it automatically.
