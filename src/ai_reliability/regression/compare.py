"""Baseline-vs-candidate regression analysis."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ai_reliability.domain.enums import Outcome, RegressionStatus
from ai_reliability.domain.regression import (
    CaseComparison,
    CategoryDelta,
    ComparisonReport,
    MetricDelta,
    StatisticalTest,
)
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.run import RunSummary
from ai_reliability.stats import mcnemar_exact, paired_bootstrap_diff


def classify(baseline_passed: bool | None, candidate_passed: bool | None) -> RegressionStatus:
    """Map a pair of per-test verdicts to a regression status (None = not comparable)."""
    if baseline_passed is None or candidate_passed is None:
        return RegressionStatus.NOT_COMPARABLE
    if baseline_passed and candidate_passed:
        return RegressionStatus.UNCHANGED_PASS
    if not baseline_passed and not candidate_passed:
        return RegressionStatus.UNCHANGED_FAIL
    return RegressionStatus.IMPROVED if candidate_passed else RegressionStatus.REGRESSED


def _group(results: Sequence[CaseResult]) -> dict[str, list[CaseResult]]:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        grouped[r.test_id].append(r)
    return grouped


def _pass_fraction(results: Sequence[CaseResult]) -> float:
    return sum(r.outcome is Outcome.PASS for r in results) / len(results)


def _representative(results: Sequence[CaseResult]) -> Outcome:
    outcomes = {r.outcome for r in results}
    return next(iter(outcomes)) if len(outcomes) == 1 else Outcome.FAIL


def compare_runs(
    baseline_run_id: str,
    baseline: Sequence[CaseResult],
    candidate_run_id: str,
    candidate: Sequence[CaseResult],
    *,
    baseline_summary: RunSummary | None = None,
    candidate_summary: RunSummary | None = None,
    pass_threshold: float = 1.0,
    seed: int = 0,
) -> ComparisonReport:
    """Compare two runs test-by-test.

    A test "passes" in a run when its pass fraction across repetitions is
    ``>= pass_threshold`` (default 1.0: every repetition must pass). Tests are
    NOT_COMPARABLE when missing from either run or when their definition hash
    differs (the test itself changed). System errors count as failures.
    """
    base_by_test, cand_by_test = _group(baseline), _group(candidate)
    cases: list[CaseComparison] = []
    for test_id in sorted(set(base_by_test) | set(cand_by_test)):
        b, c = base_by_test.get(test_id), cand_by_test.get(test_id)
        ref = (c or b or [])[0]
        if b is None or c is None:
            cases.append(
                CaseComparison(
                    test_id=test_id,
                    category=ref.category,
                    severity=ref.severity,
                    status=RegressionStatus.NOT_COMPARABLE,
                    reason="missing in baseline" if b is None else "missing in candidate",
                    baseline_pass_rate=_pass_fraction(b) if b else None,
                    candidate_pass_rate=_pass_fraction(c) if c else None,
                )
            )
            continue
        if {r.test_hash for r in b} != {r.test_hash for r in c}:
            cases.append(
                CaseComparison(
                    test_id=test_id,
                    category=ref.category,
                    severity=ref.severity,
                    status=RegressionStatus.NOT_COMPARABLE,
                    reason="test definition changed between runs",
                )
            )
            continue
        bf, cf = _pass_fraction(b), _pass_fraction(c)
        status = classify(bf >= pass_threshold, cf >= pass_threshold)
        reason = ""
        if status is RegressionStatus.REGRESSED:
            reason = next((r.failure_reason for r in c if r.outcome is not Outcome.PASS), "")
        cases.append(
            CaseComparison(
                test_id=test_id,
                category=ref.category,
                severity=ref.severity,
                status=status,
                baseline_pass_rate=bf,
                candidate_pass_rate=cf,
                baseline_outcome=_representative(b),
                candidate_outcome=_representative(c),
                reason=reason,
            )
        )

    counts = dict.fromkeys(RegressionStatus, 0)
    for case in cases:
        counts[case.status] += 1

    comparable = [c for c in cases if c.status is not RegressionStatus.NOT_COMPARABLE]
    categories: dict[str, CategoryDelta] = {}
    by_cat: dict[str, list[CaseComparison]] = defaultdict(list)
    for case in comparable:
        by_cat[case.category].append(case)
    for category, group in sorted(by_cat.items()):
        bp = sum((x.baseline_pass_rate or 0.0) >= pass_threshold for x in group) / len(group)
        cp = sum((x.candidate_pass_rate or 0.0) >= pass_threshold for x in group) / len(group)
        categories[category] = CategoryDelta(
            category=category,
            n=len(group),
            baseline_pass_rate=bp,
            candidate_pass_rate=cp,
            delta=cp - bp,
            regressed=sum(x.status is RegressionStatus.REGRESSED for x in group),
            improved=sum(x.status is RegressionStatus.IMPROVED for x in group),
        )

    metrics: dict[str, MetricDelta] = {}
    if baseline_summary is not None and candidate_summary is not None:
        for name in sorted(set(baseline_summary.metrics) | set(candidate_summary.metrics)):
            bv, cv = baseline_summary.metric(name), candidate_summary.metric(name)
            delta = None if bv is None or cv is None else cv - bv
            relative = None if delta is None or not bv else delta / abs(bv)
            metrics[name] = MetricDelta(
                name=name, baseline=bv, candidate=cv, delta=delta, relative=relative
            )

    statistics: list[StatisticalTest] = []
    if comparable:
        regressed = counts[RegressionStatus.REGRESSED]
        improved = counts[RegressionStatus.IMPROVED]
        statistics.append(
            StatisticalTest(
                name="mcnemar_exact",
                statistic=float(min(regressed, improved)),
                p_value=mcnemar_exact(regressed, improved),
                n=regressed + improved,
                note="paired binary outcomes on identical tests; assumes independent test items",
            )
        )
        diff, low, high = paired_bootstrap_diff(
            [x.baseline_pass_rate or 0.0 for x in comparable],
            [x.candidate_pass_rate or 0.0 for x in comparable],
            seed=seed,
        )
        statistics.append(
            StatisticalTest(
                name="paired_bootstrap_pass_rate_diff",
                statistic=diff,
                ci_low=low,
                ci_high=high,
                n=len(comparable),
                note="95% CI of mean per-test pass-fraction difference (candidate - baseline)",
            )
        )

    return ComparisonReport(
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        cases=tuple(cases),
        categories=categories,
        metrics=metrics,
        counts=counts,
        statistics=tuple(statistics),
    )
