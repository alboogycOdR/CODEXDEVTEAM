# Wave O generation measurement: Windows disposable pilot

Recorded 2026-10-08. This is an early CODEXDEVTEAM measurement of the manual
generation lane against the ordinary supervised maker path. It does **not**
satisfy DEVDEPARTMENT's O-C field protocol in
`C:\CLAUDECODE_kingdom.work\DEVDEPARTMENT\updates\2026oct-omlcp-jason\OMLCP.md`
§8: these are disposable Python fixtures, not three representative field tasks
such as the CLI, Flutter, and MQL5 examples in the reference. No production
routing decision should be
inferred from them.

## Setup and measured results

Each pair started from the same committed task/spec/test base in two isolated
fresh CODEXDEVTEAM project clones. The ordinary path used one strict maker and
the generated path staged a draft before the same supervised maker, host commit,
mechanical gate, and independent checker. Both used `gpt-6.1-sol` with the maker
role's `high` reasoning effort and `gpt-6-sol` as checker. The generation lane
now binds the maker role effort into the invocation and receipt so the two paths
have matching model configuration. No DEVDEPARTMENT project was modified.

| Task and base SHA | Path | End-to-end wall seconds | Input tokens | Output tokens | Gate/review |
| --- | --- | ---: | ---: | ---: | --- |
| Strict duration parser, `5e71d02fa50ea87b1c6a69271dad97b43a064bfa` | Ordinary | 159.64 | 197,907 | 2,320 | 1 gate pass; first-pass approval; DONE |
| Same task/base | Generate + maker | 180.22 (25.47 stage + 154.75 host) | 222,655 (21,560 + 201,095) | 2,370 (466 + 1,904) | 1 gate pass; first-pass approval; DONE |
| Two-file CSV toolkit, `eced6a624ed1a50abad93ed59351b5e6333a641c` | Ordinary | 202.00 | 234,885 | 4,974 | 1 gate pass; first-pass approval; DONE |
| Same task/base | Generate + maker | 231.99 (72.92 stage + 159.07 host) | 306,797 (21,963 + 284,834) | 4,593 (1,935 + 2,658) | 1 gate pass; first-pass approval; DONE |

The generated path was 20.58 seconds and 24,748 input tokens higher on the
first task, and 29.99 seconds and 71,912 input tokens higher on the second.
Both paths passed the gate and independent review on the first attempt.
Runtime cost was unmetered, so these token counts are not a monetary comparison.
The fixtures have no two-week post-merge defect observation window.
Host and stage wall time include startup/closeout overhead; the generation report's
model-call duration alone is shorter than stage wall time. The comparison uses
the measured end-to-end command durations.

The third pair was a three-file DAG planner at base
`66086c0ca4202d053bf80d1206e2710ff28d6fc4`. Generation staged three
files in one segment and verified them in 40.75 seconds. Its ordinary maker
run then recorded `launch_failed` with a `runtime_adapter_failure` after about
111 seconds. The supervisor parked with `closeout_incomplete`; the task stayed
`in_progress` and no gate or checker ran. The paired generated host run was not
started. This pair is **excluded**, with no outcome or efficiency claim. The
recorded PID was absent on read-only inspection; that alone does not prove
process-tree quiescence, so the failed worktree was not retried or reused.

The local source records are under
`C:\Users\Nuburo\AppData\Local\Temp\codexdevteam-wave-o-oc-20261008-c0fee412*`:
`*-metrics.json`, `*-host-result.txt`, and `*-stage*-result.txt`. They are
disposable host evidence, not portable release artifacts. The figures above
are copied here for review.

## Decision

Keep `auto_enabled=false` and the lane operator-invoked. The two completed
proxy pairs show equal first-pass quality and no time/input advantage; the
third is inconclusive. Do not implement O-D automatic routing on the strength
of this pilot. If automatic routing is still desired, first run the reference
O-C field protocol on three representative tasks with reproducible task bases,
complete run receipts, cost data, and the same maker/checker configuration.
