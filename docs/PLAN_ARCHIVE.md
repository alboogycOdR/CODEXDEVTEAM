# PLAN task archival adapter

The kernel keeps complete, versioned completed task records in the SQLite task
archive. `plan_archive()` is the Markdown interoperability projection used to
keep a legacy or human-readable `PLAN.md` compact: it replaces done blocks from
older lettered waves with a minimal `Status: done` plus `Archived:` stub and
returns the exact original blocks for monthly archive files.

The adapter is deterministic and does not edit PLAN or acquire HEAD authority.
It excludes active tasks and tasks in the current wave. If there is no lettered
wave context it does nothing. An eligible task with an invalid/missing
`Updated_At` stays in PLAN and produces a finding. `append_archive_blocks()` is
idempotent, preserves exact task block text, refuses a conflicting existing
entry, and writes only below `plan/archive/YYYY-MM.md`.

The caller must hold the current HEAD lease, verify every archive candidate is
already complete in authoritative state, and compare-and-swap `PLAN.md` against
the exact input hash before replacing it with `ArchiveResult.text`. If the
process stops after appending archives but before PLAN replacement, retry is
safe: existing blocks are re-read and byte-for-byte checked. Bounded notes
rotation is also available through `plan_notes_rotation()` and
`append_notes_rotation()`. It replaces an oversized JSON-quoted
`orchestrator_notes` value with a dated pointer and stores the body under
`docs/handovers/` with a digest marker, so retries do not append duplicates.
The caller applies the same lease/CAS rule to the resulting PLAN text.

Archived PLAN stubs are exposed by `ParsedPlan.archived_task_ids`. Pass those
IDs to `validate_task_set(..., archived_task_ids=...)` so dependencies remain
known as completed while task records stay in the durable archive. Archived
tasks cannot be active failure owners.

For ordinary task-status changes, `StateStore.transition_task_with_plan()`
provides the lease-fenced SQLite/PLAN projection outbox and
`apply_pending_plan_projections()` recovers interrupted replacements.
`StateStore.archive_older_plan_tasks()` validates each candidate against the
authoritative completed task state, writes exact monthly blocks idempotently,
archives the versioned task payloads in SQLite, and queues the PLAN stub
replacement through the same lease-fenced outbox. A crash after the archive
append is safe to retry; a crash after the state transaction leaves a recoverable
PLAN projection intent. The HEAD lease is rechecked after archive filesystem
writes and before SQLite commit. CONTROL and review APIs also support optional
project-root projection. Assignment and the dispatch cycle expose optional
projection. A running supervisor can opt into interval-based archive
maintenance through `SupervisorPolicy.plan_archive_interval_seconds`; it is
disabled by default and requires the project root. Successful runs record their
completion under HEAD, while archive and PLAN updates use the existing
lease/CAS recovery path. Archival is independent of task completion and
installation.
