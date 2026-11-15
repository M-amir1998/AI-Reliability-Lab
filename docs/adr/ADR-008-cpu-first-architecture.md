# ADR-008: CPU-first architecture

**Status:** Accepted

## Context

The lab must run on a laptop without a GPU, in CI, and in a slim container. Evaluating a model
does not require running the model locally: the system under test can be a remote endpoint, a
local inference server or an in-process pipeline. Heavy ML dependencies (CUDA, deep-learning
frameworks, embedding models) would make installation slow and fragile.

## Decision

* No GPU, CUDA or deep-learning framework dependency. Runtime dependencies are pydantic, PyYAML,
  Typer, Rich, httpx, jsonschema, pyarrow, DuckDB, NumPy, SciPy and psutil; Prometheus and
  OpenTelemetry are an optional extra.
* All built-in evaluators are deterministic and CPU-cheap (string, regex, JSON, lexical
  overlap); failure clustering uses normalized reason signatures, not embeddings.
* Every feature can be exercised with deterministic mock, scripted or synthetic systems, so the
  full pipeline runs offline.
* Systems under test are reached over HTTP or in-process callables; inference happens wherever
  the system runs.
* `aireliability doctor` treats a missing GPU as expected and reports CPU, RAM and disk.
* The Docker image is a non-root `python:3.12-slim` with no GPU runtime.

## Consequences

Positive:

* Fast installation with `uv`, reproducible offline CI, small container image.
* The harness overhead itself can be benchmarked on CPU (`tests/benchmarks`).

Negative / trade-offs:

* Semantic checks that usually rely on models (entailment-based groundedness, embedding
  similarity, semantic clustering) are not built in; the lexical substitutes have documented
  limits.
* Running large models locally is out of scope; doing so requires an external inference server
  reached through an adapter.
