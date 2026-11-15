# ADR-002: Immutable evaluation runs

**Status:** Accepted

## Context

Release decisions are made by comparing a candidate with a baseline. If a baseline run can be
re-executed in place, edited or partially overwritten, a later comparison silently compares
against something different from what was approved. Runs against external endpoints are also
slow and expensive, so an interruption must not force a full restart.

## Decision

* Every run gets its own directory `results/<run-id>/` with a `RunManifest` recording tool
  version, git SHA, system descriptor (incl. prompt hash), suite id/version, `test_ids_hash`,
  evaluator versions, config hash, seed, repetitions and capture policy.
* The manifest moves RUNNING -> (INTERRUPTED) -> COMPLETED. Once COMPLETED, `RunStore` refuses
  to create or finalize the run again and `RunIndex` refuses to update it (`ImmutableRunError`).
* Finalization writes all artifacts, then the COMPLETED manifest, then `checksums.json`;
  `aireliability runs verify` re-checks them.
* Interrupted runs resume from an append-only checkpoint journal keyed by deterministic
  execution ids; the journal is removed at finalization.
* Baselines are append-only declarations in SQLite; only completed runs can be declared.
* Domain objects are frozen pydantic models.

## Consequences

Positive:

* A comparison or gate decision always refers to a fixed, auditable set of results.
* Interrupted runs resume without repeating completed executions.
* Re-running produces a new run id instead of mutating history.

Negative / trade-offs:

* Correcting a mistake means a new run, not an edit; disk usage grows (mitigated by compact,
  compressed Parquet and git-ignored results).
* Immutability is enforced by code paths, not file-system permissions; checksums sit next to
  the artifacts and detect accidents, not deliberate tampering.
* Regenerated reports go to `reports/<run-id>/` rather than into the run directory.
