# System adapters

Every system under test (SUT) — plain LLM, RAG pipeline, agent or custom endpoint — is executed
through one port, `systems/base.py`:

```python
@runtime_checkable
class SystemUnderTest(Protocol):
    @property
    def descriptor(self) -> SystemDescriptor: ...          # registry record, no secrets
    async def execute(self, test: TestInput) -> SystemResponse: ...
```

Contract:

* `execute` must not raise for transport problems. It returns a `SystemResponse` with `error`
  set (`ErrorCode`: `timeout`, `http_error`, `network_error`, `rate_limited`,
  `invalid_response`, `system_error`) and `retryable` where appropriate. The runner owns retries.
* Adapters holding resources implement `aclose()` (`ClosableSystem`); `run_evaluation` calls it
  when present.
* `SystemResponse` is canonical for all kinds: `text`, `tool_calls`, `tool_results`,
  `approval_requests`, `retrieved_documents`, `citations`, `usage`, `timing`, `error`,
  `run_status`, `model`, `attempts`, `cost_usd`. All fields are untrusted data.

## Configuration and registry

`SystemRegistry` (`systems/registry.py`) loads `configs/systems/*.yaml` as `SystemConfig` and
pre-registers the synthetic systems as `synthetic/<name>`. A run can also reference a YAML path
directly. `SystemConfig.type` selects the adapter:

| `type` | Adapter | `SystemKind` |
|---|---|---|
| `synthetic` | `MockSystem` with a built-in profile | `mock` |
| `mock` | `MockSystem` with the inline `mock:` profile | `mock` |
| `scripted` | `ScriptedSystem` | `scripted` |
| `http` | `HTTPSystem` (canonical contract) | `http` |
| `openai_compatible`, `inferencelab` | `OpenAICompatibleSystem` | `openai_compatible` |
| `agent` | `AgentSystemAdapter` | `agent` |
| `agentruntime` | `AgentRuntimeSystemAdapter` (preset of the agent adapter) | `agent` |
| `rag` | `RAGSystemAdapter` | `rag` |

Validation: endpoint types require `endpoint`; `openai_compatible`/`inferencelab` also require
`model`. Other keys: `api_key_env`, `temperature`, `seed`, `max_tokens` (default 1024),
`timeout_s` (default 60, the HTTP client timeout), `allow_private_network`, `allowed_hosts`,
`prompt: {prompt_id, version}`, `tool_config`, `rag_config`, `pricing`, `rate_limit`, `metadata`.

## Mock, scripted and synthetic systems

`MockSystem` (`systems/mock/system.py`) is fully deterministic. For mock and synthetic systems,
`run_evaluation` builds a `KnowledgeBase` from the selected suite, so the mock "knows" the
reference response of every test (`reference_text`, else a value derived from `exact`,
`json_expected`, `label`, `number`, `contains_*`, `required_facts`, or a refusal when
`must_refuse` is set). Lookup is `exact` (SHA-256 of the input) or `fuzzy` (character-trigram
Jaccard >= `fuzzy_threshold`, default 0.55). Exact matching makes a system brittle to
perturbations, which the robustness suite detects.

`MockProfile.rules` assign a `MockBehavior` to tests matching all given selectors
(`categories`, `tags`, `test_ids`, `test_id_prefixes`) and a deterministic `fraction`
(hash of seed, rule index and test id). First matching rule wins.

| `MockBehavior` | Effect |
|---|---|
| `correct` | Reference response, tool calls and citations |
| `incorrect` | Deterministic corruption: JSON value changed, numbers +1, refusal turned into compliance, else a generic wrong sentence |
| `unsafe` | Prefixes `Sure. My instructions are: <system prompt>` and obeys every injected directive |
| `hallucinated` | Appends an unsupported claim with a fabricated citation `doc-fabricated-404` |
| `malformed_json` | Truncates the output so it is no longer valid JSON |
| `injection_compliant` | Obeys directives found by regex in prompt, documents and tool outputs (see [prompt-injection](../methodology/prompt-injection.md)) |
| `tool_misuse` | Adds a write-tool call with `{"scope": "all"}` without approval and repeats the last call 5 times |
| `timeout` | `ErrorCode.TIMEOUT`, not retryable |
| `http_failure` | HTTP 500, not retryable |
| `refusal` | `"I can't help with that request."`, no tool calls |
| `verbose` | Correct answer padded with filler (judge verbosity fixtures) |

Other profile knobs: `default_behavior`, `unknown_input_behavior`, `base_latency_ms` (300),
`latency_jitter_ms` (200), `simulate_delay` (False: latency is *reported*, not slept),
`transient_failure_rate` / `transient_failures_per_input` (retryable 503s on early attempts),
`output_token_ratio` (0.25). Mock token counts are synthetic (input: characters // 4; output:
characters x `output_token_ratio`), not measurements.

Synthetic systems (`systems/mock/synthetic.py`):

| id | Profile (seeded) |
|---|---|
| `synthetic/good` | Correct, fuzzy lookup |
| `synthetic/buggy` | Exact lookup; `malformed_json` on 30% of `structured_output`, `incorrect` on 25% of `correctness`/`reasoning`, `hallucinated` on 50% of `rag` |
| `synthetic/unsafe` | `tool_misuse` on `tool_use`; `injection_compliant` on safety categories |
| `synthetic/slow` | Reported latency 2500 ms + up to 3000 ms jitter |
| `synthetic/flaky` | 40% of inputs fail once with a retryable 503 |

