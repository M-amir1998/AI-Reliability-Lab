# Statistics

`stats.py` provides a small set of methods chosen for the data the lab produces: binary
pass/fail outcomes on a fixed set of test items, often compared pairwise between two runs.
Assumptions are documented per function; numbers below are defaults from the code.

## Wilson score interval

`wilson_interval(successes, n, confidence=0.95)` — used for every rate metric
(`pass_rate`, `verified_accuracy`, `critical_failure_rate`, ...; not `retry_rate`), every
category pass rate and judge accuracy.

```
z = Φ⁻¹(0.5 + confidence/2)
centre = (p + z²/2n) / (1 + z²/n)
half   = z · sqrt(p(1−p)/n + z²/4n²) / (1 + z²/n)
```

Chosen over the normal (Wald) approximation because it keeps sensible coverage at small `n` and
at rates near 0 or 1 — which is the normal case for safety categories with a handful of tests.
Returns (0, 1) for `n = 0`.

**Caveat:** run metrics count executions. With `repetitions > 1` the executions of one test are
not independent, so the interval treats correlated repetitions as independent evidence and
overstates precision. Treat the number of unique tests as the effective sample size.

## Exact McNemar test

`mcnemar_exact(b, c)` — paired comparison of two runs on identical tests. `b` = tests that passed
only in the baseline (regressions), `c` = tests that passed only in the candidate
(improvements). Two-sided exact binomial test of `min(b, c)` out of `b + c` at p = 0.5
(`scipy.stats.binomtest`). Returns 1.0 when there are no discordant pairs.

Why: both runs answer the *same* items, so only discordant pairs carry information about a
difference; an unpaired two-proportion test would ignore the pairing. Assumes independent test
items. Reported in `ComparisonReport.statistics` as `mcnemar_exact`.

## Paired bootstrap

`paired_bootstrap_diff(baseline, candidate, iterations=2000, confidence=0.95, seed=0)` —
percentile CI for `mean(candidate − baseline)` of per-test pass fractions, resampling test items
jointly so the pairing is preserved. Deterministic for a given seed (the run's `seed`). Reported
as `paired_bootstrap_pass_rate_diff`. With repetitions, per-test pass fractions are in [0, 1],
so the bootstrap uses the repetition information that the binary McNemar test discards.

## Cohen's kappa

`cohen_kappa(a, b)` — chance-corrected agreement between two binary raters (judge vs labels,
judge vs human). `None` when expected agreement is 1 (no variance), where kappa is undefined.
Reported alongside accuracy because accuracy is inflated by label imbalance.

## Sign test in pairwise comparisons

`comparison/pairwise.summarize_pairwise` reports wins A, wins B and ties, the win rate of B
excluding ties, a per-category breakdown, and a two-sided sign test (binomial test of B wins out
of decided pairs at p = 0.5). Ties are excluded from the test but always reported; a bare win
rate is never reported alone. Preference sources: deterministic scores, human preferences
(collected on blinded pairs, then mapped back with `human_review.unblind`), or a judge queried in
both orders (order-inconsistent verdicts count as ties).

Deterministic preference per shared test: the higher mean execution score wins, where a PASS
execution scores 1.0 and a non-PASS execution scores 0.99 × the mean of its evaluator scores
(0 if none), so any passing execution outranks any failing one.

## Percentiles

`percentile(values, q)` — linear interpolation (`numpy.percentile`) for p50/p95/p99 latency.
`None` for empty input.

## Why no t-tests

Pass/fail outcomes are Bernoulli, not normally distributed, and the comparisons are paired on
the same items. A t-test on 0/1 outcomes from two runs would assume normality and, in its
unpaired form, ignore the pairing. The code therefore deliberately has no t-test.

## Repetitions and stochastic models

* Real endpoints are not assumed deterministic. The example experiment configs use
  `repetitions: 3` and record `seed` and `temperature`; providers may still be non-deterministic
  at temperature 0.
* `consistency_rate` (tests with identical outcomes across repetitions / unique tests) is
  reported whenever any test has more than one execution.
* Regression classification uses `pass_threshold = 1.0`: a test passes only if every
  repetition passes. This is conservative and turns flakiness into failures; the per-test pass
  fractions in `regressions.parquet` show where.
* Mock systems are deterministic apart from the attempt-dependent transient failures of the
  `flaky` profile — mock statistics validate the code, not a model.
* p-values are reported, not used as gates. Gates use explicit thresholds on interpretable
  metrics; with small suites, a statistically insignificant regression on a critical test still
  blocks a release.
