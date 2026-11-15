# Architecture overview

AI Reliability Lab is a CPU-only Python package (`ai_reliability`, CLI `aireliability`) that
executes test suites against a *system under test*, evaluates every response with versioned
evaluators, stores the run immutably, compares it with a baseline and turns the result into an
explicit release decision. The default CI path uses deterministic mock systems only; no model,
GPU or network is required.

## Layers

```
            +-----------------------------------------------------------+
  CLI       | cli/main.py  (Typer; thin: parse args, call a service)    |
            +-----------------------------------------------------------+
  Services  | services/evaluation.py  services/demos.py                 |
            | services/workspace.py   services/doctor.py                |
            +-----------------------------------------------------------+
  Domain    | domain/*  (frozen pydantic contracts)  metrics/  regression/
  logic     | gates/  stats.py  drift/  redteam/  incidents/  comparison/
            +-----------------------------------------------------------+
  Ports     | SystemUnderTest  Evaluator  JudgeClient  RunObserver      |
            | Clock  ResponseMapper                                     |
            +-----------------------------------------------------------+
  Adapters  | systems/{mock,http,openai_compatible,agent,rag}           |
            | storage/{run_store,parquet,registry_db,analysis}          |
            | observability/metrics.py  evaluators/judge/client.py      |
            +-----------------------------------------------------------+
```

* The CLI never contains evaluation logic. `aireliability run CONFIG` loads a `RunConfig` and
  calls `services.evaluation.run_evaluation`.
* `Workspace` (one per CLI invocation) resolves `configs/prompts`, `configs/systems`,
  `configs/suites`, `incidents/tests` and `results/` relative to `--root`.
* Domain objects inherit from `DomainModel` (`domain/base.py`): `frozen=True`,
  `extra="forbid"`, `validate_default=True`, plus `canonical_json()` / `content_hash()` for
  deterministic hashing.
* Ports are `typing.Protocol`s. Adapters implement them; the domain never imports httpx,
  DuckDB or SQLite.

## Package map

| Package / module | Responsibility |
|---|---|
| `domain/` | Contracts: `TestCase`, `TestSuite`, `ExpectedBehavior`, `SystemResponse`, `CaseResult`, `RunManifest`, `RunSummary`, `GateSpec`, `ReleaseDecision`, `ComparisonReport`, `Incident`; enums in `enums.py` |
| `config/` | Typed YAML models (`SystemConfig`, `RunConfig`, `RunnerSettings`, `DriftConfig`, `RedTeamConfig`); `yaml.safe_load` only |
| `systems/` | `SystemUnderTest` port, mock/scripted/synthetic systems, HTTP, OpenAI-compatible, agent and RAG adapters, endpoint safety, prompt registry |
| `evaluators/` | `Evaluator` port, registry, deterministic, safety, RAG, agent, composite and optional LLM-judge evaluators |
| `runner/` | `EvaluationRunner`: concurrency, timeouts, retries, rate limiting, checkpoint journal, observers |
| `metrics/aggregate.py` | `summarize()`: run metrics, category summaries, failure taxonomy counts |
| `regression/compare.py` | Baseline-vs-candidate per-test classification and statistics |
| `stats.py` | Wilson interval, exact McNemar, paired bootstrap, percentile, Cohen's kappa |
| `gates/` | Gate policy parsing (`config.py`) and evaluation (`engine.py`) |
| `storage/` | Run directory (`run_store.py`), Parquet I/O, SQLite run index and baselines, DuckDB analysis |
| `testsuites/` | Built-in suites, composed suites, YAML suites, incident-derived suite |
| `redteam/` | Attack family catalogue, red-team engine, mutation and metamorphic testing |
| `incidents/` | Sanitize, classify, convert and review incident-derived tests |
| `privacy/redaction.py` | Pattern-based redaction and `TracePrivacy` application |
| `drift/` | Evaluation-output drift (PSI, KS, relative shift) |
| `traces/` | JSONL trace import with privacy policy; OTLP-JSON span analysis |
| `comparison/` | Canary, offline shadow replay, pairwise A/B |
| `human_review/` | Review queue, labels, blinded pairwise review |
| `reporting/` | Markdown/HTML reports and SVG plots |
| `analysis/failures.py` | Failure clustering by category, failure type, evaluator and reason signature |
| `observability/` | Optional Prometheus / OpenTelemetry observers of the runner itself |
| `clock.py`, `determinism.py`, `errors.py` | Injectable clocks, hash-derived randomness, error hierarchy |

