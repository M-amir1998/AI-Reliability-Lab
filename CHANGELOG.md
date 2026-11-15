# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow PEP 440.

## [0.1.0.dev0] - unreleased

### Added
- Immutable, strictly validated domain model (test cases, responses, results, runs, gates, incidents).
- Model-agnostic system adapters: mock, scripted, synthetic (good/buggy/unsafe/slow/flaky), HTTP,
  in-process, OpenAI-compatible (incl. InferenceLab), agent / AgentRuntime, RAG; SSRF endpoint guard.
- 22 versioned deterministic evaluators, composite evaluators, optional LLM judge with provenance,
  judge calibration and bias probes, pairwise comparison.
- Bounded async runner: timeouts, transport-only retries, rate limits, deterministic execution IDs,
  checkpoint/resume, cancellation, capture policies, observers.
- Parquet result storage, SQLite run index with append-only baselines, checksums, DuckDB analysis.
- Metrics with Wilson intervals, regression comparison with McNemar and paired bootstrap, typed
  quality gates with category vetoes and a critical-severity override.
- Red-team engine with ten attack families and synthetic canaries; deterministic mutations and
  metamorphic testing.
- Incident-to-eval pipeline (sanitize, classify, convert to DRAFT, review lifecycle).
- Drift analysis, canary and offline shadow comparison, trace import (privacy policies) and
  OTLP-JSON span analysis, failure clustering, human review workflow.
- Markdown / HTML / SVG reporting, optional Prometheus and OpenTelemetry instrumentation.
- `aireliability` CLI and four offline demos; Jenkins pipeline; Docker image and optional compose stack.
