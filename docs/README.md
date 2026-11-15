# Documentation

Technical documentation for AI Reliability Lab: evaluation, regression gating, red-teaming,
drift analysis and incident-to-eval for LLM, RAG and agent systems. CPU-only; CI uses offline
mock systems on Jenkins. Start with the project [README](../README.md) for installation and a
quick tour.

All numbers in these documents are either defaults from the code or explicitly labelled as
illustrative. Results for real models are **Pending**.

## Architecture

| Document | Contents |
|---|---|
| [architecture/overview.md](architecture/overview.md) | Layers, package map, data flow from suite to release decision, cross-cutting concerns |
| [architecture/system-adapters.md](architecture/system-adapters.md) | `SystemUnderTest`, mock/scripted/synthetic systems, HTTP/OpenAI-compatible/agent/RAG adapters, canonical contracts, SSRF policy |
| [architecture/evaluators.md](architecture/evaluators.md) | Evaluator contract, every built-in evaluator, composites, versioning, optional LLM judge |
| [architecture/runner.md](architecture/runner.md) | Concurrency, timeouts, retries, rate limiting, execution ids, checkpoint/resume, capture policy, observers |
| [architecture/results.md](architecture/results.md) | Run directory layout, Parquet schema, SQLite index and baselines, checksums, DuckDB |
| [architecture/gates.md](architecture/gates.md) | Gate kinds, YAML format, outcomes, critical override, metric resolution |

## Methodology

| Document | Contents |
|---|---|
| [methodology/regression-testing.md](methodology/regression-testing.md) | Baseline vs candidate, per-test classification, workflow |
| [methodology/statistics.md](methodology/statistics.md) | Wilson, exact McNemar, paired bootstrap, kappa, sign test, repetitions |
| [methodology/prompt-injection.md](methodology/prompt-injection.md) | Attack families, canaries, instruction hierarchy, mock compliance |
| [methodology/rag-evaluation.md](methodology/rag-evaluation.md) | Retrieval relevance, citations, lexical groundedness, hallucination types |
| [methodology/judge-calibration.md](methodology/judge-calibration.md) | Calibration metrics, bias probes, scripted judges |
| [methodology/drift.md](methodology/drift.md) | Evaluation-output drift methods and thresholds |

## Reliability metrics

| Document | Contents |
|---|---|
| [reliability/metrics.md](reliability/metrics.md) | Every run metric and its definition; why there is no single score |

## Incidents

| Document | Contents |
|---|---|
| [incidents/incident-to-eval.md](incidents/incident-to-eval.md) | Pipeline, incident format, classification, conversion, CLI |
| [incidents/sanitization.md](incidents/sanitization.md) | Redaction rules, canary preservation, residual scan, manual review |
| [incidents/regression-lifecycle.md](incidents/regression-lifecycle.md) | DRAFT -> REVIEWED -> ACTIVE -> RETIRED, `incident_regressions` |

## Security and CI

| Document | Contents |
|---|---|
| [../SECURITY.md](../SECURITY.md) | Security model, controls and limits, vulnerability reporting |
| [security/threat-model.md](security/threat-model.md) | Assets, trust boundaries, threats and residual risks |
| [ci/jenkins.md](ci/jenkins.md) | Jenkins + uv setup, pipeline stages, external evaluation, artifacts |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Development setup, checks, extension rules, commit hygiene |

## Decisions

[adr/README.md](adr/README.md) — ADR-001 to ADR-010.

## CLI quick reference

| Command | Purpose |
|---|---|
| `aireliability doctor` | Environment diagnostics |
| `aireliability systems list` / `inspect ID` | Registered systems |
| `aireliability suites list` / `inspect ID [--tests]` | Registered suites |
| `aireliability run CONFIG [--fail-on-gate] [--metrics-port N]` | Execute a run |
| `aireliability resume RUN_ID` | Resume an interrupted run |
| `aireliability compare BASELINE CANDIDATE [--gates POLICY]` | Compare two stored runs |
| `aireliability gates check RUN_ID --gates POLICY [--baseline REF]` | Apply a gate policy |
| `aireliability baseline set RUN_ID --name NAME` / `show --name NAME` | Append-only baselines |
| `aireliability runs list` / `verify RUN_ID` | Run index, checksum verification |
| `aireliability report RUN_ID [--drift CONFIG]` | Regenerate reports into `reports/` |
| `aireliability redteam run CONFIG` | Red-team campaign |
| `aireliability incidents add/sanitize/convert/review/activate/list` | Incident-to-eval |
| `aireliability drift analyze CONFIG` | Evaluation-output drift |
| `aireliability review queue/pending/label` | Human review |
| `aireliability traces import PATH OUTPUT [--privacy P]` / `analyze-otel PATH` | Traces |
| `aireliability canary run BASELINE CANDIDATE [--suite S]` / `canary shadow TRACES BASELINE CANDIDATE` | Canary and offline shadow comparison |
| `aireliability metrics export` / `serve` | Prometheus exposition of stored summaries |
| `aireliability demo release-gate/incident-to-eval/drift/judge-calibration` | Offline demos (mock systems) |
