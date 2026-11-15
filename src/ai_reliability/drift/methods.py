"""Lightweight distribution-shift statistics with their assumptions documented.

* PSI (population stability index): binned comparison of two samples. Bins are
  reference quantiles; empty bins are smoothed with ``epsilon``. Rule of thumb:
  < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 major shift. Sensitive to bin
  choice and small samples.
* KS two-sample test: compares continuous distributions; assumes independent
  samples and continuous data (ties from discrete values make it conservative).
* Relative mean / median shift: simple, interpretable effect sizes.
* Categorical PSI: PSI over category frequencies.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

import numpy as np
from scipy import stats as sps


def relative_shift(reference: float, current: float) -> float | None:
    """(current - reference) / |reference|; ``None`` when the reference is zero."""
    if reference == 0:
        return None
    return (current - reference) / abs(reference)


def psi(
    reference: Sequence[float], current: Sequence[float], *, bins: int = 10, epsilon: float = 1e-4
) -> float | None:
    """Population stability index using reference-quantile bins. ``None`` if a sample is empty."""
    if not reference or not current:
        return None
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        # Degenerate reference (constant): compare share equal to that constant.
        ref_share = 1.0
        cur_share = float(np.mean(np.isclose(cur, ref[0])))
        p = np.array([ref_share, 1 - ref_share]) + epsilon
        q = np.array([cur_share, 1 - cur_share]) + epsilon
        return float(np.sum((q - p) * np.log(q / p)))
    edges[0], edges[-1] = -np.inf, np.inf
    p = np.histogram(ref, edges)[0] / len(ref) + epsilon
    q = np.histogram(cur, edges)[0] / len(cur) + epsilon
    return float(np.sum((q - p) * np.log(q / p)))


def categorical_psi(
    reference: Sequence[str], current: Sequence[str], *, epsilon: float = 1e-4
) -> float | None:
    if not reference or not current:
        return None
    ref, cur = Counter(reference), Counter(current)
    total = 0.0
    for key in set(ref) | set(cur):
        p = ref[key] / len(reference) + epsilon
        q = cur[key] / len(current) + epsilon
        total += (q - p) * math.log(q / p)
    return total


def ks_test(reference: Sequence[float], current: Sequence[float]) -> tuple[float, float] | None:
    """(KS statistic, p-value) or ``None`` when either sample has fewer than 5 values."""
    if len(reference) < 5 or len(current) < 5:
        return None
    result = sps.ks_2samp(reference, current)
    return float(result.statistic), float(result.pvalue)
