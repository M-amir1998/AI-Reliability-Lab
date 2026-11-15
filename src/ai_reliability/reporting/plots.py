"""Small, dependency-free SVG plots. Only charts that answer a release question are produced."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

_W = 640
_FONT = "font-family='system-ui,sans-serif' font-size='12'"
_PASS = "#2f7d4f"
_FAIL = "#b3392f"
_NEUTRAL = "#4a6fa5"


def _svg(height: int, body: str, title: str) -> str:
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{_W}' height='{height}' viewBox='0 0 {_W} {height}'>"
        f"<rect width='100%' height='100%' fill='white'/>"
        f"<text x='10' y='20' {_FONT} font-weight='bold'>{escape(title)}</text>{body}</svg>"
    )


def bar_chart(
    title: str,
    labels: Sequence[str],
    values: Sequence[float],
    *,
    max_value: float = 1.0,
    fmt: str = "{:.0%}",
) -> str:
    """Horizontal bars (e.g. pass rate by category)."""
    row, left, width = 22, 180, _W - 260
    body = []
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        y = 34 + i * row
        length = 0 if max_value <= 0 else max(0.0, min(1.0, value / max_value)) * width
        color = (
            _PASS if value >= 0.95 * max_value else _NEUTRAL if value >= 0.8 * max_value else _FAIL
        )
        body.append(
            f"<text x='{left - 6}' y='{y + 13}' text-anchor='end' {_FONT}>{escape(label)}</text>"
            f"<rect x='{left}' y='{y}' width='{length:.1f}' height='{row - 6}' fill='{color}'/>"
            f"<text x='{left + length + 4:.1f}' y='{y + 13}' {_FONT}>{escape(fmt.format(value))}</text>"
        )
    return _svg(40 + len(labels) * row, "".join(body), title)


def delta_chart(title: str, labels: Sequence[str], deltas: Sequence[float]) -> str:
    """Diverging bars for per-category pass-rate change (regression waterfall)."""
    row, centre, half = 22, _W // 2 + 60, (_W // 2) - 110
    scale = max((abs(d) for d in deltas), default=0.0) or 1.0
    body = [
        f"<line x1='{centre}' y1='28' x2='{centre}' y2='{34 + len(labels) * row}' stroke='#888'/>"
    ]
    for i, (label, delta) in enumerate(zip(labels, deltas, strict=True)):
        y = 34 + i * row
        length = abs(delta) / scale * half
        x = centre if delta >= 0 else centre - length
        color = _PASS if delta > 0 else _FAIL if delta < 0 else "#999"
        body.append(
            f"<text x='{centre - half - 8}' y='{y + 13}' text-anchor='end' {_FONT}>{escape(label)}</text>"
            f"<rect x='{x:.1f}' y='{y}' width='{max(length, 1):.1f}' height='{row - 6}' fill='{color}'/>"
            f"<text x='{centre + (length + 4 if delta >= 0 else 4):.1f}' y='{y + 13}' {_FONT}>{delta:+.1%}</text>"
        )
    return _svg(44 + len(labels) * row, "".join(body), title)


def histogram(title: str, values: Sequence[float], *, bins: int = 20, unit: str = "ms") -> str:
    """Distribution plot (e.g. latency)."""
    if not values:
        return _svg(60, f"<text x='10' y='45' {_FONT}>no data</text>", title)
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / span * bins))] += 1
    peak = max(counts)
    height, base, left = 220, 190, 50
    width = (_W - left - 20) / bins
    body = [
        f"<rect x='{left + i * width:.1f}' y='{base - c / peak * 150:.1f}' width='{width - 2:.1f}' "
        f"height='{c / peak * 150:.1f}' fill='{_NEUTRAL}'/>"
        for i, c in enumerate(counts)
    ]
    body.append(f"<text x='{left}' y='{base + 16}' {_FONT}>{lo:.0f} {unit}</text>")
    body.append(
        f"<text x='{_W - 20}' y='{base + 16}' text-anchor='end' {_FONT}>{hi:.0f} {unit}</text>"
    )
    body.append(f"<text x='{left}' y='40' {_FONT}>n = {len(values)}, max bin = {peak}</text>")
    return _svg(height, "".join(body), title)


def line_chart(title: str, x_labels: Sequence[str], series: dict[str, Sequence[float]]) -> str:
    """Metric trend across runs (e.g. drift over runs). Each series is normalized to its own range."""
    height, left, top, bottom = 240, 50, 40, 200
    n = len(x_labels)
    step = (_W - left - 30) / max(1, n - 1)
    colors = [_NEUTRAL, _FAIL, _PASS, "#8a5a9e", "#c98a1b"]
    body = []
    for idx, (name, values) in enumerate(series.items()):
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        points = " ".join(
            f"{left + i * step:.1f},{bottom - (v - lo) / span * (bottom - top):.1f}"
            for i, v in enumerate(values)
        )
        color = colors[idx % len(colors)]
        body.append(f"<polyline points='{points}' fill='none' stroke='{color}' stroke-width='2'/>")
        body.append(
            f"<text x='{left + 10}' y='{height - 10 - idx * 14}' fill='{color}' {_FONT}>"
            f"{escape(name)} ({lo:.3g} – {hi:.3g})</text>"
        )
    for i, label in enumerate(x_labels):
        body.append(
            f"<text x='{left + i * step:.1f}' y='{bottom + 14}' text-anchor='middle' {_FONT}>{escape(label)}</text>"
        )
    return _svg(height + 20 * max(0, len(series) - 1), "".join(body), title)
