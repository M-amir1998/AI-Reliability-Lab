"""Baseline-vs-candidate comparison contracts."""

from __future__ import annotations

from pydantic import Field

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import Outcome, RegressionStatus, Severity


class CaseComparison(DomainModel):
    """Comparison of one test between a baseline and a candidate run."""

    test_id: str
    category: str
    severity: Severity
    status: RegressionStatus
    baseline_pass_rate: float | None = None
    candidate_pass_rate: float | None = None
    baseline_outcome: Outcome | None = None
    candidate_outcome: Outcome | None = None
    reason: str = ""


class CategoryDelta(DomainModel):
    """Per-category change in pass rate, computed over comparable tests only."""

    category: str
    n: int
    baseline_pass_rate: float | None
    candidate_pass_rate: float | None
    delta: float | None
    regressed: int
    improved: int


class MetricDelta(DomainModel):
    """Change in a run-level metric."""

    name: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    relative: float | None = None


class StatisticalTest(DomainModel):
    """Result of a statistical comparison, with assumptions spelled out."""

    name: str
    statistic: float | None = None
    p_value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    n: int = 0
    note: str = ""


class ComparisonReport(DomainModel):
    """Full regression analysis of a candidate against a baseline."""

    baseline_run_id: str
    candidate_run_id: str
    cases: tuple[CaseComparison, ...]
    categories: dict[str, CategoryDelta]
    metrics: dict[str, MetricDelta]
    counts: dict[RegressionStatus, int] = Field(default_factory=dict)
    statistics: tuple[StatisticalTest, ...] = ()

    @property
    def comparable(self) -> int:
        return sum(
            n for status, n in self.counts.items() if status is not RegressionStatus.NOT_COMPARABLE
        )

    @property
    def regression_rate(self) -> float | None:
        """Regressed tests divided by comparable tests."""
        if self.comparable == 0:
            return None
        return self.counts.get(RegressionStatus.REGRESSED, 0) / self.comparable

    def ids_with(self, status: RegressionStatus) -> tuple[str, ...]:
        return tuple(c.test_id for c in self.cases if c.status is status)

    @property
    def new_failures(self) -> tuple[str, ...]:
        return self.ids_with(RegressionStatus.REGRESSED)

    @property
    def fixed_failures(self) -> tuple[str, ...]:
        return self.ids_with(RegressionStatus.IMPROVED)

    @property
    def unchanged_failures(self) -> tuple[str, ...]:
        return self.ids_with(RegressionStatus.UNCHANGED_FAIL)
