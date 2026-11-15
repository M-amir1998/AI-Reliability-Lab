"""Canary evaluation: baseline and candidate deployments on the same tests, compared on
quality, safety, latency, cost and errors."""

from __future__ import annotations

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.regression import ComparisonReport
from ai_reliability.domain.run import RunSummary
from ai_reliability.domain.test_case import TestSuite
from ai_reliability.metrics.aggregate import summarize
from ai_reliability.regression.compare import compare_runs
from ai_reliability.runner.executor import EvaluationRunner

SAFETY_CATEGORIES = frozenset(
    {
        "prompt_injection",
        "jailbreak",
        "privacy",
        "safety",
        "instruction_hierarchy",
        "tool_use",
        "resource_abuse",
    }
)


class DimensionDelta(DomainModel):
    dimension: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    better: str


class CanaryReport(DomainModel):
    baseline_run_id: str
    candidate_run_id: str
    dimensions: tuple[DimensionDelta, ...]
    comparison: ComparisonReport


def _category_rate(
    summary: RunSummary, categories: frozenset[str], *, safety: bool
) -> float | None:
    selected = [c for name, c in summary.categories.items() if (name in categories) == safety]
    total = sum(c.total for c in selected)
    return sum(c.passed for c in selected) / total if total else None


def _delta(name: str, b: float | None, c: float | None, better: str) -> DimensionDelta:
    return DimensionDelta(
        dimension=name,
        baseline=b,
        candidate=c,
        delta=None if b is None or c is None else c - b,
        better=better,
    )


def canary_dimensions(baseline: RunSummary, candidate: RunSummary) -> tuple[DimensionDelta, ...]:
    return (
        _delta(
            "quality_pass_rate",
            _category_rate(baseline, SAFETY_CATEGORIES, safety=False),
            _category_rate(candidate, SAFETY_CATEGORIES, safety=False),
            "higher",
        ),
        _delta(
            "safety_pass_rate",
            _category_rate(baseline, SAFETY_CATEGORIES, safety=True),
            _category_rate(candidate, SAFETY_CATEGORIES, safety=True),
            "higher",
        ),
        _delta(
            "p95_latency_ms",
            baseline.metric("p95_latency_ms"),
            candidate.metric("p95_latency_ms"),
            "lower",
        ),
        _delta(
            "mean_cost_usd",
            baseline.metric("mean_cost_usd"),
            candidate.metric("mean_cost_usd"),
            "lower",
        ),
        _delta(
            "system_error_rate",
            baseline.metric("system_error_rate"),
            candidate.metric("system_error_rate"),
            "lower",
        ),
    )


async def run_canary(
    baseline_runner: EvaluationRunner,
    candidate_runner: EvaluationRunner,
    suite: TestSuite,
    *,
    baseline_run_id: str,
    candidate_run_id: str,
) -> CanaryReport:
    """Run both deployments on the identical test set and compare."""
    base = await baseline_runner.run(baseline_run_id, suite.tests)
    cand = await candidate_runner.run(candidate_run_id, suite.tests)
    bs, cs = summarize(baseline_run_id, base.results), summarize(candidate_run_id, cand.results)
    comparison = compare_runs(
        baseline_run_id,
        base.results,
        candidate_run_id,
        cand.results,
        baseline_summary=bs,
        candidate_summary=cs,
    )
    return CanaryReport(
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        dimensions=canary_dimensions(bs, cs),
        comparison=comparison,
    )
