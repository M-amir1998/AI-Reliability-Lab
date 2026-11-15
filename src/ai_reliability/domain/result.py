"""Evaluation results: per-evaluator verdicts, per-execution results and metrics."""

from __future__ import annotations

from pydantic import Field

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import ErrorCode, FailureCategory, Outcome, Severity

DetailValue = str | int | float | bool | None


class EvaluationResult(DomainModel):
    """The verdict of one evaluator on one response.

    ``score`` is optional and always in [0, 1] when present. ``details`` holds
    small scalar diagnostics; it must never contain secrets or full prompts.
    """

    evaluator: str
    evaluator_version: str
    outcome: Outcome
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""
    failure_category: FailureCategory | None = None
    details: dict[str, DetailValue] = Field(default_factory=dict)
    components: tuple[EvaluationResult, ...] = ()

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS


class CaseResult(DomainModel):
    """Result of executing and evaluating one test case once (one repetition)."""

    execution_id: str
    run_id: str
    test_id: str
    test_hash: str
    category: str
    severity: Severity
    tags: tuple[str, ...] = ()
    repetition: int = Field(default=0, ge=0)
    outcome: Outcome
    evaluations: tuple[EvaluationResult, ...] = ()
    failure_category: FailureCategory | None = None
    failure_reason: str = ""
    error_code: ErrorCode | None = None
    latency_ms: float | None = None
    ttft_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    response_chars: int = 0
    tool_call_count: int = 0
    attempts: int = 1
    input_sha256: str = ""
    response_sha256: str = ""
    response_text: str | None = None

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS

    @property
    def is_error(self) -> bool:
        return self.outcome is Outcome.ERROR


class MetricResult(DomainModel):
    """A named metric with its sample size and optional confidence interval."""

    name: str
    value: float | None
    n: int = 0
    ci_low: float | None = None
    ci_high: float | None = None
    unit: str = ""
    description: str = ""