## Data flow: suite to release decision

`services.evaluation.run_evaluation()` performs these steps in order:

```
RunConfig (YAML)
  |  select_suite(): resolve suite, apply filters (default statuses = [ACTIVE])
  v
TestSuite --(mock/synthetic only)--> KnowledgeBase.from_tests(suite)  (oracle answers)
  |  SystemRegistry.build()      -> SystemUnderTest adapter
  |  EvaluatorRegistry.validate_suite()  -> fail fast on missing expectations
  v
RunManifest + RunStore.create()  (manifest.json status=RUNNING, config.yaml)
RunIndex.upsert_run(RUNNING)     (results/registry.sqlite)
  |
  v
EvaluationRunner.run()  --> CaseResult per (test, repetition), journaled to checkpoint.jsonl
  |  interrupted? -> manifest status INTERRUPTED; resume later with `aireliability resume`
  v
summarize()          -> RunSummary (metrics, categories, failure counts, critical failures)
compare_runs()       -> ComparisonReport          (only if `baseline:` is set)
evaluate_gates()     -> ReleaseDecision           (only if `gates:` is set)
render_plots/report  -> report.md, report.html, release-report.md, plots/*.svg
RunStore.finalize()  -> parquet + json artifacts, manifest COMPLETED, checksums.json
RunIndex.upsert_run(COMPLETED, pass_rate, decision)
```

A completed run directory is never rewritten (`ImmutableRunError`). See
[results.md](results.md) for the layout and [gates.md](gates.md) for the decision logic.

## Cross-cutting concerns

| Concern | Mechanism |
|---|---|
| Determinism | `determinism.stable_unit()` derives pseudo-randomness from SHA-256 of its arguments; no global RNG. Mock behaviour, retry jitter, red-team markers and blinding all use it. `FakeClock` makes timing testable without sleeping. |
| Reproducibility | `RunManifest` records tool version, git SHA, system descriptor (incl. prompt hash), suite id/version, `test_ids_hash`, evaluator versions, config hash, seed, repetitions and capture policy. |
| Test identity | `TestCase.definition_hash()` hashes `input`, `expected` and `evaluators` only; lifecycle metadata is excluded so promoting a test does not break comparability. |
| Error model | `errors.py` defines one exception type per distinguishable failure (`UnsafeEndpoint`, `ImmutableRunError`, `ResultCorruption`, `InvalidStatusTransition`, ...). Adapters return `SystemResponse.error` instead of raising for transport problems. |
| Privacy | Prompt/input text is never written to results (only `input_sha256`). Response text is kept according to `CapturePolicy` and passed through `Redactor`. See [../../SECURITY.md](../../SECURITY.md). |
| Secrets | Configs reference credentials only by environment-variable *name* (`api_key_env`). |
| Untrusted output | Model output is parsed as data (`json.loads`, JSON pointers, regex). Nothing is executed; ruff bans `pickle`. |
| Observability | `RunObserver` hooks; optional Prometheus/OTel observers instrument the evaluation infrastructure, not the model. |

## Related documents

- [system-adapters.md](system-adapters.md), [evaluators.md](evaluators.md), [runner.md](runner.md),
  [results.md](results.md), [gates.md](gates.md)
- [../reliability/metrics.md](../reliability/metrics.md)
- [../adr/README.md](../adr/README.md)
