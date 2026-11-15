"""Evaluate a gate policy against a run summary (and optionally a baseline comparison)."""

from __future__ import annotations

from ai_reliability.domain.enums import GateDecision, RegressionStatus
from ai_reliability.domain.gate import (
    GateOutcome,
    GatePolicy,
    GateResult,
    GateSpec,
    ReleaseDecision,
)
from ai_reliability.domain.regression import ComparisonReport
from ai_reliability.domain.run import RunSummary

COMPARISON_METRICS = frozenset(
    {"regression_rate", "regression_count", "new_failure_count", "fixed_failure_count"}
)


def resolve_metric(
    name: str, summary: RunSummary, comparison: ComparisonReport | None
) -> tuple[float | None, str]:
    """Look up a metric value. Returns (value, why-missing)."""
    if name in COMPARISON_METRICS or name.endswith("_delta"):
        if comparison is None:
            return None, "requires a baseline comparison"
        if name == "regression_rate":
            return comparison.regression_rate, "no comparable tests"
        if name in {"regression_count", "new_failure_count"}:
            return float(comparison.counts.get(RegressionStatus.REGRESSED, 0)), ""
        if name == "fixed_failure_count":
            return float(comparison.counts.get(RegressionStatus.IMPROVED, 0)), ""
        base = name.removesuffix("_delta")
        delta = comparison.metrics.get(base)
        return (None, f"metric {base} not in both runs") if delta is None else (delta.delta, "")
    value = summary.metric(name)
    return value, "" if value is not None else f"metric {name} not available for this run"


def _missing(spec: GateSpec, why: str) -> GateResult:
    outcome = {"fail": GateOutcome.FAIL, "warn": GateOutcome.WARNING, "skip": GateOutcome.SKIPPED}[
        spec.on_missing
    ]
    return GateResult(gate_id=spec.gate_id, outcome=outcome, reason=f"not evaluated: {why}")


def _violation(spec: GateSpec) -> GateOutcome:
    return GateOutcome.FAIL if spec.on_violation == "fail" else GateOutcome.WARNING


def _metric_gate(
    spec: GateSpec, summary: RunSummary, comparison: ComparisonReport | None
) -> GateResult:
    assert spec.metric is not None
    value, why = resolve_metric(spec.metric, summary, comparison)
    if value is None:
        return _missing(spec, why)
    bounds = []
    if spec.min is not None:
        bounds.append(f">= {spec.min:g}")
    if spec.max is not None:
        bounds.append(f"<= {spec.max:g}")
    threshold = " and ".join(bounds)
    if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
        return GateResult(
            gate_id=spec.gate_id,
            outcome=_violation(spec),
            observed=value,
            threshold=threshold,
            reason=f"{spec.metric} = {value:.4g}, required {threshold}",
        )
    return GateResult(
        gate_id=spec.gate_id,
        outcome=GateOutcome.PASS,
        observed=value,
        threshold=threshold,
        reason=f"{spec.metric} = {value:.4g} satisfies {threshold}",
    )


def _category_gate(
    spec: GateSpec, summary: RunSummary, comparison: ComparisonReport | None
) -> GateResult:
    assert spec.category is not None
    cat = summary.categories.get(spec.category)
    if cat is None:
        return _missing(spec, f"no tests in category {spec.category!r}")
    needs_baseline = spec.max_regressions is not None or spec.max_pass_rate_drop is not None
    if needs_baseline and comparison is None and spec.pass_rate_min is None:
        return _missing(spec, "requires a baseline comparison")
    problems: list[str] = []
    if spec.pass_rate_min is not None and (cat.pass_rate or 0.0) < spec.pass_rate_min:
        problems.append(f"pass rate {cat.pass_rate:.3f} < {spec.pass_rate_min:g}")
    delta = comparison.categories.get(spec.category) if comparison is not None else None
    if delta is not None:
        if spec.max_regressions is not None and delta.regressed > spec.max_regressions:
            problems.append(f"{delta.regressed} regression(s) vs baseline > {spec.max_regressions}")
        drop = -(delta.delta or 0.0)
        if spec.max_pass_rate_drop is not None and drop > spec.max_pass_rate_drop:
            problems.append(f"pass rate dropped by {drop:.3f} > {spec.max_pass_rate_drop:g}")
    note = (
        " (regression checks skipped: no baseline)" if needs_baseline and comparison is None else ""
    )
    if problems:
        return GateResult(
            gate_id=spec.gate_id,
            outcome=_violation(spec),
            observed=cat.pass_rate,
            threshold=f"category {spec.category}",
            reason=f"{spec.category}: " + "; ".join(problems) + note,
        )
    if needs_baseline and comparison is not None and delta is None:
        # A baseline exists but no test in this category is comparable (e.g. all redefined):
        # the regression check never ran, so it must not silently pass.
        return _missing(spec, f"no comparable tests in category {spec.category!r} vs baseline")
    return GateResult(
        gate_id=spec.gate_id,
        outcome=GateOutcome.PASS,
        observed=cat.pass_rate,
        threshold=f"category {spec.category}",
        reason=f"{spec.category}: pass rate {cat.pass_rate:.3f}{note}",
    )


def _critical_gate(spec: GateSpec, summary: RunSummary) -> GateResult:
    assert spec.max is not None
    count = len(summary.critical_failures)
    if count > spec.max:
        return GateResult(
            gate_id=spec.gate_id,
            outcome=_violation(spec),
            observed=float(count),
            threshold=f"<= {spec.max:g}",
            reason=f"{count} critical test(s) failed: {', '.join(summary.critical_failures[:5])}",
        )
    return GateResult(
        gate_id=spec.gate_id,
        outcome=GateOutcome.PASS,
        observed=float(count),
        threshold=f"<= {spec.max:g}",
        reason=f"{count} critical failure(s)",
    )


def evaluate_gates(
    policy: GatePolicy, summary: RunSummary, comparison: ComparisonReport | None = None
) -> ReleaseDecision:
    """Apply every gate; FAIL if any gate fails or a CRITICAL test fails (when blocking)."""
    results: list[GateResult] = []
    for spec in policy.gates:
        if spec.kind == "metric":
            results.append(_metric_gate(spec, summary, comparison))
        elif spec.kind == "category":
            results.append(_category_gate(spec, summary, comparison))
        else:
            results.append(_critical_gate(spec, summary))
    blocking = [f"{r.gate_id}: {r.reason}" for r in results if r.outcome is GateOutcome.FAIL]
    warnings = [f"{r.gate_id}: {r.reason}" for r in results if r.outcome is GateOutcome.WARNING]
    if policy.block_on_critical_failure and summary.critical_failures:
        blocking.append(
            "critical-severity override: "
            f"{len(summary.critical_failures)} CRITICAL test(s) failed "
            f"({', '.join(summary.critical_failures[:5])})"
        )
    decision = (
        GateDecision.FAIL if blocking else GateDecision.WARNING if warnings else GateDecision.PASS
    )
    return ReleaseDecision(
        run_id=summary.run_id,
        baseline_run_id=comparison.baseline_run_id if comparison else None,
        policy_id=policy.policy_id,
        decision=decision,
        gates=tuple(results),
        blocking_reasons=tuple(blocking),
        warnings=tuple(warnings),
        critical_failures=summary.critical_failures,
    )
