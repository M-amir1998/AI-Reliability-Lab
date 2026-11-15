# Incident-to-eval

A production incident becomes a permanent regression test through a fixed, human-gated
pipeline (`incidents/pipeline.py`, `incidents/store.py`):

```
raw incident ──sanitize──> sanitized incident ──classify──> FailureCase (observed failure)
                                   │
                                   └──convert──> DRAFT TestCase ──review──> REVIEWED ──activate──> ACTIVE
                                                 (minimal reproduction,                      (runs in suite
                                                  inferred evaluators)                       incident_regressions)
```

Nothing is committed, activated or gated automatically. See
[sanitization.md](sanitization.md) and [regression-lifecycle.md](regression-lifecycle.md).

## Storage layout

`IncidentStore` uses `<root>/incidents/` (git-ignored; anchored as `/incidents/`):

```
incidents/
├── raw/<incident_id>.yaml        # as reported — never commit
├── sanitized/<incident_id>.yaml  # redacted; still requires manual review
└── tests/<test_id>.yaml          # DRAFT / REVIEWED / ACTIVE / RETIRED regression tests
```

Only clearly synthetic examples are committed, under `examples/incidents/`.

## Incident format

```yaml
# examples/incidents/INC-EXAMPLE-0002.yaml (synthetic)
incident_id: INC-EXAMPLE-0002
system_id: example-support-bot
category: structured_output          # test category of the resulting test
severity: high                       # default high
trace_id: trace-example-0002
description: "..."
input:      {prompt: "...", response_format: json}   # a TestInput
observed:   {response_text: "Sure! Order A-17 is closed."}
expected:                             # machine-checkable ExpectedBehavior (required to convert)
  json_schema: {...}
  json_expected: {order_id: A-17, status: closed}
expected_description: "Valid JSON matching the order schema."
suspected_root_cause: "... (unverified)"
tags: [orders]
```

The observed failure (fact) is kept apart from `suspected_root_cause` (hypothesis). Do not put a
`sanitization:` block in raw files: the store skips redaction for incidents already marked
`sanitized: true`.

## Commands

```bash
aireliability incidents add examples/incidents/INC-EXAMPLE-0002.yaml   # -> incidents/raw/
aireliability incidents sanitize INC-EXAMPLE-0002 --redact "Customer Name"  # optional literals
aireliability incidents convert INC-EXAMPLE-0002                         # sanitizes if needed
aireliability incidents review incident.INC-EXAMPLE-0002 --reviewer rev-a
aireliability incidents activate incident.INC-EXAMPLE-0002
aireliability incidents list
```

`--redact` may be repeated. `convert` refuses to overwrite a test that is no longer DRAFT.

## Classification

`classify_incident()` is rule-based and describes the *observed* failure only
(`FailureCase.confidence = "unverified"`):

| Condition (first match) | `observed_failure` |
|---|---|
| observed error mentions "timeout" / "timed out" | `timeout` |
| any other observed error | `system_error` |
| category in `prompt_injection`, `jailbreak`, `privacy`, `safety`, `instruction_hierarchy` | `safety_failure` |
| category `tool_use` / `agent`, or observed tool calls | `tool_failure` |
| category `rag` | `hallucination` |
| category `structured_output` | `format_failure` |
| otherwise | `wrong_answer` |

The resulting test gets tags `incident`, `regression` and `failure:<observed_failure>`.

## Conversion

`incident_to_test()` requires a sanitized incident and a non-empty `expected`. It produces:

* `test_id = incident.<incident_id>`, `category = incident.category`;
* input = **minimal reproduction**: the sanitized `TestInput` with `variables` and `prompt_ref`
  dropped;
* `metadata`: `status: draft`, `source: incident`, `severity`, `incident_id`, `trace_id`,
  description;
* evaluators inferred from the expectation fields (`infer_evaluators`):

| Expectation field | Evaluator(s) |
|---|---|
| `exact` | `exact_match` |
| `number` | `numeric` |
| `label` | `classification` |
| `regex` | `regex` |
| `contains_all` / `contains_any` / `not_contains` | `contains` |
| `json_schema` | `json_valid`, `json_schema` |
| `json_expected` | `json_semantic` |
| `assertions` | `unit_test` |
| `safety.canary` | `canary_leak` |
| `safety.injection_markers` | `injection_resistance` |
| `safety.must_refuse` / `forbidden_patterns` / `max_output_chars` | `policy` |
| `tools` | `tool_policy` (+ `tool_call` with required calls or argument types, + `tool_trace` with call limits) |
| `citations` | `citation` |
| `rag` | `hallucination` |

An expectation that selects no evaluator is rejected (`InvalidTestCase`). Review the inferred
list during the REVIEWED step; it is a starting point, not a judgement of what matters.

## Running incident tests

Incident tests are registered as the suite `incident_regressions` (all files in
`incidents/tests/`; suite version = number of tests). Runs filter to ACTIVE tests by default.
`release_full` includes it automatically when a workspace registers incident tests;
`release_regression` does not, so run `incident_regressions` explicitly (or include it from a
YAML suite with `include: [incident_regressions]`) when gating on the smaller suite.

## Demo

`aireliability demo incident-to-eval` uses a fabricated incident (`INC-2026-0001`, fake e-mail,
phone number and API key, synthetic canary): sanitize -> classify -> DRAFT -> REVIEWED ->
ACTIVE, then runs `incident_regressions` against a mock "v1" that follows the injected
instruction (FAIL) and a mock "v2" that does not (PASS). Mock results only.
