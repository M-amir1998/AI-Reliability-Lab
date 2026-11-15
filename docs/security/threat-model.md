# Threat model

Short summary. Controls, limits and the vulnerability reporting process are in
[SECURITY.md](../../SECURITY.md).

## Assets

| Asset | Where |
|---|---|
| Provider API keys | environment variables / Jenkins credentials (referenced by name only) |
| Production incident and trace content | `incidents/`, `traces/` (git-ignored) |
| Model output captured in results | `results/<run-id>/*.parquet`, checkpoint journal |
| Integrity of release decisions | `summary.json`, `gates.json`, baselines in `registry.sqlite` |
| The CI host (often a 16 GB laptop) | Jenkins agent |

## Trust boundaries

```
 trusted                         |  untrusted
 --------------------------------+-------------------------------------------
 repository configs, suites,     |  system-under-test output (text, tool calls,
 gate policies, evaluator code   |    citations, retrieved documents)
 Jenkins credentials store       |  test context from production (incidents, traces)
 operator running the CLI        |  external endpoints and judge models
```

## Threats and mitigations

| Threat | Mitigation | Residual risk |
|---|---|---|
| Direct / indirect prompt injection against the SUT | Measured by red-team families, canaries, markers, tool-policy evaluators | Evidence-based detection misses silent behaviour changes |
| SUT output manipulates an LLM judge | Judge-attack regex -> `needs_review`; tagged untrusted answer; invalid output -> `needs_review`; judges off by default | Regex is easily evaded; calibrate judges |
| Code execution via model output | JSON/regex parsing only; declarative assertions; no eval/exec; `pickle` banned by ruff; `yaml.safe_load` | — |
| Secret leakage into results or Git | env-var names only; redaction of captured text; git-ignored artifact dirs; secret scan stage in Jenkins | Redaction is pattern-based |
| PII in incidents/traces | sanitization + residual scan + manual review; `metadata_only` trace default | Names/addresses not detected; some incident fields not redacted |
| SSRF via configured endpoints | scheme/host/IP policy, metadata hosts blocked, redirects off | No DNS resolution by default; opt-outs bypass checks |
| ReDoS via test regexes | configs are trusted input | Synchronous regex cannot be timed out |
| Tampering with stored runs | immutable-run checks, `checksums.json`, append-only baselines | Checksums are co-located; not tamper-proof |
| Credential exposure in CI | Jenkins "Secret text" bound only in the optional stage; default build blanks provider keys; reports archived, not raw Parquet | Build parameters are interpolated into a shell script; restrict who can trigger builds |
| Resource exhaustion on the CI host | `disableConcurrentBuilds`, 45-minute timeout, bounded runner concurrency | — |

## Out of scope

Hardening of the systems under test, multi-tenant deployment of the lab, and protection against
a malicious repository owner.
