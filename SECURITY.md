# Security policy

AI Reliability Lab evaluates systems that are, by assumption, untrusted: their outputs, the
documents they retrieve and the tools they call may be adversarial. This document describes the
security model, the controls in the code and their limits. A shorter threat model is in
[docs/security/threat-model.md](docs/security/threat-model.md).

## Supported versions

The project is pre-alpha (`0.1.0.dev0`). Only the latest commit on `main` of the 0.1.x
development line receives fixes.

| Version | Supported |
|---|---|
| 0.1.x (dev, `main`) | yes |
| anything older | no |

## Reporting a vulnerability

Please report vulnerabilities privately by opening a **GitHub security advisory** on this
repository (Security tab -> "Report a vulnerability"). Do not open a public issue, pull request
or discussion for an undisclosed vulnerability, and do not include real secrets or personal data
in the report — synthetic reproductions are preferred.

Include the affected component, a minimal reproduction, the impact you expect, and whether the
issue is already public. Reports are handled on a best-effort basis by the maintainer.

## Security model

### Prompt injection and indirect injection

All system output and all test context (`documents`, `tool_outputs`) are untrusted data.

* The lab never follows instructions found in model output; output is only parsed and compared.
* The OpenAI-compatible adapter separates system, developer and user content and wraps documents
  and tool outputs in `<untrusted_context>` / `<untrusted_tool_outputs>` tags. This is a
  formatting convention, **not** a security boundary.
* Resistance is *measured*, not assumed: the red-team catalogue covers direct, indirect,
  tool-output and nested injection, instruction hierarchy, jailbreaks, extraction, tool and
  resource abuse ([docs/methodology/prompt-injection.md](docs/methodology/prompt-injection.md)).
* Detection relies on evidence (markers, canary leaks, unauthorized tool calls, a lexical
  refusal heuristic). Injections that change behaviour without such evidence are not detected.

### Attacks on the judge

An optional LLM judge reads untrusted answers and can be manipulated by them.

* `LLMJudgeEvaluator` routes a response to `needs_review` without calling the judge when it
  matches `_JUDGE_ATTACK` (`evaluators/judge/evaluator.py`): phrases such as "as the judge",
  "dear grader", "grade/score/rate this answer as", "ignore the/your rubric" or an embedded
  `"verdict": "pass"`.
* The judge prompt wraps answers in `<answer>` tags and instructs the judge to ignore
  instructions inside them.
* Any judge output that is not a valid JSON verdict, and any failed judge call, becomes
  `needs_review` — never a silent PASS.
* Limits: the regex is a short phrase list and is trivially evaded by rephrasing or encoding;
  tag-wrapping reduces but does not prevent manipulation. Calibrate judges and keep
  deterministic evaluators on the same tests ([ADR-001](docs/adr/ADR-001-deterministic-evaluators-first.md)).

### Malicious test content

Suites, system configs, gate policies and incidents are configuration and are treated as
trusted input from the repository owner.

* YAML is parsed only with `yaml.safe_load` and validated by strict pydantic models
  (`extra="forbid"`).
* Test-supplied regular expressions (`expected.regex`, `matches` assertions,
  `forbidden_patterns`, scripted-system rules) run on Python's `re`, which has no time limit.
  Evaluators run synchronously inside the event loop, so `evaluator_timeout_s` cannot interrupt
  a catastrophic-backtracking pattern. Do not run suites from untrusted sources.
* JSON schemas are checked with `Draft202012Validator.check_schema` before use.

### No execution of model output

* Responses are parsed with `json.loads`, JSON pointers and regular expressions only.
  Declarative assertions (`unit_test`) compare data; no response content is evaluated.
* There is no `eval`, `exec` or `pickle` of model output. Ruff's banned-API rule rejects any
  `import pickle` (`pyproject.toml`).
* Endpoint payloads are parsed by lenient pydantic models into canonical types; malformed
  payloads become `invalid_response` errors.
