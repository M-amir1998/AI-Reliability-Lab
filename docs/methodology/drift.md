# Drift analysis

## Scope

`drift/` detects shifts in **evaluation outputs**: what the harness observed when it ran the
evaluated test sets (pass/error rates, latency, output tokens, response length, category and
failure mix) in a current run versus one or more reference runs.

It is **not** production model drift detection. It says nothing about shifts in real user
traffic unless the evaluated inputs are a representative sample of that traffic. Every
`DriftReport` carries this note in `scope_note`.

Typical uses: the same suite run nightly against the same endpoint (silent provider-side model
change, latency regression, longer answers), or comparing evaluation behaviour across releases.
Comparing runs of *different* suites mostly measures the suite difference (`category_mix`).

## Usage

```yaml
# configs/drift/example.yaml
reference_runs: [REPLACE_WITH_RUN_ID_1, REPLACE_WITH_RUN_ID_2]
current_run: REPLACE_WITH_CURRENT_RUN_ID
results_dir: results
thresholds: {psi_warn: 0.1, psi_drift: 0.25, ks_alpha: 0.01,
             relative_shift_warn: 0.1, relative_shift_drift: 0.25}
```

```bash
aireliability drift analyze configs/drift/example.yaml
aireliability report RUN_ID --drift configs/drift/example.yaml   # include in a report
aireliability demo drift                                          # synthetic example
```

Data is loaded from `results.parquet` of all listed runs with one DuckDB scan
(`storage/analysis.load_columns`); reference runs are pooled.

## Methods (`drift/methods.py`)

| Method | Definition | Assumptions / caveats |
|---|---|---|
| Relative shift | `(current − reference) / |reference|`; `None` if reference is 0 | Simple effect size; unstable for tiny references |
| PSI | `Σ (q − p) · ln(q / p)` over 10 bins at reference quantiles (outer edges open), `epsilon = 1e-4` added to every bin share | Sensitive to bin choice and small samples. A constant reference collapses to a two-bin "equal to constant / not" comparison |
| Categorical PSI | Same formula over category frequencies | Union of categories on both sides |
| KS two-sample | `scipy.stats.ks_2samp` statistic and p-value | Independent samples, continuous data; ties from discrete values make it conservative. `None` if either side has < 5 values |

Rule of thumb for PSI from the code: < 0.1 stable, 0.1–0.25 moderate, > 0.25 major shift.

## Features and decision rules (`drift/analyze.py`)

| Feature | Kind | Status rule |
|---|---|---|
| `pass_rate` | rate | absolute change > 0.03 WARN, > 0.08 DRIFT (fixed in code) |
| `error_rate` | rate | absolute change > 0.02 WARN, > 0.05 DRIFT (fixed in code) |
| `latency_ms` | continuous, median | worst of PSI, relative shift and KS rules |
| `output_tokens` | continuous, mean | same |
| `response_chars` | continuous, mean | same |
| `category_mix` | categorical PSI | > `psi_warn` WARN, > `psi_drift` DRIFT |
| `failure_mix` | categorical PSI over failure categories (`none` for passes) | same |

Continuous rules (thresholds from `DriftThresholds`, defaults shown above):

* PSI > `psi_drift` -> DRIFT; > `psi_warn` -> WARN;
* |relative shift| > `relative_shift_drift` -> DRIFT; > `relative_shift_warn` -> WARN;
* KS p-value < `ks_alpha` -> WARN only. KS alone never escalates to DRIFT because with large
  samples it flags tiny, irrelevant shifts.
* Fewer than 5 observations on either side -> `insufficient_data`.

The report status is the worst feature status (`drift` > `warn` > `stable`), or
`insufficient_data` if no feature could be assessed.

## Interpretation notes

* Rows with missing latency or token counts are dropped per feature, so `n` differs by feature.
* Mock systems report synthetic latency and token counts; drift on mock runs validates the
  pipeline, not a model.
* With repetitions, executions of the same test are not independent; KS p-values are then
  optimistic. Prefer the effect-size rules (PSI, relative shift).
* A DRIFT status is a prompt to investigate (compare runs, read failure clusters), not a
  release decision. Gates are configured separately ([gates](../architecture/gates.md)).
