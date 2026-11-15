"""CPU overhead benchmarks of the evaluation *infrastructure* (not LLM inference).

Run explicitly with ``uv run pytest tests/benchmarks -s``. Assertions are generous
sanity bounds, not performance claims.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_reliability.clock import FakeClock
from ai_reliability.config.models import RunnerSettings
from ai_reliability.domain.test_case import ExpectedBehavior
from ai_reliability.evaluators.registry import default_evaluator_registry
from ai_reliability.metrics.aggregate import summarize
from ai_reliability.regression.compare import compare_runs
from ai_reliability.reporting.markdown import render_plots, render_report
from ai_reliability.runner.executor import EvaluationRunner
from ai_reliability.storage.parquet import write_results
from ai_reliability.systems.mock import KnowledgeBase, MockProfile, MockSystem
from tests.conftest import make_case, run
from tests.unit.test_storage_reporting_analysis import manifest

pytestmark = pytest.mark.benchmark
N = 2000
TESTS = [
    make_case(ExpectedBehavior(exact=f"a{i}"), test_id=f"b{i}", prompt=f"benchmark question {i}")
    for i in range(N)
]


def _timed(label: str, fn):  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    value = fn()
    elapsed = time.perf_counter() - start
    print(f"\n{label}: {elapsed:.3f}s")
    return value, elapsed


@pytest.fixture(scope="module")
def results():  # type: ignore[no-untyped-def]
    clock = FakeClock()
    system = MockSystem(
        "bench", MockProfile(matching="exact"), KnowledgeBase.from_tests(TESTS), clock=clock
    )
    runner = EvaluationRunner(
        system, default_evaluator_registry(), settings=RunnerSettings(concurrency=32), clock=clock
    )
    out, elapsed = _timed(
        f"runner throughput ({N} executions)", lambda: run(runner.run("bench", TESTS))
    )
    print(f"  -> {N / elapsed:,.0f} executions/s")
    return out.results


def test_runner_throughput(results) -> None:  # type: ignore[no-untyped-def]
    assert len(results) == N


def test_parquet_write(results, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _, elapsed = _timed("parquet write", lambda: write_results(tmp_path / "r.parquet", results))
    print(f"  -> {(tmp_path / 'r.parquet').stat().st_size / 1024:.0f} KiB")
    assert elapsed < 30


def test_regression_comparison(results) -> None:  # type: ignore[no-untyped-def]
    report, elapsed = _timed(
        "regression comparison", lambda: compare_runs("a", results, "b", results)
    )
    assert report.comparable == N and elapsed < 30


def test_report_generation(results) -> None:  # type: ignore[no-untyped-def]
    summary = summarize("bench", results)
    text, elapsed = _timed(
        "report generation",
        lambda: render_report(
            manifest("bench"), summary, results, plots=sorted(render_plots(summary, results, None))
        ),
    )
    assert "## Overall metrics" in text and elapsed < 30
