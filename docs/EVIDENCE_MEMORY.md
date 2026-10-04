# Evidence-backed memory kernel

`EvidenceMemory` is a provider-neutral local SQLite store for cited project or
stack facts. It does not edit framework, constitution, instruction, or project
source files. Every fact requires one or more unique evidence references, and
the statement and citations are rejected if the secret scanner finds a
credential-like value.

```python
from codexdevteam_kernel import EvidenceMemory

memory = EvidenceMemory(".codexdevteam/project/atlas.sqlite")
memory.add_fact(
    fact_id="fact-ports-boundary",
    kind="hot_file",
    scope="project",
    subject="src/ports.py",
    statement="Check import boundaries when changing the shared ports module.",
    evidence=("review:REVIEW-14", "commit:<full-sha>"),
    origin_project="my-project",
)
```

Task retrieval matches scoped paths or explicit subjects, ranks active facts by
confidence multiplied by a 90-day recency half-life, and applies count and
estimated-token limits. Token estimates are a stable UTF-8 byte heuristic, not
a provider tokenizer. Every selection is written to an idempotent injection
event before the caller uses it.

Review outcomes are attached to one injection event. An approval scores all
facts from that injection as wins; rework scores losses only for explicitly
matching injected facts. Confidence is a Beta posterior seeded at 0.6. After
five injections, confidence below 0.35 demotes a fact to probation; probation
facts are not retrieved. They retire after 30 days. Pending outcomes from
already-recorded injections can still update a probation fact and restore it
when confidence reaches 0.6.

The supervisor can attach a `FactInjection` returned by retrieval to the
matching maker task prompt. The rendered block includes citations and marks
facts as advisory; mismatched task IDs, missing citations, and secret-like
content fail closed. Deterministic evidence miners and automatic
review-outcome scoring are implemented. Shared-fact export and migration or
rendering of legacy `INSTINCTS.md` remain open. Callers must provide citations
from verified mechanical or review records. The general fact API checks that
citations are present; each miner additionally validates the evidence source
appropriate to its fact type.

`mine_passing_test_facts()` reads only
HEAD-registered test-run receipts whose typed state rows, journal events,
artifact hashes, passing status, and output-log hashes still agree. It emits a
cited `test_command` fact per registered run. `retrieve_for_task_record()` also
matches a test fact's name when it appears in the task's test evidence.

`mine_hot_file_facts()` emits project-scoped guidance only after repeated
HEAD-recorded territory denials for the same exact path (or identical exact
glob), within a configurable time window. The state store exposes these only
when its typed conflict row agrees with the journal event. Broad overlapping
globs that do not identify a single subject are retained as conflict events
but are not mined into path-specific memory. Each mined fact cites the
conflict events that met the threshold.

`mine_review_catch_facts()` uses only HEAD review events that still match the
registered gate artifact and successful checker invocation receipt. It mines
repeated `file:<path>` evidence references from changes-requested reviews only
when each path is inside that task's owned territory. The miner ignores
approved reviews, stale events, malformed references, and out-of-territory
paths. It does not infer file paths from free-text rationale.

`record_gate_attempt()` stores only the check-status map, failure-name lists,
and a hash of the completed artifact in the HEAD journal. It omits check output
and command text. `verified_gate_attempt_events()` rechecks the event, typed
receipt, and artifact hash. `mine_gate_history_facts()` emits a cited
`spec_gotcha` only after distinct task/SHA/fingerprint attempts repeatedly
report the same new mechanical failure; replays and inherited failures do not
count.

When memory is configured for `Supervisor.apply_checker_output()`, the maker
invocation records its injection event and the HEAD review event binds that
injection to the review. Approval scores all injected facts as wins. A
non-approval scores losses only for injected facts explicitly cited as
`memory-fact:<fact-id>` in the normalized verdict's `evidence_refs`; unknown or
out-of-injection fact IDs are rejected. Replaying the same review is idempotent
in both ledgers.
