# Incident sanitization

Raw incidents are assumed to contain secrets and personal data. `sanitize_incident()`
(`incidents/pipeline.py`) redacts them with `Redactor` (`privacy/redaction.py`, version `"1"`)
and then verifies that nothing detectable remains. Redaction is **best-effort**: the result is
always marked `manual_review_required: true`.

## Redaction rules

Applied in this order; each match is replaced by `[REDACTED:<kind>]`:

| Kind | Matches |
|---|---|
| `private_key` | PEM blocks `-----BEGIN ... PRIVATE KEY----- ... -----END ... PRIVATE KEY-----` |
| `authorization_header` | `Authorization: Bearer|Basic|Token <value>` |
| `bearer_token` | `Bearer <16+ token chars>` |
| `jwt` | three base64url segments starting `eyJ...eyJ...` |
| `anthropic_key` | `sk-ant-` + 20+ chars |
| `openai_key` | `sk-` / `sk-proj-` + 20+ chars |
| `aws_access_key` | `AKIA` / `ASIA` + 16 uppercase alphanumerics |
| `github_token` | `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` + 36+ chars, `github_pat_` + 40+ chars |
| `slack_token` | `xoxa-`/`xoxb-`/`xoxp-`/`xoxr-`/`xoxs-` + 10+ chars |
| `google_api_key` | `AIza` + 35 chars |
| `credential_field` | value (4+ chars) after `password`, `passwd`, `pwd`, `secret`, `client_secret`, `api_key`/`api-key`/`apikey`, `access_token`, `token` followed by `:` or `=`; only the value is replaced |
| `email` | e-mail addresses |
| `credit_card` | 13–19 digit sequences (spaces/dashes allowed) that pass the Luhn check |
| `phone` | international numbers starting with `+` |
| `us_phone` | `(555) 555-5555`-style numbers |
| `ipv4` | dotted-quad IPv4 addresses |

Operators can add `extra_literals` (CLI: `aireliability incidents sanitize ID --redact "..."`,
repeatable) for known sensitive strings that patterns cannot recognise — customer names,
internal hostnames, project code names. Literals are replaced with `[REDACTED:literal]` before
the pattern rules run.

## What is redacted

`description`, `expected_description`, all `input` text (`prompt`, `system_prompt`,
`developer_prompt`, document texts, tool outputs, variable values), `observed.response_text`,
`observed.error`, `expected.reference_text` and `suspected_root_cause`.

A raw incident file is always sanitized, even if it claims `sanitized: true`.

Not redacted or re-scanned: tags, ids, and the `expected` fields other
than `reference_text` (e.g. `exact`, `contains_all`, `json_expected`, `safety.forbidden_patterns`).
Write those fields without sensitive data, or check them by hand during review.

## Preserved canary literals

If `expected.safety.canary` is set, that literal is preserved: it is swapped for a placeholder
before redaction and restored afterwards, and the `credential_field` rule skips placeholders.
Canaries are synthetic by contract; without preservation, `internal secret = <canary>` would be
redacted and the reproduction could no longer exhibit the leak it is meant to catch. **Never
declare a real secret as a canary.**

Preservation and `extra_literals` apply only when the default redactor is built by
`sanitize_incident`; passing a custom `redactor` uses that redactor's configuration as-is.

## Residual scan

After redaction, every free-text field listed above is scanned again with the same redactor. If
any rule still matches, sanitization fails with `IncidentSanitizationFailure` naming the kinds
found, and nothing is written. The audit record stores only counts:

```yaml
sanitization:
  sanitized: true
  redactions: {email: 1, openai_key: 1, phone: 1}   # illustrative counts
  sanitizer_version: "1"
  manual_review_required: true
  reviewed_by: null
```

Original values are never stored in the record.

## Manual review is required

Pattern redaction cannot recognise names, postal addresses, account numbers in unusual formats,
free-text descriptions of people, internal URLs, secrets in unknown formats, or sensitive
content in images or attachments. Before any derived artifact leaves the machine:

1. Read `incidents/sanitized/<id>.yaml` and the DRAFT test in full.
2. Add `--redact` literals and re-run sanitization from the raw file if anything remains
   (delete the stale sanitized file first; a sanitized incident is not re-sanitized).
3. Promote the test with `aireliability incidents review <test_id> --reviewer <pseudonym>`.
4. Only then consider committing a derived test, and only if it is acceptable to publish.

## Same redactor elsewhere

* Response text kept under the run capture policy is redacted with the default `Redactor`.
* Trace import with `--privacy redacted_text` uses it for input/response text; error strings follow
  the same capture policy (dropped under `metadata_only`, hashed under `hash_only`, redacted under
  `redacted_text` and `full_text`), and metadata entries that trigger any rule are dropped.

## Limits

* English/Western formats; no named-entity recognition.
* Regexes can over-redact (e.g. version strings shaped like IPv4 addresses, long digit strings
  that happen to pass Luhn) and under-redact (secrets split across lines, encoded secrets).
* Hashes (`sha256:` under `hash_only`, `input_sha256` in results) of short or guessable text can
  be reversed by brute force; hashing is not anonymisation.
