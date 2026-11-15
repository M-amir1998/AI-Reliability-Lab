# ADR-004: Parquet result storage

**Status:** Accepted

## Context

Each run produces one row per execution with typed metadata (outcome, category, latency, tokens,
cost, hashes) plus nested evaluator results. Results must be stored compactly, read back exactly,
queried across many runs (comparisons, drift, trends) and kept on a laptop without running a
database server.

## Decision

* Per-execution results are written to `results.parquet` (and the non-passing subset to
  `failures.parquet`) with an explicit Arrow schema and zstd compression; nested evaluations are
  stored as a JSON string column (`evaluations_json`). Comparisons go to `regressions.parquet`.
* Aggregates and decisions are small JSON files (`summary.json`, `comparison.json`, `gates.json`,
  `manifest.json`).
* A SQLite file (`results/registry.sqlite`) indexes run metadata and append-only baseline
  declarations; it never holds results.
* Cross-run analysis uses DuckDB over the Parquet files (`storage/analysis.py`), behind an
  adapter so DuckDB stays out of the domain.
* All of `results/` is git-ignored.

## Consequences

Positive:

* No database service to install or operate; artifacts are plain files that can be archived,
  copied and inspected with standard tools (DuckDB, pandas, pyarrow).
* Columnar, compressed files keep large runs small and fast to scan.
* Reading validates every row back into `CaseResult`; schema drift surfaces as
  `ResultCorruption`.

Negative / trade-offs:

* Parquet files are written once per run at finalization; live progress is only in the JSONL
  checkpoint journal.
* Evaluation details inside `evaluations_json` are not columnar; querying them needs JSON
  functions.
* SQLite holds baselines for a results directory; losing that file loses baseline history
  (run directories remain).
* Schema changes need care to keep old runs readable.
