"""Drift analysis over *evaluation outputs* of historical vs new runs.

Scope: this detects shifts in what the evaluation harness observed (scores,
latency, response length, token counts, error rate, category/failure mix) on
the evaluated test sets. It is NOT production model drift detection: it says
nothing about shifts in real user traffic unless the evaluated inputs are a
representative sample of that traffic.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from enum import StrEnum

from ai_reliability.config.models import DriftThresholds
from ai_reliability.domain.base import DomainModel
from ai_reliability.drift.methods import categorical_psi, ks_test, psi, relative_shift
from ai_reliability.storage.analysis import RunColumns


class DriftStatus(StrEnum):
    STABLE = "stable"
    WARN = "warn"
    DRIFT = "drift"
    INSUFFICIENT_DATA = "insufficient_data"


class FeatureDrift(DomainModel):
    feature: str
    status: DriftStatus
    reference_value: float | None = None
    current_value: float | None = None
    relative_shift: float | None = None
    psi: float | None = None
    ks_statistic: float | None = None
    ks_p_value: float | None = None
    n_reference: int = 0
    n_current: int = 0
    note: str = ""


class DriftReport(DomainModel):
    reference_runs: tuple[str, ...]
    current_runs: tuple[str, ...]
    features: tuple[FeatureDrift, ...]
    scope_note: str = (
        "Evaluation-output drift only; not a substitute for production traffic drift monitoring."
    )

    @property
    def status(self) -> DriftStatus:
        statuses = {f.status for f in self.features}
        for s in (DriftStatus.DRIFT, DriftStatus.WARN, DriftStatus.STABLE):
            if s in statuses:
                return s
        return DriftStatus.INSUFFICIENT_DATA


def _worst(*statuses: DriftStatus) -> DriftStatus:
    order = [DriftStatus.DRIFT, DriftStatus.WARN, DriftStatus.STABLE, DriftStatus.INSUFFICIENT_DATA]
    return next(s for s in order if s in statuses)


def _continuous(
    feature: str,
    ref: Sequence[float],
    cur: Sequence[float],
    t: DriftThresholds,
    *,
    use_median: bool,
) -> FeatureDrift:
    if len(ref) < 5 or len(cur) < 5:
        return FeatureDrift(
            feature=feature,
            status=DriftStatus.INSUFFICIENT_DATA,
            n_reference=len(ref),
            n_current=len(cur),
            note="need at least 5 observations per side",
        )
    centre = statistics.median if use_median else statistics.fmean
    rv, cv = float(centre(ref)), float(centre(cur))
    shift = relative_shift(rv, cv)
    p = psi(ref, cur)
    ks = ks_test(ref, cur)
    by_psi = (
        DriftStatus.DRIFT
        if p is not None and p > t.psi_drift
        else DriftStatus.WARN
        if p is not None and p > t.psi_warn
        else DriftStatus.STABLE
    )
    abs_shift = abs(shift) if shift is not None else 0.0
    by_shift = (
        DriftStatus.DRIFT
        if abs_shift > t.relative_shift_drift
        else DriftStatus.WARN
        if abs_shift > t.relative_shift_warn
        else DriftStatus.STABLE
    )
    # KS alone only escalates to WARN: with large n it flags tiny, irrelevant shifts.
    by_ks = DriftStatus.WARN if ks is not None and ks[1] < t.ks_alpha else DriftStatus.STABLE
    return FeatureDrift(
        feature=feature,
        status=_worst(by_psi, by_shift, by_ks),
        reference_value=rv,
        current_value=cv,
        relative_shift=shift,
        psi=p,
        ks_statistic=ks[0] if ks else None,
        ks_p_value=ks[1] if ks else None,
        n_reference=len(ref),
        n_current=len(cur),
        note=f"{'median' if use_median else 'mean'} shift, PSI over reference quantile bins, KS two-sample",
    )


def _rate(
    feature: str, ref: Sequence[float], cur: Sequence[float], *, warn: float, drift: float
) -> FeatureDrift:
    if not ref or not cur:
        return FeatureDrift(feature=feature, status=DriftStatus.INSUFFICIENT_DATA)
    rv, cv = statistics.fmean(ref), statistics.fmean(cur)
    delta = abs(cv - rv)
    status = (
        DriftStatus.DRIFT
        if delta > drift
        else DriftStatus.WARN
        if delta > warn
        else DriftStatus.STABLE
    )
    return FeatureDrift(
        feature=feature,
        status=status,
        reference_value=rv,
        current_value=cv,
        relative_shift=relative_shift(rv, cv),
        n_reference=len(ref),
        n_current=len(cur),
        note=f"absolute change in rate (warn > {warn}, drift > {drift})",
    )


def _categorical(
    feature: str, ref: Sequence[str], cur: Sequence[str], t: DriftThresholds
) -> FeatureDrift:
    value = categorical_psi(ref, cur)
    if value is None:
        return FeatureDrift(feature=feature, status=DriftStatus.INSUFFICIENT_DATA)
    status = (
        DriftStatus.DRIFT
        if value > t.psi_drift
        else DriftStatus.WARN
        if value > t.psi_warn
        else DriftStatus.STABLE
    )
    return FeatureDrift(
        feature=feature,
        status=status,
        psi=value,
        n_reference=len(ref),
        n_current=len(cur),
        note="PSI over category frequencies",
    )


def analyze_drift(
    reference: RunColumns, current: RunColumns, thresholds: DriftThresholds
) -> DriftReport:
    """Compare evaluation-output distributions of reference runs vs current runs."""
    t = thresholds
    features = (
        _rate("pass_rate", reference.passed, current.passed, warn=0.03, drift=0.08),
        _rate("error_rate", reference.is_error, current.is_error, warn=0.02, drift=0.05),
        _continuous("latency_ms", reference.latency_ms, current.latency_ms, t, use_median=True),
        _continuous(
            "output_tokens", reference.output_tokens, current.output_tokens, t, use_median=False
        ),
        _continuous(
            "response_chars", reference.response_chars, current.response_chars, t, use_median=False
        ),
        _categorical("category_mix", reference.categories, current.categories, t),
        _categorical("failure_mix", reference.failure_categories, current.failure_categories, t),
    )
    return DriftReport(
        reference_runs=reference.run_ids, current_runs=current.run_ids, features=features
    )
