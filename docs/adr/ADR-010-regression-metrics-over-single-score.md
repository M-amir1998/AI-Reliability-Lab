# ADR-010: Regression metrics over a single aggregate score

**Status:** Accepted

## Context

A single "reliability score" is easy to report and easy to misread. Averaging correctness,
safety, latency and format validity lets an improvement in one hide a regression in another —
e.g. a candidate that answers more questions correctly but now follows instructions hidden in
retrieved documents. Two runs with identical pass rates can also fail entirely different tests.

## Decision

* `summarize()` produces separate, documented metrics (pass rate, verified accuracy, critical
  failure rate, error and timeout rates, evaluator-group rates, latency percentiles, tokens,
  cost) with sample sizes and Wilson intervals. There is no composite score.
* Composite evaluators report a transparent weighted-mean score, but pass/fail is derived from
  component outcomes, never from the score.
* Releases are judged by test-level comparison against a baseline: each test is `improved`,
  `regressed`, `unchanged_pass`, `unchanged_fail` or `not_comparable`; per-category deltas,
  `regression_rate`, exact McNemar and a paired bootstrap CI are reported.
* Gates apply to individual metrics and categories, so a safety category can veto an overall
  improvement.

## Consequences

Positive:

* Reports name exactly which tests and categories regressed and why.
* Trade-offs are explicit and must be accepted (or rejected) through gate policy, not hidden by
  a weighting.
* Metrics with too little data are `null` rather than misleading.

Negative / trade-offs:

* There is no one-line answer for dashboards; readers must look at several metrics.
* Gate policies must be designed per product; there is no universal threshold.
* Per-test comparison requires stable test definitions; edited tests become `not_comparable`
  until a new baseline is declared.
