# DEVDEPARTMENT Interoperability Contract

## Goal

Document future interoperability with projects that already use DEVDEPARTMENT
without replacing DEVDEPARTMENT or creating two simultaneous control planes.
This is not part of the standalone v0.2 acceptance scope; v0.2 operates projects
initialized under CODEXDEVTEAM from their start. DEVDEPARTMENT remains the
behavioral reference for supervisor and dispatch policy.

## Shared/portable project concepts

Where schema versions are compatible, both systems may understand:
- task IDs and lifecycle;
- PLAN/task state;
- REVIEW/verdict history;
- specs and acceptance criteria;
- dossiers;
- Owned_Paths and dependency relationships;
- task branches and worktrees;
- test/gate evidence;
- builder identity history.

Compatibility is versioned and validated; it is never assumed from filenames alone.

## Framework ownership

An existing DEVDEPARTMENT installation remains owner of its framework-owned root files. CODEXDEVTEAM onboarding must not install a second competing copy over DEVDEPARTMENT scripts, hooks, configuration or sync-owned files.

For such a project CODEXDEVTEAM installs/adopts a compatibility HEAD adapter and handover metadata only in locations proven non-conflicting by the compatibility manifest.

## HEAD lease

The shared project carries durable HEAD metadata (final schema to be implemented), conceptually:

```json
{
  "protocol_version": 1,
  "active_head": "CODEXDEVTEAM",
  "runtime": "codex",
  "instance_id": "...",
  "acquired_at": "...",
  "heartbeat_at": "..."
}
```

Activation requires a clean/understood incumbent state. A live incompatible lease blocks activation.

## Future handover (deferred beyond v0.2)

When cross-system transfer is scheduled, DEVDEPARTMENT -> CODEXDEVTEAM and the
reverse must:
1. validate project protocol version;
2. inspect inflight tasks and worktrees;
3. park/stop incumbent orchestration;
4. snapshot/record state;
5. acquire the new HEAD lease atomically;
6. run compatibility validation;
7. resume eligible work without re-claiming it;
8. record the handover event.

## Non-goals

- running two supervisors as co-heads;
- merging two competing framework sync ownership models;
- silently translating unknown state;
- changing incumbent project tuning during installation.
