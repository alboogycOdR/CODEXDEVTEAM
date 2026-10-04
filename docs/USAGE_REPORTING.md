# Invocation outcome and cost reporting

Every HEAD-recorded invocation event carries its configured role, runtime,
concrete model, purpose, status, duration, and available usage fields. The
Codex adapter reads reported input/output/cached-input token totals from
completed-turn events. Other runtime adapters may supply the same provider-
neutral usage fields or a reported cost. Unknown metrics remain null.

The report command reads the SQLite event journal in read-only mode:

```powershell
codexdevteam usage --state-db .codexdevteam/project/state.sqlite
codexdevteam usage --state-db .codexdevteam/project/state.sqlite --rates .codexdevteam/project/usage-rates.json
```

Rate configuration is explicit and keyed by runtime/model, with an effective
timestamp. Amounts are USD per million tokens. Cached input uses its configured
rate; if omitted, it uses the regular input rate. No vendor pricing is baked
into the package. For example:

```json
[
  {
    "runtime": "codex",
    "model": "configured-model-id",
    "input_usd_per_million": 1.0,
    "output_usd_per_million": 4.0,
    "cached_input_usd_per_million": 0.25,
    "effective_at": 1790812800
  }
]
```

The report groups by role, runtime, model, and invocation purpose. It includes
success/failure counts, duration, known token totals, priced invocation count,
unpriced invocation count, and summed known cost. `cost_usd: null` means no
invocation in that group could be priced; a numeric total with unpriced runs is
partial and is accompanied by the two counts. Reports do not change routing or
supervision policy.

`compare_tier_rent()` compares two configured maker `(runtime, model)` pairs
using first-review approval rates and cost per first-pass approval. It requires
a configured minimum number of reviewed tasks per tier and complete maker cost
metering; otherwise `pays_rent` is `null` with a reason. The thresholds are
caller-supplied and the result is measurement only; it never changes routing.

`measure_pilot(store, rates=...)` combines HEAD-verified gate attempts and
review events with invocation summaries. It reports reviewed tasks, first-pass
approval rate, checker review sessions, changes requested, gate rejection rate,
maker/checker invocation totals, known spend, unmetered calls, and whether
spend is complete. Rates remain explicit; missing usage or pricing keeps the
spend result marked incomplete rather than treating it as zero-cost.
