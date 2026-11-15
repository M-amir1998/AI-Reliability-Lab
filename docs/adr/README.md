# Architecture decision records

Each ADR records one decision with its context and consequences (including trade-offs). Format:
**Status**, **Context**, **Decision**, **Consequences**. ADRs are not edited after acceptance
except for status changes; a changed decision gets a new ADR that supersedes the old one.

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-deterministic-evaluators-first.md) | Deterministic evaluators before LLM judges | Accepted |
| [ADR-002](ADR-002-immutable-evaluation-runs.md) | Immutable evaluation runs | Accepted |
| [ADR-003](ADR-003-explicit-quality-gates.md) | Explicit quality gates | Accepted |
| [ADR-004](ADR-004-parquet-result-storage.md) | Parquet result storage | Accepted |
| [ADR-005](ADR-005-incident-to-eval-workflow.md) | Incident-to-eval workflow | Accepted |
| [ADR-006](ADR-006-prompt-text-not-retained-by-default.md) | Prompt text not retained by default | Accepted |
| [ADR-007](ADR-007-jenkins-instead-of-github-actions.md) | Jenkins instead of GitHub Actions | Accepted |
| [ADR-008](ADR-008-cpu-first-architecture.md) | CPU-first architecture | Accepted |
| [ADR-009](ADR-009-model-agnostic-system-adapters.md) | Model-agnostic system adapters | Accepted |
| [ADR-010](ADR-010-regression-metrics-over-single-score.md) | Regression metrics over single aggregate score | Accepted |

To add an ADR, copy the structure of an existing one, use the next number, and link it here.
