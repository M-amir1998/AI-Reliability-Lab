# Regression test lifecycle

Every `TestCase` has `metadata.status` (`TestStatus`). The lifecycle keeps unreviewed tests out
of release gates and keeps retired tests in history.

## States

| Status | Meaning | Runs by default |
|---|---|---|
| `draft` | Generated (e.g. from an incident); not yet reviewed | no |
| `reviewed` | A named (pseudonymous) reviewer checked input, expectation and evaluators | no |
| `active` | Part of the regression set | **yes** |
| `retired` | Kept for history; no longer run | no |

Hand-written and built-in tests default to `active`; incident-derived tests start as `draft`.

## Allowed transitions

Enforced by `incidents.pipeline.transition()`; anything else raises `InvalidStatusTransition`:

```
          ┌──────────── RETIRED <──────────────┐
          │                ^                   │
        DRAFT ────────> REVIEWED ────────> ACTIVE
          ^                │
          └────────────────┘   (send back for rework)
```

| From | To |
|---|---|
| `draft` | `reviewed`, `retired` |
| `reviewed` | `active`, `draft`, `retired` |
| `active` | `retired` |
| `retired` | — (terminal) |

Moving to `reviewed` requires a reviewer id (`--reviewer`); it is stored in
`metadata.reviewer`. DRAFT -> ACTIVE is rejected: review cannot be skipped. An ACTIVE test cannot
go back to DRAFT; retire it and create a new version instead.

CLI support: `aireliability incidents review <test_id> --reviewer <id>` (-> REVIEWED) and
`aireliability incidents activate <test_id>` (-> ACTIVE). Retirement and sending back to DRAFT
are available through `IncidentStore.set_status()` in Python; there is no CLI command for them.

## Status and comparability

`TestCase.definition_hash()` covers input, expected behaviour and evaluators only. Changing the
status (or reviewer) does not change the hash, so a test promoted to ACTIVE stays comparable
with runs made while it was DRAFT. Editing the definition changes the hash and the test becomes
`not_comparable` against older runs ([regression-testing](../methodology/regression-testing.md)).

## Selection

`TestSuite.select()` and `SuiteFilter` default to `statuses: [active]`. To run drafts, e.g. to
check that a new incident test fails on the current system before review, set the filter
explicitly:

```yaml
# illustrative RunConfig fragment
suite: incident_regressions
filters: {statuses: [draft, reviewed]}
```

A run whose filters leave no tests fails with `ConfigurationError`.

## The `incident_regressions` suite

`Workspace` registers every YAML file in `incidents/tests/` as suite `incident_regressions`
(tier `release`, version = number of files). Like every suite, runs keep only ACTIVE tests
unless filters say otherwise. It is not composed into `release_regression`; reference it
explicitly in the run config or include it from a YAML suite to gate releases on it.

Recommended review checklist before `review`:

1. The reproduction is minimal and contains no sensitive data ([sanitization](sanitization.md)).
2. The expectation captures the *correct* behaviour, not merely "different from the incident".
3. The inferred evaluators check what matters; add or remove evaluators as needed.
4. Severity is right — CRITICAL tests block releases on their own.
5. The test fails on the system version that caused the incident and passes on the fix.
