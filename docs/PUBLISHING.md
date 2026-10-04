# Publishing policy

`decide_publication()` reduces explicit policy and its persisted batch-window
timestamp into a decision. It is a pure function: it does not inspect Git,
create commits, contact remotes, or publish refs. A later publisher adapter must
perform those effects only after receiving `publish: true` and must independently
enforce its repository/ref and commit-content rules.

Publication requires an explicit `git.enabled: true` and an explicit
`git.push_policy`. Installation, HEAD activation,
successful task completion, and a merge/park event do not enable it. The schedule
policy is independently configured as `every`, `batch`, or `merge_only`:

- `every` allows each explicit event when publishing is enabled.
- `batch` defers bookkeeping events until `push_batch_minutes` has elapsed since
  the stored `window_start`; merge and park boundaries can publish immediately.
- `merge_only` defers bookkeeping events and allows merge and park boundaries.

For batch scheduling, the caller persists the returned
`next_window_start` before/after effects according to its retry protocol. A
backward clock observation resets the window instead of triggering an early
publish. Invalid policy, event, timestamps, or configuration fail closed.

This reducer does not implement coordination-plan commit/CAS behavior, remote
selection, ahead/behind detection, quiet-period locking, retry/outbox semantics,
or cross-platform push process management. Those belong in a later adapter with
focused temporary-repository tests. The current implementation does not invoke
that adapter or push CODEXDEVTEAM changes.
