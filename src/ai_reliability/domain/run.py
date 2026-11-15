"""Evaluation run manifest and summary: everything needed to reproduce and audit a run."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import CapturePolicy, RunStatus
from ai_reliability.domain.result import MetricResult
from ai_reliability.domain.system import SystemDescriptor


class RunManifest(DomainModel):
    """Immutable description of what was tested, how, and with which versions."""

    run_id: str
    created_at: datetime
    status: RunStatus = RunStatus.RUNNING
    tool_version: str
    git_sha: str | None = None
    system: SystemDescriptor
    suite_id: str
    suite_version: str
    test_count: int
    test_ids_hash: str
    evaluator_versions: dict[str, str]
    config_hash: str
    seed: int = 0
    repetitions: int = Field(default=1, ge=1)
    capture_policy: CapturePolicy = CapturePolicy.FAILED_CASES
    baseline_run_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    completed_at: datetime | None = None


class CategorySummary(DomainModel):
    """Per-category aggregate."""

    category: str
    total: int
    passed: int
    failed: int
    errors: int
    pass_rate: float | None
    ci_low: float | None = None
    ci_high: float | None = None


class RunSummary(DomainModel):
    """Aggregate metrics for one run. Component metrics stay primary; no magic score."""

    run_id: str
    total_executions: int
    unique_tests: int
    metrics: dict[str, MetricResult]
    categories: dict[str, CategorySummary]
    failure_categories: dict[str, int] = Field(default_factory=dict)
    critical_failures: tuple[str, ...] = ()

    def metric(self, name: str) -> float | None:
        result = self.metrics.get(name)
        return None if result is None else result.value
