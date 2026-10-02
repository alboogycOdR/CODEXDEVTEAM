# Compatibility version matrix

Compatibility is evaluated as a capability boundary, not inferred from shared
filenames. The pinned DEVDEPARTMENT reference is Wave E at
`2d4202c9c70300f3f5cdf97a0b866c1a15b4c760`. At that checkout, the framework
manifest is version 1 with role `pack`, the PLAN records `plan_version: 6.31`,
and `autopilot.json` has no `framework_version`. The PLAN number is a document
revision, not a shared task-protocol version. DEVDEPARTMENT has no CODEXDEVTEAM
task, registry, or CONTROL wire-schema version that establishes state
interoperability.

| Detected system/version | Supported operation | State/protocol interoperability | Activation |
|---|---|---|---|
| CODEXDEVTEAM task protocol 1, registry 1, CONTROL 1 | Native CODEXDEVTEAM kernel operations | Supported within the CODEXDEVTEAM v1 contracts | Separate explicit activation; install does not activate |
| DEVDEPARTMENT Wave E at pinned reference SHA; sync manifest 1 / pack; PLAN 6.31 | Install inactive CODEXDEVTEAM sidecar metadata | Not declared compatible; read-only PLAN projection is experimental and no automatic migration/write-back is provided | Not permitted by the sidecar; explicit handover protocol is still missing |
| Other DEVDEPARTMENT SHA, missing `framework_version`, or unknown internal revisions | Preserve DEVDEPARTMENT and install sidecar files only when paths are free | Unknown; no task-state import or translation | Not permitted |
| Both systems present with the recognized inactive sidecar marker | Recognize DEVDEPARTMENT as incumbent | No shared-writer operation | CODEXDEVTEAM remains inactive |
| Both systems present without the exact recognized inactive-sidecar state | Report conflict; do not overwrite either framework | Unknown/conflicting | Refused |
| Unknown CODEXDEVTEAM task/registry/CONTROL schema version | Reject parsing or application | Unsupported | Refused |

The CODEXDEVTEAM installer ownership manifest governs CODEXDEVTEAM files only.
It does not authorize writes to DEVDEPARTMENT-owned paths. A sidecar install
does not imply that task records, plan status, worktrees, review receipts,
capacity observations, or HEAD leases can be shared. Version compatibility
must be expanded only alongside fixture coverage and a handover protocol.

`assess_compatibility()` now implements the metadata boundary above: native
CODEXDEVTEAM task/registry/CONTROL versions must all match, and the DEVDEPARTMENT
sidecar tier is available only for the exact pinned Wave E revision and its
observed manifest/PLAN metadata. A match still never authorizes activation.
Unknown or changed metadata is refused. The gate classifies metadata only; it
does not import task state, manipulate HEAD leases, or perform handover.

Current executable evidence covers compatibility-version rejection,
inactive-sidecar recognition, conflict detection, and preservation of
DEVDEPARTMENT files during installation. It does not cover live state migration,
lease transfer, or a round-trip handover. See
[HANDOVER_MATRIX.md](HANDOVER_MATRIX.md) for the source/target migration matrix,
the Wave E process-fencing findings, and the operator evidence required before
any future controlled transfer.
