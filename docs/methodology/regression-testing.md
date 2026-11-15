# Regression testing

A release question is rarely "is the score high?" but "did anything that used to work stop
working?". The lab answers it with a test-by-test comparison of a candidate run against a
declared baseline run (`regression/compare.py`).

## Workflow

```bash
# 1. Run the current release and declare it as the baseline (append-only)
aireliability run configs/runs/<baseline>.yaml
aireliability baseline set <BASELINE_RUN_ID> --name release

# 2. Run the candidate against it (baseline + gates in the run config)
#    baseline: baseline:release
#    gates: configs/gates/release.yaml
aireliability run configs/runs/<candidate>.yaml --fail-on-gate

# or compare two existing runs after the fact
aireliability compare <BASELINE_RUN_ID> <CANDIDATE_RUN_ID> --gates configs/gates/release.yaml
```

Both runs must use the same suite version for the comparison to be meaningful; tests present
in only one run are reported but not compared.

## Per-test classification

For each test id the executions of all repetitions are grouped. A test *passes* in a run when its
pass fraction is `>= pass_threshold`. `run_evaluation`, `compare` and `gates check` use the
default `pass_threshold = 1.0`: every repetition must pass, so a flaky test counts as failing.

| Baseline | Candidate | Status |
|---|---|---|
| pass | pass | `unchanged_pass` |
| fail | fail | `unchanged_fail` |
| pass | fail | `regressed` |
| fail | pass | `improved` |
| missing in either run | | `not_comparable` ("missing in baseline/candidate") |
| `test_hash` differs | | `not_comparable` ("test definition changed between runs") |

`test_hash` is `TestCase.definition_hash()` over input, expected behaviour and evaluator list.
Editing a test therefore makes it incomparable instead of producing a misleading regression;
changing only lifecycle metadata (status, reviewer) does not. System errors count as failures:
`ERROR`, `SKIPPED` and `NEEDS_REVIEW` executions are all "not PASS".

`regressed` cases carry the first non-PASS candidate `failure_reason`.

## Aggregates in the comparison report

`ComparisonReport` (`comparison.json`, `regressions.parquet`):

* `counts` per status, `regression_rate = regressed / comparable`;
  `new_failures` (= regressed), `fixed_failures` (= improved), `unchanged_failures`;
* `categories[c]`: `n`, baseline and candidate per-test pass rate over comparable tests,
  `delta`, `regressed`, `improved`;
* `metrics[m]`: baseline, candidate, `delta` and `relative` change for every run metric present
  in either summary (exposed to gates as `<m>_delta`);
* `statistics`: an exact McNemar test on (regressed, improved) and a paired bootstrap 95% CI of
  the mean per-test pass-fraction difference. See [statistics.md](statistics.md).

## Why test-level, not score-level

Two runs with identical pass rates can differ in which tests pass. Aggregate scores hide this;
the per-test view shows exactly which behaviours broke, and category gates can veto a release
that regresses on safety while improving elsewhere ([gates](../architecture/gates.md),
[ADR-010](../adr/ADR-010-regression-metrics-over-single-score.md)).

## Practical guidance

* **Stochastic systems.** Real endpoints are not assumed deterministic; the example experiment
  configs use `repetitions: 3`. With the 1.0 threshold any failing repetition marks the test as
  failing, which is conservative. `consistency_rate` and per-test pass fractions show
  flakiness. See [statistics.md](statistics.md#repetitions-and-stochastic-models).
* **Baseline hygiene.** Declare baselines only from completed runs. History is append-only;
  re-declaring an earlier run under the same name appends a new declaration and makes it
  current again (useful for rolling back a baseline).
* **Suite evolution.** New tests show up as `not_comparable` (missing in baseline) until a new
  baseline including them is declared. They are still covered by absolute gates such as
  `pass_rate_min` and the critical-severity override.
* **Incident tests.** Incident-derived tests live in the `incident_regressions` suite (ACTIVE
  only by default); include that suite in the run if it should be compared and gated. See
  [../incidents/regression-lifecycle.md](../incidents/regression-lifecycle.md).
* **Robustness.** The `robustness` suite adds deterministic answer-preserving mutations of
  `core_quality` tests (whitespace, capitalization, question formatting, irrelevant context,
  instruction reordering, unicode, paraphrase). `redteam/mutations/metamorphic.py` checks
  outcome invariance between a passing source test and its follow-up.

## Real-model results

Real-model comparisons (`configs/runs/experiment-*.yaml`) are **Pending**. All numbers produced
by the demos and CI come from deterministic mock systems and say nothing about any real model.
