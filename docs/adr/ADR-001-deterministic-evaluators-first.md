# ADR-001: Deterministic evaluators before LLM judges

**Status:** Accepted

## Context

Release gates need verdicts that are reproducible, cheap and explainable. LLM-as-a-judge is
attractive for open-ended answers but is stochastic, costs money per call, needs network access,
changes when the provider updates the model, shows known biases (verbosity, position, format,
self-preference), and can be manipulated by the very output it grades. The default CI path must
run offline on a CPU-only laptop.

## Decision

* All built-in evaluators are deterministic: text, structured output, operational, safety,
  RAG (lexical) and agent evaluators. `default_evaluator_registry()` registers only these and
  three composites.
* `LLMJudgeEvaluator` exists but is never registered by default; it must be added explicitly.
* A judge records full provenance (model, revision, prompt template and hash, temperature,
  rubric id and version, run id) and its evaluator version embeds rubric, model and prompt hash.
* Invalid judge output, failed judge calls and suspected judge-manipulation attempts produce
  `needs_review`, never a silent PASS.
* Judges are calibrated against labels (`calibrate_judge`) and probed for biases before use;
  offline `ScriptedJudge`s exercise this machinery in tests and demos.

## Consequences

Positive:

* Verdicts are reproducible bit-for-bit for the same response; CI needs no API keys.
* Every failure has a concrete reason (missing substring, schema path, leaked canary form).
* Evaluator semantics are versioned and auditable.

Negative / trade-offs:

* Deterministic checks are narrower: lexical groundedness misses paraphrase and can pass
  contradictions; refusal detection is a regex heuristic; exact/contains checks need careful
  expectations.
* Test authors must write machine-checkable expectations, which takes more effort than a rubric.
* Open-ended quality (helpfulness, tone) is only measurable with an explicitly configured,
  calibrated judge or human review.
