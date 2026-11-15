# ADR-009: Model-agnostic system adapters

**Status:** Accepted

## Context

The lab must evaluate plain LLMs, RAG pipelines and tool-using agents from different providers
and in-house services, and compare them with each other. Coupling evaluators or the runner to a
vendor SDK or to a specific agent framework would make comparisons uneven and add heavy
dependencies. Related projects (InferenceLab, AgentRuntime) should be usable without becoming
hard dependencies.

## Decision

* One port, `SystemUnderTest` (`descriptor`, `async execute(TestInput) -> SystemResponse`), for
  every kind of system. `SystemResponse` is canonical: text, tool calls and results, approval
  requests, retrieved documents, citations, usage, timing, error.
* Adapters: mock/synthetic, scripted, generic HTTP, OpenAI-compatible chat completions, agent,
  RAG, and in-process callables. All HTTP adapters use `httpx` directly; no vendor SDK.
* Adapters return classified errors instead of raising; the runner applies one retry policy.
* Canonical JSON contracts for agents and RAG pipelines are documented; services implement them
  or are wrapped by a mapper.
* InferenceLab is configured as an OpenAI-compatible endpoint (`type: inferencelab`) and
  AgentRuntime as an agent endpoint (`type: agentruntime`); neither is imported.
* Every endpoint passes the SSRF policy; credentials are referenced by env-var name.

## Consequences

Positive:

* Any system with an HTTP API or a Python callable can be evaluated with the same suites,
  evaluators, gates and reports.
* Comparisons between systems differ only in the adapter, not in evaluation logic.
* Mock systems implement the same port, so CI exercises the real runner and evaluators.

Negative / trade-offs:

* Systems with other protocols need an adapter or a thin wrapper service.
* Provider-specific features (streaming, logprobs, multi-turn sessions) are not modelled; only
  single-turn requests are executed and TTFT is recorded only if reported.
* The OpenAI-compatible adapter makes rendering choices (developer prompt as a second system
  message, untrusted context in tagged blocks) that influence results.
