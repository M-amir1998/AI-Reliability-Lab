# Reliability metrics

`metrics/aggregate.summarize()` turns the `CaseResult`s of a run into a `RunSummary`
(`summary.json`). Every metric is a plain, documented ratio or statistic stored as a
`MetricResult` with `value`, sample size `n`, optional Wilson 95% CI (`ci_low`, `ci_high`),
`unit` and `description`. A metric whose denominator is empty has `value: null`, not 0.

## No single magic score

There is deliberately no composite "reliability score". A weighted average hides exactly the
trade-offs a release decision must see — e.g. better accuracy bought with worse injection
resistance. Component metrics stay primary, and gates are applied to them individually
([gates](../architecture/gates.md), [ADR-010](../adr/ADR-010-regression-metrics-over-single-score.md)).

Terminology: an *execution* is one (test, repetition); *unique tests* are distinct test ids.
"Non-PASS" means `fail`, `error`, `skipped` or `needs_review`.

## Outcome rates (always present)

| Metric | Definition | CI |
|---|---|---|
| `pass_rate` | PASS executions / all executions | Wilson |
| `verified_accuracy` | PASS / executions with outcome PASS, FAIL or ERROR (excludes SKIPPED and NEEDS_REVIEW) | Wilson |
| `test_pass_rate` | tests passing in every repetition / unique tests | Wilson |
| `critical_failure_rate` | non-PASS CRITICAL executions / CRITICAL executions | Wilson |
| `critical_failure_count` | number of non-PASS CRITICAL executions (`n` = CRITICAL executions) | — |
| `system_error_rate` | ERROR executions that are not timeouts / all executions | Wilson |
| `timeout_rate` | ERROR executions with `error_code = timeout` / all executions | Wilson |
| `needs_review_rate` | NEEDS_REVIEW executions / all executions | Wilson |
| `retry_rate` | executions with `attempts > 1` / all executions | none |

Notes:

* `verified_accuracy` keeps ERROR in the denominator: an infrastructure error lowers it.
* `system_error_rate` counts every ERROR outcome except timeouts — transport errors, adapter
  exceptions **and** evaluator crashes (an evaluator failure makes the execution ERROR).
* `critical_failure_count` counts executions; `RunSummary.critical_failures` and the
  `critical_failures` gate count distinct test ids. They differ when `repetitions > 1`.

## Conditional metrics

| Metric | Present when | Definition |
|---|---|---|
| `consistency_rate` | any test has > 1 execution | tests whose executions all have the same outcome / unique tests (Wilson) |
| `structured_output_validity` | `json_valid` or `json_schema` evaluated | pass rate of those evaluations |
| `injection_resistance_rate` | `injection_resistance` or `canary_leak` evaluated | pass rate of those evaluations |
| `citation_validity` | `citation` evaluated | pass rate of `citation` evaluations |
| `tool_policy_compliance` | `tool_policy` or `tool_trace` evaluated | pass rate of those evaluations |
| `groundedness_rate` | `groundedness` evaluated | pass rate of `groundedness` evaluations |

Evaluator-group metrics count **evaluations**, not executions: a test with both
`injection_resistance` and `canary_leak` contributes two. Evaluations nested inside composites
are included, and SKIPPED evaluations are excluded. All have Wilson CIs.

## Latency, tokens, cost, length

| Metric | Present when | Definition |
|---|---|---|
| `p50_latency_ms`, `p95_latency_ms`, `p99_latency_ms` | always (null without latencies) | linear-interpolated percentiles of `latency_ms` (as reported by the system, else measured by the runner) |
| `mean_latency_ms` | always | mean `latency_ms` |
| `p50_ttft_ms` | any TTFT reported | median time to first token |
| `mean_output_tokens` | any output tokens reported | mean reported output tokens |
| `total_tokens` | any input tokens reported | sum of reported input + output tokens |
| `total_cost_usd`, `mean_cost_usd` | any cost computed | from configured `pricing` (never hard-coded) |
| `mean_response_chars` | always | mean response length in characters |

Token counts are those reported by the system; endpoint adapters never estimate them. Mock
systems fabricate counts from text length, so token and cost metrics of mock runs are synthetic.

## Category summaries

`RunSummary.categories[c]`: `total`, `passed`, `failed`, `errors`, `pass_rate` (execution pass
rate) with Wilson CI. Category gates use these values.

## Failure taxonomy

`RunSummary.failure_categories` counts non-PASS executions by `failure_category`
(`FailureCategory`): `wrong_answer`, `hallucination`, `instruction_failure`, `format_failure`,
`safety_failure`, `tool_failure`, `retrieval_failure`, `citation_failure`, `timeout`,
`system_error`, `performance`. These describe *what* failed, never *why*. The report also groups
failures into clusters by category, failure type, evaluator, error code and a normalized reason
signature (`analysis/failures.py`).

## Comparison metrics (with a baseline)

From `ComparisonReport` ([regression testing](../methodology/regression-testing.md)):
`regression_rate` (regressed / comparable tests), `regression_count` = `new_failure_count`
(regressed tests), `fixed_failure_count` (improved tests), and `<metric>_delta` (candidate −
baseline) for every run metric above.

## Red-team, drift and judge metrics

Not part of `RunSummary`:

* red-team: attack success rate per family and overall, resisted, errors, canary leaks
  ([prompt injection](../methodology/prompt-injection.md));
* drift: per-feature status, PSI, KS, relative shift ([drift](../methodology/drift.md));
* judge: accuracy, precision, recall, F1, false-pass rate, kappa, bias flip rates
  ([judge calibration](../methodology/judge-calibration.md)).

All numbers produced in CI and demos come from mock systems. Real-model metrics: **Pending**.
