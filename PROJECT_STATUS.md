# Project status

| Item | Status |
|---|---|
| Repository | https://github.com/M-amir1998/AI-Reliability-Lab (public) |
| Version | 0.1.0.dev0 (no release tag; local quality gates pass) |
| Python | 3.11+ (developed and tested on 3.12, CPU-only) |
| Git authors | M-amir1998 only — all commits with the local identity plus GitHub's initial LICENSE commit; no co-author or tool attribution trailers |

## Verification (fresh clone of the committed tree, Windows 11, CPU-only)

| Check | Result |
|---|---|
| `uv sync --frozen --all-extras`, `uv lock --check` | Pass (67 packages, lock up to date) |
| `ruff format --check src tests` | Pass (149 files) |
| `ruff check src tests` | Pass |
| `mypy` (strict, 126 source files) | Pass |
| `pytest` (all directories) | 289 passed, 0 failed, 0 skipped |
| Coverage (line + branch, `--cov`) | 91% (line + branch) |
| `aireliability run configs/runs/ci-core.yaml --fail-on-gate` | Pass: 60/60 on synthetic/good, release decision PASS |
| Secret scan (`detect-secrets-hook` over `git ls-files`) | Clean (5 audited synthetic-fixture false positives in `.secrets.baseline`) |
| Package build (`uv build`) | Pass (sdist + wheel) |
| Docker build | **Not verified** — Docker CLI present, daemon not running on the development machine |
| Jenkins pipeline | Jenkinsfile written; every stage command executed locally as above. Not yet run on a Jenkins controller. |
| GitHub Actions | None (by design, ADR-007) |

Test breakdown: unit 255, property 9, integration 6, security 7, e2e 8, benchmarks 4 (passed / failed / skipped: 289 / 0 / 0).

## Architecture

CLI → application services → immutable domain → ports (`SystemUnderTest`, `Evaluator`, `JudgeClient`, `RunObserver`, `Clock`) → adapters (HTTP, OpenAI-compatible, agent, RAG, judge, Parquet/SQLite/DuckDB storage, Prometheus/OpenTelemetry). See `docs/architecture/overview.md`.

## Capabilities

| Area | Implemented |
|---|---|
| System adapters | Mock (11 behaviours), scripted, synthetic good/buggy/unsafe/slow/flaky, HTTP, in-process, OpenAI-compatible, InferenceLab preset, agent + AgentRuntime preset, RAG; SSRF guard |
| Evaluators | 22 deterministic + 3 composites; optional LLM judge with provenance; calibration; bias probes; pairwise (deterministic / human / judge with order swap) |
| Test suites | smoke, ci_core, core_quality, structured_output, rag_grounding, agent_tool_safety, prompt_injection, redteam_full, robustness, release_regression, release_full, incident_regressions, YAML suites |
| Regression engine | Per-test 5-way classification, definition-hash comparability, category and metric deltas, exact McNemar, paired bootstrap |
| Quality gates | Metric / category / critical-failure gates, warn vs fail, on_missing policy, critical-severity override |
| Red team | 10 families, 37 attacks, synthetic canary with 6 leak encodings, unique injection markers |
| Prompt injection | Direct, indirect (retrieved document), tool-output, nested; instruction hierarchy |
| RAG evaluation | Retrieval relevance, citations, lexical groundedness, context utilization, typed hallucinations |
| Agent evaluation | Tool-call schema, ALLOW/DENY/REQUIRE_APPROVAL policy, tool-trace hygiene, tool-output injection |
| Robustness | 11 deterministic mutation operators, metamorphic relations |
| Incident-to-eval | Sanitize (redact + residual scan + canary preservation), classify (observed vs suspected cause), DRAFT conversion, DRAFT→REVIEWED→ACTIVE lifecycle, trace linkage |
| Drift | Pass/error rate shift, median/mean shift, PSI, KS, categorical PSI; scope documented |
| Canary / shadow | Same-suite canary comparison over 5 dimensions; offline shadow replay of sanitized traces |
| Traces | JSONL import with HASH_ONLY / METADATA_ONLY (default) / REDACTED_TEXT / FULL_TEXT; OTLP-JSON span analysis |
| Human review | Queue, pseudonymous labels, blinded pairwise with separate key, judge-vs-human agreement |
| Storage | Immutable run directories, Parquet (zstd), checksums, SQLite run index, append-only baselines, resume journal |
| Reporting | report.md, report.html, release-report.md, SVG plots |
| Observability | Optional Prometheus runner metrics, OpenTelemetry spans, stored-summary exporter, compose stack |

## Local demo results (SYNTHETIC / MOCK SYSTEMS — not model results)

| Demo | Result |
|---|---|
| `demo release-gate` | Baseline 95.0% → candidate 97.5% pass rate; correctness +11.1 pp; prompt injection −11.8 pp; **release FAIL** (prompt_injection gate, regression gate, 2 critical failures) |
| `demo incident-to-eval` | Redactions {email: 1, openai_key: 1, phone: 1}; draft → reviewed → active; buggy v1 FAIL, fixed v2 PASS |
| `demo drift` | pass_rate, latency, output_tokens, response_chars and failure_mix flagged DRIFT; category_mix STABLE |
| `demo judge-calibration` | Unbiased scripted judge 100% accuracy; verbosity-biased judge 50% false-pass rate, 50% verbosity flip rate; position-biased judge inconsistent on 20/20 pairs |
| `redteam run configs/redteam/unsafe-synthetic.yaml` | Unsafe mock: 81.1% attack success (30/37); synthetic/good: 0.0% |

## External-model status

**Pending.** No real model, API or deployment has been evaluated. No claims about any model's quality or safety are made.

## Known limitations

- Lexical groundedness and regex refusal detection are heuristics; no NLI evaluator yet.
- Redaction is pattern-based; sanitized incidents still require manual review.
- Red-team catalogue is fixed and small (37 attacks); mock injection compliance is a simulator.
- Drift covers evaluation outputs only; statistical tests assume independent test items.
- AgentRuntime / InferenceLab adapters are contract-level and untested against live services.
- Docker image build not verified locally; Jenkins pipeline not yet executed on a controller.

## Next real-model evaluation

```bash
export OPENAI_API_KEY=...   # or the variable named by api_key_env; never commit it
# 1. set model / revision / pricing in configs/systems/openai-compatible-example.yaml
uv run aireliability run configs/runs/experiment-1-system-a.yaml
uv run aireliability baseline set <RUN_ID_A> --name experiment-1
# 2. point a copy of the config at System B with `baseline: baseline:experiment-1`, then run it
uv run aireliability report <RUN_ID_B>
```
