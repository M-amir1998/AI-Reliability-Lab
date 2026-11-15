# ADR-005: Incident-to-eval workflow

**Status:** Accepted

## Context

Production incidents are the most valuable source of regression tests: they describe failures
that actually happened. They are also the most dangerous input: raw incidents contain secrets,
personal data and customer content, and an automatically generated test can encode the wrong
expectation. Root causes stated at incident time are often wrong.

## Decision

* Incidents follow a fixed pipeline: raw -> **sanitize** (pattern redaction + residual scan) ->
  **classify** (observed failure only) -> **convert** (minimal reproduction, inferred
  deterministic evaluators) -> DRAFT test.
* Conversion requires a sanitized incident and a machine-checkable `expected` behaviour.
* Observed failure (`FailureCase.observed_failure`, fact) is stored separately from
  `suspected_root_cause` (hypothesis, `confidence: unverified`).
* Generated tests start as DRAFT. Promotion requires a named (pseudonymous) reviewer
  (DRAFT -> REVIEWED) and an explicit activation (REVIEWED -> ACTIVE); DRAFT -> ACTIVE is
  rejected. Only ACTIVE tests run by default.
* Incident data lives in git-ignored `incidents/`; the tool never commits anything.
* Incident-derived tests form the `incident_regressions` suite.

## Consequences

Positive:

* Every fixed incident can become a permanent, gated regression test.
* Sensitive data is reduced before a test exists, and a human checks every test before it can
  affect a release.
* Classification and root-cause hypotheses cannot be confused in reports.

Negative / trade-offs:

* The workflow is deliberately manual; tests do not appear in gates until someone reviews and
  activates them.
* Redaction is best-effort and does not cover every field; manual review remains mandatory.
* Evaluator inference is mechanical and may select too much or too little; reviewers must adjust.
* `incident_regressions` must be included in release runs explicitly.
