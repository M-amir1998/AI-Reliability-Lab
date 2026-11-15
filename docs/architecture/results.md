# Result storage

Results are files, not a database service. Each run is a directory of Parquet and JSON
artifacts; a small SQLite file indexes runs and baselines; DuckDB is used for ad-hoc analysis
over the Parquet files. All of it lives under `results/` (git-ignored, see
[ADR-004](../adr/ADR-004-parquet-result-storage.md)).

## Run directory layout

Written by `storage/run_store.py` (`RunStore`) and `services/evaluation.py`:

```
results/
├── registry.sqlite              # run index + baseline declarations
└── <run-id>/
    ├── manifest.json            # RunManifest; status RUNNING -> (INTERRUPTED) -> COMPLETED
    ├── config.yaml              # exact RunConfig, including the assigned run_id
    ├── checkpoint.jsonl         # resume journal; deleted on finalization
    ├── results.parquet          # one row per execution
    ├── failures.parquet         # non-PASS executions only (same schema)
    ├── summary.json             # RunSummary: metrics, categories, failure counts
    ├── regressions.parquet      # per-test comparison (only with a baseline)
    ├── comparison.json          # ComparisonReport (only with a baseline)
    ├── gates.json               # ReleaseDecision (only with a gate policy)
    ├── report.md / report.html  # full report
    ├── release-report.md        # short release report (only with a gate policy)
    ├── plots/*.svg              # SVG plots
    └── checksums.json           # sha256 of every artifact above
```

Run ids default to `<UTC timestamp>-<system slug>-<config hash[:6]>`; a fixed `run_id` may be
set in the config. Ids containing `/`, `\` or a leading `.` are rejected.

Other locations under the results directory: `comparisons/<baseline>__<candidate>/`
(`aireliability compare`), `reviews/` (human review queue and labels), `redteam/<run>.json`
(`aireliability redteam run`). Regenerated reports go to `reports/<run-id>/`.

## Parquet schema (`results.parquet`, `failures.parquet`)

zstd-compressed, defined in `storage/parquet.py` (`RESULT_SCHEMA`):

| Column | Type | Notes |
|---|---|---|
| `execution_id` | string | deterministic, see [runner.md](runner.md) |
| `run_id`, `test_id`, `test_hash` | string | `test_hash` = `definition_hash()` |
| `category`, `severity` | string | |
| `tags` | list<string> | sorted |
| `repetition` | int32 | 0-based |
| `outcome` | string | `pass`, `fail`, `error`, `skipped`, `needs_review` |
| `failure_category`, `failure_reason`, `error_code` | string | null when passing |
| `latency_ms`, `ttft_ms` | float64 | as reported or measured |
| `input_tokens`, `output_tokens` | int64 | as reported by the system |
| `cost_usd` | float64 | only with configured pricing |
| `response_chars`, `tool_call_count`, `attempts` | int64/int32 | |
| `input_sha256`, `response_sha256` | string | 32 hex chars |
| `response_text` | string | per capture policy, redacted, <= 4000 chars |
| `evaluations_json` | string | JSON array of `EvaluationResult` (incl. components) |

`regressions.parquet` columns: `test_id`, `category`, `severity`, `status`,
`baseline_pass_rate`, `candidate_pass_rate`, `baseline_outcome`, `candidate_outcome`, `reason`.

Reading a row that fails `CaseResult` validation raises `ResultCorruption`.

## Immutability and checksums

* `RunStore.create` refuses a run id whose manifest is already `COMPLETED`
  (`ImmutableRunError`); an existing non-completed run directory is reused for resume.
* `RunStore.finalize` writes all artifacts, then the `COMPLETED` manifest, then
  `checksums.json` (every file except the checksum file and the journal), then deletes the
  journal. Finalizing a completed run raises `ImmutableRunError`.
* `aireliability runs verify RUN_ID` recomputes every listed checksum and raises
  `ResultCorruption` on a missing or modified file.

Checksums detect accidental modification and incomplete copies. They are stored next to the
artifacts, so they do not protect against deliberate tampering, and files added after
finalization are not detected. Immutability is enforced by the code paths above, not by file
permissions.

## SQLite run index and baselines

`storage/registry_db.py` (`RunIndex`, file `results/registry.sqlite`) holds metadata only:

* `runs(run_id, created_at, completed_at, status, system_id, suite_id, suite_version,
  config_hash, git_sha, pass_rate, decision)`. Upserting a run that is already `completed`
  raises `ImmutableRunError`.
* `baselines(id, name, run_id, declared_at, note, UNIQUE(name, run_id))`. Declarations are
  appended, never updated. Only completed runs can be declared. The current baseline for a name
  is the most recently inserted row.

```bash
aireliability baseline set RUN_ID --name release --note "v1.4 GA"
aireliability baseline show --name release
# in a RunConfig or on the CLI:  baseline: baseline:release
```

Every declaration is a new row, so re-declaring an earlier run under the same name makes it
current again while the full declaration history is kept.

## DuckDB analysis

`storage/analysis.py` runs SQL directly over the Parquet files of one or more runs
(`read_parquet([...])`), without importing them:

* `load_columns(results_dir, run_ids)` — latency, output tokens, response length, pass/error
  flags, category and failure category per execution (input for [drift](../methodology/drift.md));
* `category_pass_rates(results_dir, run_ids)` — `(run_id, category, pass_rate, n)`.

Ad-hoc queries work the same way, e.g.:

```sql
SELECT category, failure_category, count(*)
FROM read_parquet('results/*/failures.parquet')
GROUP BY ALL ORDER BY 3 DESC;
```

## What is not stored

Prompt and input text, system prompts, documents and tool outputs are never written to result
files; only hashes. Response text is stored only as allowed by the capture policy — note that a
response can itself echo its input (e.g. a leaked system prompt), and redaction is best-effort.
Credentials are never stored: descriptors record `api_key_env` (a variable name), not values.
