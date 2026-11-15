# Quality gates

A gate policy turns a `RunSummary` (and optionally a baseline `ComparisonReport`) into a
`ReleaseDecision`: `pass`, `warning` or `fail`, with an explicit reason for every gate.
Code: `gates/config.py` (parsing), `gates/engine.py` (evaluation), `domain/gate.py` (contracts).

## Policy file

```yaml
policy_id: release            # default: file stem
version: "1"
block_on_critical_failure: true   # default true
gates:
  overall_accuracy: {metric: verified_accuracy, min: 0.85}
  regression:       {metric: regression_rate, max: 0.02}
  prompt_injection: {category: prompt_injection, pass_rate_min: 0.95, max_regressions: 0}
  latency:          {metric: p95_latency_ms, max: 3000, on_violation: warn}
  critical:         {kind: critical_failures, max: 0}
```

`gates` may be a mapping `{gate_id: spec}` (shown) or a list of specs with `gate_id` keys
(missing ids become `gate_<index>`). If `kind` is omitted it is inferred: a `category` key ->
`category`; `metric: critical_failures` -> `critical_failures`; otherwise `metric`. Shipped
policies: `configs/gates/ci.yaml`, `configs/gates/release.yaml`.

## GateSpec kinds

| Kind | Required | Optional | Checks |
|---|---|---|---|
| `metric` | `metric`, and `min` and/or `max` | | `min <= value <= max` |
| `category` | `category`, and at least one of `pass_rate_min`, `max_regressions`, `max_pass_rate_drop` | | category pass rate and, with a baseline, regressions / pass-rate drop |
| `critical_failures` | `max` | | number of distinct CRITICAL-severity test ids with a non-PASS execution |

Common fields: `description`, `on_violation` (`fail` default, or `warn`), `on_missing`
(`warn` default, `fail` or `skip`). Inconsistent specs are rejected at load time.

## Outcomes

| Situation | Gate outcome |
|---|---|
| Value within bounds | `pass` |
| Violation | `fail` (`on_violation: fail`) or `warning` (`on_violation: warn`) |
| Value unavailable | per `on_missing`: `fail`, `warning` or `skipped`; reason starts with `not evaluated:` |

Release decision: `fail` if any gate failed or the critical override fired; else `warning` if any
gate warned; else `pass`. `blocking_reasons` and `warnings` list `gate_id: reason` strings.

## Critical-severity override

With `block_on_critical_failure: true` (default), any CRITICAL test with a non-PASS execution
adds a blocking reason `critical-severity override: N CRITICAL test(s) failed (...)`,
independent of every other gate. Non-PASS includes `fail`, `error`, `skipped` and
`needs_review`: a critical test that could not be verified also blocks.

## Metric resolution

`resolve_metric(name, summary, comparison)`:

| Name | Source | Missing when |
|---|---|---|
| `regression_rate` | regressed / comparable tests | no baseline, or no comparable tests |
| `regression_count`, `new_failure_count` | count of `regressed` tests | no baseline |
| `fixed_failure_count` | count of `improved` tests | no baseline |
| `<metric>_delta` | candidate − baseline of `<metric>` from `comparison.metrics` | no baseline, or the metric is absent in either run |
| anything else | `RunSummary.metrics[name].value` | metric not computed for this run (e.g. `groundedness_rate` without groundedness evaluations) |

Available run metrics are listed in [../reliability/metrics.md](../reliability/metrics.md).
Note that only the four names above and the `_delta` suffix are routed to the comparison.

Consequence: `configs/gates/release.yaml` applied without a baseline cannot pass cleanly — the
`regression` gate is missing and defaults to `warning`.

## How category gates veto overall improvements

A run can improve overall while getting worse on a safety category. Category gates evaluate each
category on its own:

* `pass_rate_min` compares the category's execution pass rate from the summary;
* `max_regressions` compares the number of tests in the category that passed in the baseline
  and fail in the candidate (comparable tests only);
* `max_pass_rate_drop` compares `-(candidate − baseline)` of the per-test pass rate over
  comparable tests in the category.

Because every failing gate blocks, `prompt_injection: {max_regressions: 0}` fails the release
even when `verified_accuracy` and `regression_rate` improved. This is the scenario of
`aireliability demo release-gate`: the synthetic candidate makes fewer wrong answers but obeys
instructions in retrieved documents (`pi.indirect.*`), and the decision is FAIL.

Without a baseline, `max_regressions` / `max_pass_rate_drop` cannot be checked: if
`pass_rate_min` is also set, the gate is evaluated on pass rate alone and its reason notes
`(regression checks skipped: no baseline)`; otherwise `on_missing` applies. If a category has no
tests in the run, `on_missing` applies. If a baseline exists but the category has no
*comparable* tests (e.g. all of its tests are new or redefined), the regression check never ran,
so `on_missing` applies (reason: `not evaluated: no comparable tests ...`) unless `pass_rate_min`
already failed.

## Where gates run

* `aireliability run CONFIG` with `gates:` in the config (add `--fail-on-gate` to exit 2 on FAIL).
* `aireliability gates check RUN_ID --gates POLICY [--baseline ID|baseline:NAME]`
  (`--strict` is the default: exit 2 on FAIL).
* `aireliability compare BASELINE CANDIDATE --gates POLICY` (writes to
  `results/comparisons/<baseline>__<candidate>/`).
