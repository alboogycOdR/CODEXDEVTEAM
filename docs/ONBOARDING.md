# Onboarding Contract

CODEXDEVTEAM has three onboarding modes.

## Mode A — fresh project

No compatible development OS is detected.

Onboarding:
1. inspect repository and stack;
2. install the CODEXDEVTEAM framework;
3. create project-owned coordination state;
4. configure project verification commands;
5. configure the builder registry;
6. smoke-test runtime launch and worktree write capability;
7. validate CONTROL emission for each proposed strict-mode worker;
8. enable strict mode only for verified workers;
9. initialize HEAD lease;
10. run harness audit.

The `codexdevteam init` command stages packaged framework defaults and an
installation marker as one directory rename. The bundled Codex planner is a
candidate with a configuration placeholder for its concrete model; no workers
are active. The marker starts with `active_head: null` and `activated: false`,
and installation never acquires HEAD. Live runtime/write/CONTROL smoke steps
remain required before strict mode or activation.

Before gate runs, configure Git to ignore mutable runtime artifacts under
`.codexdevteam/project/` while keeping project-owned configuration files
trackable. At minimum, ignore SQLite databases and sidecar files plus the
`control-outbox/` directory (for example, with local `.git/info/exclude`
patterns). The installer does not edit a project's `.gitignore` or Git exclude
file. Commit the inactive framework defaults and any configuration intended
to be shared before running a clean-worktree verification gate.

Fresh installs also include `.codexdevteam/framework/task-routing.json`, which
maps explicit `Task_Class` labels to abstract capability floors. Projects may
edit the mapping to match their own configured `capability_order`; it selects
no runtime or concrete model. Load it with `TaskClassPolicy.from_file()` and
pass the resulting policy to the supervisor. Unknown task classes are refused.

Fresh projects prefer safe modern defaults, including batch push behavior and deterministic pre-review gates where configured.

## Mode B — existing DEVDEPARTMENT project

Detect and validate the incumbent installation.

Onboarding:
1. identify DEVDEPARTMENT framework/protocol versions;
2. do not overwrite framework-owned files;
3. validate shared-state compatibility;
4. install CODEXDEVTEAM compatibility/head-adapter state only;
5. leave DEVDEPARTMENT as active HEAD;
6. offer an explicit handover workflow;
7. activate CODEXDEVTEAM only after the incumbent is parked and the lease can be acquired safely.

The sidecar installer accepts only new files under
`.codexdevteam/framework/sidecar/`, stages those files with an inactive
installation marker, and publishes them atomically. The inspector recognizes
that explicit inactive marker as an intentional sidecar state; unrecognized
dual installations remain conflicts. DEVDEPARTMENT-owned paths are never
written by this installer.

Installation is not activation.

## Mode C — existing CODEXDEVTEAM project

Run version-aware sync/upgrade:
- preserve project-owned state;
- update clean framework-owned files;
- detect local modifications as conflicts;
- apply dedicated merge rules to mixed-ownership configuration;
- never silently change safety/autonomy posture.

`codexdevteam upgrade --project <path>` updates only files named by the
installation's `.managed-files.json` ownership baseline and the incoming
framework bundle. It stages updates and the next manifest together, rechecks
hashes before replacement, and rolls back on failure. Local edits conflict;
omitted managed files are rejected rather than silently orphaned. The
installation marker and `.codexdevteam/project/` state are not rewritten.
DEVDEPARTMENT sidecars use a separate managed prefix. Older installations
without a hash manifest stop with a diagnostic and require an explicit
migration baseline before upgrade.

`codexdevteam usage --state-db <path> [--rates <json>]` reads invocation events
without writing to the project database. Costs use explicit runtime/model
rates with effective timestamps; missing usage or rates are reported as
unmetered. The installer includes an empty usage-rate template, not vendor
prices.

## Verification principle

Missing prerequisites are failures, not empty successes. Onboarding must report exactly what was verified live versus inferred from configuration.