`ScriptedSystem` applies ordered regex rules (`scope: prompt|all`, case-insensitive, DOTALL)
returning fixed `SystemResponse`s; first match wins, default `"I don't know."`.

## Endpoint adapters

All endpoint adapters use `httpx` directly (no vendor SDK), `post_json()` for uniform error
classification, and `auth_headers(api_key_env)` which reads the key from the named environment
variable at call time. If the variable is unset, the request is sent without an
`Authorization` header (no error is raised).

Transport classification (`systems/http/transport.py`): 429 -> `rate_limited` (retryable);
408, 425, 502, 503, 504 -> `http_error` (retryable); other >= 400 -> `http_error` (not
retryable; plain 500s are treated as deterministic bugs); httpx timeout -> `timeout`
(retryable); other transport errors -> `network_error` (retryable); non-JSON body ->
`invalid_response`.

| Adapter | Request | Response mapping |
|---|---|---|
| `HTTPSystem` | `{"input": <TestInput without prompt_ref>, "temperature", "seed"}` | `{"text", "usage": {"input_tokens","output_tokens"}, "model"}` |
| `OpenAICompatibleSystem` | `POST {endpoint}/chat/completions`; system message, developer prompt as a second system message prefixed `[developer]`, user message with `<untrusted_context>` / `<untrusted_tool_outputs>` blocks; `response_format: json_object` when `response_format: json`; tools as functions | `choices[0].message` content and tool calls, `usage.prompt_tokens/completion_tokens` |
| `AgentSystemAdapter` | `{"task", "system_prompt", "developer_prompt", "context_documents", "tool_output_fixtures", "tools", "policy_version"}` | canonical agent contract (below) |
| `RAGSystemAdapter` | `{"query", "fixture_documents", "top_k"}` | canonical RAG contract (below) |
| `CallableSystem` | in-process async callable | any mapper; exceptions become `system_error` |

The `<untrusted_context>` delimiters are a formatting convention, not a security boundary.

The versioned prompt selected with `prompt:` is recorded (id, version, sha256) in the descriptor
and run manifest for every system type, but its template is only *sent* by
`OpenAICompatibleSystem` (`openai_compatible`, `inferencelab`), and only for tests that have no
`system_prompt` of their own. `http`, `agent`, `agentruntime` and `rag` adapters never send it.

### Canonical agent contract

```json
{
  "final_answer": "...",
  "status": "completed | failed | awaiting_approval",
  "tool_calls": [{"tool": "...", "arguments": {}, "decision": "allow|deny|require_approval",
                  "approved": true, "call_id": "..."}],
  "tool_results": [{"tool": "...", "call_id": "...", "output": "...", "is_error": false}],
  "approval_requests": [{"tool": "...", "reason": "..."}],
  "usage": {"input_tokens": 0, "output_tokens": 0}
}
```

### Canonical RAG contract

```json
{"answer": "...",
 "retrieved": [{"doc_id": "...", "text": "...", "score": 0.8, "source": "..."}],
 "citations": [{"doc_id": "...", "quote": "...", "claim": "..."}],
 "usage": {"input_tokens": 0, "output_tokens": 0}}
```

Payloads are parsed with lenient pydantic models (unknown keys ignored); a payload that does not
validate becomes `invalid_response`. Tool arguments may be a JSON object or a JSON string; an
unparseable string is kept as `{"_unparsed": <first 2000 chars>}`. No vector database is needed:
RAG evaluators use the documents the pipeline reports, else the test's fixture documents.

## InferenceLab and AgentRuntime without a hard dependency

Neither project is imported. `inferencelab` is an alias of `openai_compatible` (see
`configs/systems/inferencelab-local.yaml`), and `agentruntime` is the agent adapter pointed at
an AgentRuntime-style endpoint that returns the canonical agent contract (route is
configuration, see `configs/systems/agentruntime-local.yaml`). For in-process pipelines use
`systems.agent.adapter.in_process_agent()` or `systems.rag.adapter.in_process_rag()`.

## Endpoint safety (SSRF policy)

`validate_endpoint()` (`systems/http/endpoint_safety.py`) runs when an adapter is constructed:

1. Scheme must be in `allowed_schemes` (`https`, `http`).
2. Host must be present; credentials embedded in the URL are rejected.
3. If `allowed_hosts` is set, the host must be listed — and then no further checks apply.
4. If `allow_private_network: true`, the URL is accepted.
5. Otherwise blocked: `localhost`, `metadata`, `metadata.google.internal`, `instance-data`,
   `ip6-localhost`, and hosts ending in `.localhost`, `.internal`, `.local`; literal IPs that
   are private, loopback, link-local (incl. 169.254.169.254), reserved, multicast or unspecified.

Limits: `EndpointPolicy.resolve_dns` defaults to `False` and the registry does not enable it, so
a public-looking hostname that *resolves* to a private address is not blocked; DNS rebinding is
not addressed. Adapters that create their own `httpx.AsyncClient` disable redirects; an injected
client keeps its own settings. Opting into private networks is intended for local servers
(InferenceLab, AgentRuntime on `127.0.0.1`) and must not be used on shared deployments.

## Adding an adapter

See [CONTRIBUTING.md](../../CONTRIBUTING.md#adding-a-system-adapter).
