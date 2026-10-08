# Manual generation lane (Wave O)

CODEXDEVTEAM adapts the delivered O-A/O-B portion of DEVDEPARTMENT Wave O from
`C:\CLAUDECODE_kingdom.work\DEVDEPARTMENT` (Wave O merge `9f7b019`). It is an
optional way to start a fully specified new-file task with one bounded Codex
generation, then send the generated draft through the ordinary supervised maker,
host commit, mechanical gate, and independent checker. The nonce-framed stream
parser and deterministic verifier are adapted from the DEVDEPARTMENT implementation.

The lane is **manual** and `auto_enabled` is `false` in
`.codexdevteam/framework/generation.json`. A fresh project's normal dispatch
behavior is unchanged until an operator stages a specific task. Automatic routing,
comparative field measurement, board metrics, and provider batch adapters remain
future work, as in the reference Wave O plan. No DEVDEPARTMENT project or state is
modified by this lane. CODEXDEVTEAM selects the lane through the explicit CLI
call; it does not add a `Lane` field to the authoritative task protocol while
automatic routing is disabled.

## Eligible task

Use a pending, unassigned task whose acceptance criteria, new output paths,
declared exports, dependencies, and test command are fixed. Discovery, debugging,
brownfield edits, and tasks that already have a task branch or worktree use the
normal iterative maker path. The task's `Owned_Paths` must cover every output;
configured protected paths still need `Protected_Grants`. `PLAN.md`, `.git/`, and
`.codexdevteam/` are never generator territory, including with a grant.

The project must be a configured fresh CODEXDEVTEAM installation with a parked
supervisor, an available live-verified strict Codex maker, a different checker
model, a current capacity observation, and no competing HEAD lease. Keep the
manifest inside the project and commit it with the task specification before
staging. Do not run this command from a DEVDEPARTMENT sidecar.

## Manifest

Write a JSON file such as `generation/TASK-001.json`:

```json
{
  "files": [
    {
      "path": "src/greeter.py",
      "purpose": "Return a greeting for a supplied name",
      "exports": ["greet"],
      "est_lines": 20
    }
  ],
  "context": ["SPEC.md"],
  "conventions": "Python standard library only; UTF-8 and LF line endings.",
  "dependencies": {},
  "tests": "python -m unittest discover -s tests"
}
```

`context` lists small, read-only project files to quote in the generation packet.
`files` lists **new** files in output order. `exports` names definitions the
deterministic verifier can check. The task acceptance criteria come from the
authoritative task record, not from the manifest. Keep the manifest and context
concise; the configured packet and continuation size limits are enforced.

## Windows PowerShell flow

Set the same three arguments for each command. `--unit` selects one strict maker
when the registry has more than one:

```powershell
$project = 'C:\Projects\NEW-PROJECT'
$task = 'TASK-001'
$manifest = "$project\generation\TASK-001.json"
codexdevteam-generate --project $project --task $task --manifest $manifest --unit maker-id classify
codexdevteam-generate --project $project --task $task --manifest $manifest --unit maker-id packet
codexdevteam-generate --project $project --task $task --manifest $manifest --unit maker-id stage --confirm-stage
codexdevteam-generate --project $project --task $task --manifest $manifest --unit maker-id report
```

`classify` reports eligibility and reasons. `packet` shows the exact bounded
prompt and nonce before invoking a model. `stage` takes the exclusive HEAD lease,
runs a read-only Codex invocation in an isolated temporary Git directory, records
each invocation, accepts only complete nonce-delimited files, and can continue at
the first unfinished file up to the configured limit. An incomplete result returns
nonzero and keeps its host-owned state for a bounded resume with the same command.

After a staged result, run `codexdevteam-generate ... verify` for manifest,
syntax, export, and placeholder findings. Then start the ordinary supervised host
run. The task worktree contains **uncommitted** generated files. The maker must
inspect, run the project's full checks, and repair them. The host commits only
the final checked bytes after the maker has exited; the gate and checker decide
acceptance. A staged receipt is checked again before dispatch against the task,
active strict maker identity, protected paths, Git base, and file hashes.

The `report` command provides per-task segment counts, runtime token values when
the adapter reports them, duration, and status. It does not claim measured cost
savings or production benefit. Follow the reference O-C matched-task protocol
before considering automatic routing.

## Failure handling

If a segment fails, stalls, is truncated, changes identity, or violates territory,
the stage is incomplete. Inspect `report` and the host-owned files under
`.codexdevteam/state/generation/<TASK-ID>/`; do not hand-edit raw segments,
state, or receipts. Do not dispatch a task with an incomplete generation. A changed
manifest, task base, model, or packet cannot resume the old run. The ordinary
iterative maker path remains available after a deliberate operator decision to
retire the incomplete generation state and any unused task branch.

The generation verifier is a cheap filter. It does not replace the project's
build, typecheck, full test command, host territory check, or independent review.
