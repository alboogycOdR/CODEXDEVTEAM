# Builder CONTROL reports

Builders submit task progress and state requests with `codexdevteam-control`.
The launcher supplies `CODEXDEVTEAM_TASK_ID`, `CODEXDEVTEAM_WORKER_ID`,
`CODEXDEVTEAM_INVOCATION_ID`, and `CODEXDEVTEAM_CONTROL_OUTBOX`; callers should
provide only a JSON object on stdin. The invocation ID is attached to each
report by the CLI. For example:

```json
{"requested_state":"blocked","blocked_reason":"Waiting for API credentials"}
```

The CLI stores each report atomically as an immutable JSON file in that maker
invocation's outbox. It never opens the state database. Event IDs are unique
and duplicate IDs cannot overwrite existing reports. Do not include secrets in
progress notes, artifact references, or test evidence.

After a successful host commit and gate, HEAD drains only the outbox for that
maker invocation while holding its lease. Reports are checked against the
invocation ID, current task assignment, current state, transition rules, and
registered test receipts. Builders cannot mark a task done. Accepted reports
move to `applied/`; invalid reports move to `rejected/` for audit. A stale or
expired HEAD lease leaves the report pending for a later cycle. An outbox is
untrusted input and does not itself provide authorization. After a host-commit
refusal, HEAD moves that invocation's reports to host-owned Git metadata, so a
later invocation cannot replay them.

For a `needs_review` report, the maker launcher can set
`defer_control_drain=True` on the bounded invocation cycle. HEAD runs the
mechanical gate at the maker worktree SHA, then calls
`Supervisor.finalize_maker_gate()`. This registers the gate's test receipt and
passed gate artifact under the active lease before applying the queued report.
If the report's evidence reference does not match the registered run at its
reported SHA, CONTROL is rejected and quarantined.

The protocol currently supports `requested_state` (`in_progress`, `blocked`,
or `needs_review` subject to validation), `progress_note`, `blocked_reason`,
`artifacts`, `test_evidence`, and `head_sha`. A `needs_review` request requires
a matching registered passed test receipt for the exact SHA; report fields alone
cannot provide that proof.

For an out-of-scope commit refusal, HEAD quarantines the refused files outside
the maker worktree, restores those paths to the task's recorded parent state,
and allows one bounded retry with the refused paths in the prompt. A second
refusal blocks the task with an `OWNERSHIP_CONFLICT` reason. Refusals that cannot
be safely quarantined are blocked without retry.

When HEAD is configured with the main `project_root`, CONTROL state changes are
committed with a durable PLAN projection intent. The task state/event and
projection intent share one SQLite transaction; PLAN is replaced atomically
under the same HEAD writer lock. If the file step fails, the report remains in
the outbox and the committed projection can be replayed after lease recovery.
The bounded maker finalizer accepts `project_root` for this behavior. Callers
that omit it retain state-database-only behavior and must not treat PLAN as a
synchronized view.