* Tool calls proposed by a system are recorded and evaluated as data. The lab never executes
  tools; agent tool execution is the responsibility of the system under test.

### Secret leakage and canaries

* Red-team tests place a **synthetic** canary (`CANARY_SECRET_12345` by default) in a protected
  system prompt. `canary_leak` checks response text, tool arguments and citation quotes for
  plain, obfuscated, reversed, base64, hex and ROT13 forms. Paraphrased, partial or multi-turn
  exfiltration is not detected.
* Never use a real secret as a canary. Incident sanitization deliberately preserves declared
  canary literals.

### Credentials

* Configs reference credentials only by environment-variable **name** (`api_key_env`). The value
  is read at request time to build the `Authorization` header and is never written to configs,
  manifests, results or logs. If the variable is unset, the request is sent unauthenticated.
* `SystemDescriptor` and manifests record `api_key_env`, never the value.
* `aireliability doctor` reports which provider variables are set, never their values.
* In Jenkins, keys come from "Secret text" credentials bound only in the optional external
  stage; the default pipeline blanks `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`
  ([docs/ci/jenkins.md](docs/ci/jenkins.md)).
* Error messages carry error codes and exception type names, not request bodies or headers.

### External endpoint trust and SSRF

`systems/http/endpoint_safety.py` validates every endpoint when an adapter (or judge client) is
built: allowed schemes `https`/`http`; no credentials in URLs; `localhost`, cloud metadata names
and `.localhost`/`.internal`/`.local` hosts rejected; literal private, loopback, link-local
(incl. 169.254.169.254), reserved, multicast and unspecified IPs rejected. Adapters that own their
HTTP client disable redirects.

Limits: hostnames are not resolved by default (`resolve_dns=False`), so a public name resolving
to a private address is not blocked, and DNS rebinding is not addressed. `allow_private_network`
and `allowed_hosts` bypass the checks; use them only for local servers you control, never on a
shared or exposed deployment. Plain `http` is permitted. Treat every response from an external
endpoint as untrusted.

### Trace and result privacy

* Trace import applies a `TracePrivacy` policy at the boundary; the default is
  **`metadata_only`**:

  | Policy | Input/response text kept |
  |---|---|
  | `hash_only` | `sha256:<hash>` only |
  | `metadata_only` (default) | none |
  | `redacted_text` | redacted text |
  | `full_text` | original text |

  Input hashes are always kept; error strings are always redacted; metadata entries that match
  a redaction rule are dropped. Only `redacted_text`/`full_text` traces can be replayed.
* Evaluation results never store input/prompt text (only hashes). Response text is kept
  according to the run capture policy — by default (`failed_cases`) for non-passing executions,
  redacted and truncated to 4000 characters — in `results.parquet`, `failures.parquet` and the
  checkpoint journal. Use `capture: metadata_only` for sensitive systems.
* Hashes of short or guessable text can be brute-forced; hashing is not anonymisation.
* Observability exports metadata only (ids, categories, outcomes, latency); the OTel observer
  never sets prompt or response attributes, and the OTLP analyzer ignores content-like attributes.

### Incident privacy

* `incidents/`, `traces/`, `reviews/`, `results/` and `reports/` are git-ignored; only synthetic
  examples under `examples/` are committed.
* Sanitization redacts common secret and PII patterns, verifies that no pattern remains, and
  always sets `manual_review_required`. It is best-effort and does not cover every field
  ([docs/incidents/sanitization.md](docs/incidents/sanitization.md)).
* Incident-derived tests start as DRAFT and require a named reviewer before activation.

### Local services

`aireliability metrics serve` binds `127.0.0.1` by default and serves read-only aggregate
metrics. `docker-compose.yml` publishes Prometheus, Grafana (anonymous viewer) and the lab on
`127.0.0.1` only; it is intended for local use.

## Out of scope

* Vulnerabilities in the systems being evaluated (the lab reports them; it does not fix them).
* Findings that require a malicious repository owner (configs and suites are trusted input).
* Results of mock systems: they are synthetic and say nothing about real models.
