# ADR-006: Prompt text not retained by default

**Status:** Accepted

## Context

Evaluation inputs can contain proprietary prompts, protected system prompts with (synthetic)
secrets, customer documents from incidents, and production traces. Result files are archived,
shared and kept for a long time. Storing full prompts and responses everywhere would turn the
result store into a copy of sensitive data. Debugging, on the other hand, needs to see what a
system answered when it failed.

## Decision

* Result files never store input text (user prompt, system/developer prompt, documents, tool
  outputs). Each execution keeps `input_sha256` of the canonical input and `response_sha256`.
* Response text is governed by the run's `CapturePolicy`: `metadata_only` (none),
  `failed_cases` (**default**: non-passing executions only) or `all`. Captured text is passed
  through the `Redactor` and truncated to 4000 characters.
* Trace import applies a `TracePrivacy` policy at the boundary; the default is `metadata_only`
  (no input or response text). `hash_only`, `redacted_text` and `full_text` are opt-in; only the
  last two make traces replayable.
* Observability (Prometheus, OpenTelemetry) exports metadata only.
* Jenkins archives reports and summaries, not Parquet files or journals.

## Consequences

Positive:

* Result artifacts can be archived and shared with a much smaller privacy footprint.
* Hashes still allow checking that two runs used identical inputs.
* The default keeps exactly the evidence needed to debug failures.

Negative / trade-offs:

* Under the default policy, captured failure responses are still stored (redacted); a response
  may itself echo prompt content or leaked secrets. Use `metadata_only` for sensitive systems.
* Inspecting a passing response requires `capture: all` or re-running the test.
* Hashes of short or guessable inputs can be brute-forced; hashing is not anonymisation.
* Shadow replay requires traces imported with `redacted_text` or `full_text`.
