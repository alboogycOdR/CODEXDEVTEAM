# Fast-tier mechanical jobs

`FastTierRunner.classify_run_log()` is a provider-neutral, read-only vertical
slice for optional fast workers. The worker must be active in the registry with
role `fast`; runtime and concrete model remain worker configuration. The input
is rejected if it is empty, longer than 24,000 characters, or matches a
detected secret pattern.

The accepted output is one JSON object with exactly `kind` and `evidence_line`,
plus optional `reset_at`. `kind` is one of `ok`, `capacity`, `quota`, `auth`,
`crash`, or `timeout`. The evidence must be an exact complete line from the
provided run log; optional reset timestamps must be timezone-qualified ISO
8601. Markdown, extra fields, unknown kinds, invented evidence, and malformed
timestamps fail validation.

An invalid result is retried once. A second invalid result emits one
idempotent `tier.escalated` journal event and returns an escalation result.
A valid result emits a `tier.classified` event. Neither outcome mutates task
state or supersedes mechanical verification. Callers decide how a valid
classification affects capacity handling or routes an escalation.

`Supervisor.run_dispatch_cycle()` can optionally run the classifier against
caller-supplied per-worker run logs before dispatch. The fast-tier worker and
runtime adapter must be explicitly configured, and the caller must supply an
existing independent capacity observation for every worker with a log. A
`quota` result can reduce quota to zero; `auth`, `crash`, and `timeout` can mark
the worker unavailable. `ok` and `capacity` never create or increase capacity;
escalated or invalid classifications do not alter observations. Strict role,
capability, freshness, and capacity checks still run after those restrictions.
The integration is opt-in and absent from default supervisor cycles.

The implementation has contract fixtures for retry, exact evidence,
read-only execution, one-time escalation, and restrictive dispatch behavior.
`tests/fixtures/fast_tier_synthetic_corpus.jsonl` covers all six schema
categories with fabricated, redacted examples and is used only to validate the
parser contract. It is not a field corpus and gives no estimate of model
accuracy. A representative redacted field-log corpus and measured accuracy
are still required before configuring routine dispatch.

`codexdevteam-fast-eval --cases <labeled.jsonl> --predictions <results.jsonl>`
scores a labeled corpus without invoking a model or modifying project state.
Each case row contains `case`, `kind`, `log`, and `evidence_line`; each
prediction row contains `case`, `kind`, and `evidence_line`, with null values
for both prediction fields representing an abstention. Either row may include
`reset_at` for quota-window cases; gold timestamps must be timezone-qualified
ISO 8601. Missing predictions are also counted as abstentions. The report
includes coverage, kind and evidence accuracy, reset-time accuracy for cases
with a labeled reset time, exact accuracy across kind/evidence/reset time,
per-kind precision/recall/F1, and a confusion matrix. It refuses duplicate or
unknown case IDs, unsupported labels, malformed gold evidence, overlong logs,
and detected secrets. The evaluator deliberately does not make an enablement
decision: corpus representativeness, label quality, sample size, and acceptable
error thresholds still require review before routine dispatch.
