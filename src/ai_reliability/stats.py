"""Statistics used for reliability comparisons, with assumptions stated per function.

Only methods appropriate for the data at hand are provided:

* Wilson score intervals for binomial pass rates (better coverage than the
  normal approximation at small n or extreme rates);
* exact McNemar test for *paired* binary outcomes (same tests, two systems);
* paired bootstrap for differences in per-test pass rates;
* Cohen's kappa for judge-vs-label agreement.

t-tests are deliberately absent: pass/fail data is not normally distributed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats as sps


def wilson_interval(successes: int, n: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. Returns (0, 1) when n == 0."""
    if n <= 0:
        return 0.0, 1.0
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    z = float(sps.norm.ppf(0.5 + confidence / 2))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    low = 0.0 if successes == 0 else max(0.0, min(p, centre - half))
    high = 1.0 if successes == n else min(1.0, max(p, centre + half))
    return low, high


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value.

    ``b`` = pairs passing in baseline only (regressions), ``c`` = pairs passing in
    candidate only (improvements). Assumes paired, independent test items.
    Returns 1.0 when there are no discordant pairs.
    """
    n = b + c
    if n == 0:
        return 1.0
    return float(sps.binomtest(min(b, c), n, 0.5, alternative="two-sided").pvalue)


def paired_bootstrap_diff(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    iterations: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Paired bootstrap CI for ``mean(candidate - baseline)``.

    Items are resampled jointly, preserving the pairing. Returns (diff, low, high).
    """
    if len(baseline) != len(candidate):
        raise ValueError("paired samples must have equal length")
    if not baseline:
        return 0.0, 0.0, 0.0
    diffs = np.asarray(candidate, dtype=float) - np.asarray(baseline, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diffs), size=(iterations, len(diffs)))
    means = diffs[idx].mean(axis=1)
    alpha = (1 - confidence) / 2
    return (
        float(diffs.mean()),
        float(np.quantile(means, alpha)),
        float(np.quantile(means, 1 - alpha)),
    )


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in [0, 100]); ``None`` for empty input."""
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Cohen's kappa for two binary raters. ``None`` when undefined (no variance)."""
    if len(a) != len(b):
        raise ValueError("rater label sequences must align")
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if math.isclose(expected, 1.0):
        return None
    return (observed - expected) / (1 - expected)
