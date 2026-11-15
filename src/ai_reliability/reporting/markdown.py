"""Markdown reports: full evaluation report and short executive release report."""

from __future__ import annotations

from collections.abc import Sequence

from ai_reliability.analysis.failures import cluster_failures
from ai_reliability.domain.enums import GateDecision, Outcome, RegressionStatus
from ai_reliability.domain.gate import GateOutcome, ReleaseDecision
from ai_reliability.domain.regression import ComparisonReport
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.run import RunManifest, RunSummary
from ai_reliability.drift.analyze import DriftReport
from ai_reliability.reporting.plots import bar_chart, delta_chart, histogram

SYNTHETIC_BANNER = (
    "> **SYNTHETIC / MOCK SYSTEM RESULTS** — produced by deterministic mock systems for "
    "demonstration and CI. They say nothing about any real model."
)

_DECISION_ICON = {
    GateDecision.PASS: "PASS",
    GateDecision.WARNING: "WARNING",
    GateDecision.FAIL: "FAIL",
}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _num(value: float | None, unit: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:,.1f}{unit}" if abs(value) >= 10 else f"{value:.4g}{unit}"


def _metric_cell(summary: RunSummary, name: str) -> str:
    m = summary.metrics.get(name)
    if m is None or m.value is None:
        return "n/a"
    if m.unit == "ratio":
        ci = (
            f" (95% CI {m.ci_low:.1%}–{m.ci_high:.1%})"
            if m.ci_low is not None and m.ci_high is not None
            else ""
        )
        return f"{m.value:.1%}{ci}"
    return _num(m.value, f" {m.unit}" if m.unit not in {"", "count"} else "")


def is_synthetic(manifest: RunManifest) -> bool:
    return manifest.system.kind.value in {"mock", "scripted"}


def render_plots(
    summary: RunSummary, results: Sequence[CaseResult], comparison: ComparisonReport | None
) -> dict[str, str]:
    """SVG plots worth looking at: pass rate by category, regression deltas, failure mix, latency."""
    plots: dict[str, str] = {}
    cats = sorted(summary.categories.values(), key=lambda c: c.category)
    if cats:
        plots["pass_rate_by_category.svg"] = bar_chart(
            "Pass rate by category", [c.category for c in cats], [c.pass_rate or 0.0 for c in cats]
        )
    if summary.failure_categories:
        items = sorted(summary.failure_categories.items(), key=lambda kv: -kv[1])
        plots["failure_categories.svg"] = bar_chart(
            "Failures by observed failure type",
            [k for k, _ in items],
            [float(v) for _, v in items],
            max_value=float(max(v for _, v in items)),
            fmt="{:.0f}",
        )
    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    if latencies:
        plots["latency_distribution.svg"] = histogram("End-to-end latency distribution", latencies)
    if comparison is not None and comparison.categories:
        deltas = sorted(comparison.categories.values(), key=lambda d: d.delta or 0.0)
        plots["regression_waterfall.svg"] = delta_chart(
            "Pass-rate change vs baseline by category",
            [d.category for d in deltas],
            [d.delta or 0.0 for d in deltas],
        )
    return plots


def render_report(
    manifest: RunManifest,
    summary: RunSummary,
    results: Sequence[CaseResult],
    *,
    comparison: ComparisonReport | None = None,
    decision: ReleaseDecision | None = None,
    drift: DriftReport | None = None,
    plots: Sequence[str] = (),
) -> str:
    """Full report.md."""
    lines: list[str] = [f"# Evaluation report — `{manifest.run_id}`", ""]
    if is_synthetic(manifest):
        lines += [SYNTHETIC_BANNER, ""]
    if decision is not None:
        lines += [
            f"**Release decision: {_DECISION_ICON[decision.decision]}** (policy `{decision.policy_id}`)",
            "",
        ]
    s = manifest.system
    prompt_ref = (
        f"{s.prompt.prompt_id}@{s.prompt.version} ({(s.prompt.sha256 or '')[:12]})"
        if s.prompt
        else "n/a"
    )
    lines += [
        "## System",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| System | `{s.system_id}` ({s.kind.value}) |",
        f"| Model / revision | {s.model or 'n/a'} / {s.revision or 'n/a'} |",
        f"| Prompt | {prompt_ref} |",
        f"| Temperature / seed | {s.temperature if s.temperature is not None else 'n/a'} / {s.seed if s.seed is not None else 'n/a'} |",
        "",
        "## Test suite and setup",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Suite | `{manifest.suite_id}` v`{manifest.suite_version}` ({manifest.test_count} tests) |",
        f"| Repetitions / seed | {manifest.repetitions} / {manifest.seed} |",
        f"| Executions | {summary.total_executions} |",
        f"| Capture policy | `{manifest.capture_policy.value}` |",
        f"| Config hash / git | `{manifest.config_hash}` / `{manifest.git_sha or 'n/a'}` |",
        f"| Tool version | {manifest.tool_version} |",
        f"| Evaluators | {', '.join(f'{k}@{v}' for k, v in sorted(manifest.evaluator_versions.items()))} |",
        "",
        "## Overall metrics",
        "",
        "| Metric | Value | Definition |",
        "|---|---|---|",
    ]
    order = (
        "pass_rate",
        "verified_accuracy",
        "test_pass_rate",
        "critical_failure_rate",
        "system_error_rate",
        "timeout_rate",
        "needs_review_rate",
        "retry_rate",
        "consistency_rate",
        "structured_output_validity",
        "injection_resistance_rate",
        "citation_validity",
        "tool_policy_compliance",
        "groundedness_rate",
    )
    for name in order:
        if name in summary.metrics:
            lines.append(
                f"| `{name}` | {_metric_cell(summary, name)} | {summary.metrics[name].description} |"
            )
    lines += [
        "",
        "## Category metrics",
        "",
        "| Category | Tests | Pass | Fail | Error | Pass rate (95% CI) |",
        "|---|---|---|---|---|---|",
    ]
    for c in summary.categories.values():
        ci = (
            f" ({c.ci_low:.0%}–{c.ci_high:.0%})"
            if c.ci_low is not None and c.ci_high is not None
            else ""
        )
        lines.append(
            f"| {c.category} | {c.total} | {c.passed} | {c.failed} | {c.errors} | {_pct(c.pass_rate)}{ci} |"
        )

    safety = [
        c
        for c in summary.categories.values()
        if c.category
        in {
            "prompt_injection",
            "jailbreak",
            "privacy",
            "safety",
            "instruction_hierarchy",
            "tool_use",
            "resource_abuse",
        }
    ]
    lines += ["", "## Safety", ""]
    if safety:
        for c in safety:
            lines.append(f"- **{c.category}**: {c.passed}/{c.total} passed ({_pct(c.pass_rate)})")
    else:
        lines.append("No safety categories in this suite.")
    if summary.critical_failures:
        lines += [
            "",
            f"**Critical failures ({len(summary.critical_failures)}):** "
            + ", ".join(f"`{t}`" for t in summary.critical_failures),
        ]

    lines += ["", "## Regressions", ""]
    if comparison is None:
        lines.append("No baseline comparison for this run.")
    else:
        counts = comparison.counts
        lines += [
            f"Baseline `{comparison.baseline_run_id}` → candidate `{comparison.candidate_run_id}`.",
            "",
            "| Status | Tests |",
            "|---|---|",
            *(f"| {st.value} | {counts.get(st, 0)} |" for st in RegressionStatus),
            "",
            f"Regression rate: {_pct(comparison.regression_rate)}",
            "",
            "| Category | n | Baseline | Candidate | Δ | Regressed | Improved |",
            "|---|---|---|---|---|---|---|",
        ]
        for d in comparison.categories.values():
            lines.append(
                f"| {d.category} | {d.n} | {_pct(d.baseline_pass_rate)} | {_pct(d.candidate_pass_rate)} | "
                f"{'n/a' if d.delta is None else f'{d.delta:+.1%}'} | {d.regressed} | {d.improved} |"
            )
        if comparison.new_failures:
            lines += ["", "**New failures:**", ""]
            reasons = {c.test_id: c.reason for c in comparison.cases}
            lines += [f"- `{t}` — {reasons.get(t, '')}" for t in comparison.new_failures]
        if comparison.fixed_failures:
            lines += [
                "",
                "**Fixed failures:** " + ", ".join(f"`{t}`" for t in comparison.fixed_failures),
            ]
        for st in comparison.statistics:
            if st.p_value is not None:
                lines.append(
                    f"\n- {st.name}: statistic={st.statistic}, p={st.p_value:.3g}, n={st.n} — {st.note}"
                )
            else:
                lines.append(
                    f"\n- {st.name}: {st.statistic:+.3f} [{st.ci_low:+.3f}, {st.ci_high:+.3f}], n={st.n} — {st.note}"
                )

    lines += ["", "## Failures", ""]
    clusters = cluster_failures(results)
    if not clusters:
        lines.append("No failures.")
    else:
        lines += [
            "Observed failures grouped by category, failure type, evaluator and reason signature "
            "(no root cause is implied).",
            "",
            "| Cluster | Category | Failure type | Evaluator | Count | Example tests |",
            "|---|---|---|---|---|---|",
        ]
        for cl in clusters[:20]:
            lines.append(
                f"| {cl.cluster_id} | {cl.category} | {cl.failure_category} | {cl.evaluator} | {cl.count} | "
                + ", ".join(f"`{t}`" for t in cl.test_ids[:3])
                + " |"
            )

    lines += ["", "## Quality gates", ""]
    if decision is None:
        lines.append("No gate policy applied.")
    else:
        lines += ["| Gate | Outcome | Observed | Reason |", "|---|---|---|---|"]
        for g in decision.gates:
            lines.append(
                f"| `{g.gate_id}` | {g.outcome.value.upper()} | {_num(g.observed)} | {g.reason} |"
            )
        for reason in decision.blocking_reasons:
            if reason.startswith("critical-severity override"):
                lines.append(f"\n**{reason}**")

    lines += [
        "",
        "## Latency",
        "",
        f"p50 {_metric_cell(summary, 'p50_latency_ms')}, p95 {_metric_cell(summary, 'p95_latency_ms')}, "
        f"p99 {_metric_cell(summary, 'p99_latency_ms')} (end-to-end, as reported by the system or measured by the runner; "
        "no breakdown is fabricated).",
        "",
        "## Cost",
        "",
    ]
    if "total_cost_usd" in summary.metrics:
        lines.append(
            f"Estimated total cost {_metric_cell(summary, 'total_cost_usd')} from configured pricing."
        )
    else:
        lines.append("No pricing configured or no token usage reported; cost not estimated.")
    lines += ["", "## Drift", ""]
    if drift is None:
        lines.append("No drift analysis attached to this report.")
    else:
        lines += [
            f"Overall: **{drift.status.value}**. {drift.scope_note}",
            "",
            "| Feature | Status | Reference | Current | PSI | KS p |",
            "|---|---|---|---|---|---|",
        ]
        for f in drift.features:
            lines.append(
                f"| {f.feature} | {f.status.value} | {_num(f.reference_value)} | {_num(f.current_value)} | "
                f"{_num(f.psi)} | {_num(f.ks_p_value)} |"
            )
    if plots:
        lines += ["", "## Plots", "", *(f"![{p}](plots/{p})" for p in plots)]
    lines += [
        "",
        "## Limitations",
        "",
        "- Deterministic evaluators check what they are specified to check; lexical groundedness and refusal "
        "detection are heuristics, not semantic judgements.",
        "- Pass rates are over this suite only and carry the sampling uncertainty shown in the confidence intervals.",
        "- Red-team coverage is a fixed catalogue of attack families, not proof of robustness to unseen attacks.",
        "- Response text is retained only per the capture policy and is redacted best-effort.",
    ]
    if is_synthetic(manifest):
        lines.append(
            "- These results come from a mock system and must not be read as model performance."
        )
    return "\n".join(lines) + "\n"


def render_release_report(
    manifest: RunManifest,
    summary: RunSummary,
    decision: ReleaseDecision,
    comparison: ComparisonReport | None = None,
) -> str:
    """Short executive release-report.md."""
    lines = [f"# Release report — `{manifest.system.system_id}`", ""]
    if is_synthetic(manifest):
        lines += [SYNTHETIC_BANNER, ""]
    lines += [f"## Decision: **{_DECISION_ICON[decision.decision]}**", ""]
    if decision.blocking_reasons:
        lines += ["### Blocking", "", *(f"- {r}" for r in decision.blocking_reasons), ""]
    if decision.warnings:
        lines += ["### Warnings", "", *(f"- {w}" for w in decision.warnings), ""]
    passed = [g for g in decision.gates if g.outcome is GateOutcome.PASS]
    lines += [f"Gates passed: {len(passed)}/{len(decision.gates)}", ""]
    if comparison is not None:
        lines += [
            f"### Changes vs baseline `{comparison.baseline_run_id}`",
            "",
            f"- New failures (regressions): **{len(comparison.new_failures)}**",
            f"- Fixed failures: {len(comparison.fixed_failures)}",
            f"- Unchanged failures: {len(comparison.unchanged_failures)}",
            "",
            "| Metric | Baseline | Candidate | Δ |",
            "|---|---|---|---|",
        ]
        for name in (
            "pass_rate",
            "verified_accuracy",
            "injection_resistance_rate",
            "tool_policy_compliance",
            "critical_failure_rate",
            "p95_latency_ms",
        ):
            d = comparison.metrics.get(name)
            if d is None:
                continue
            is_ratio = name.endswith(("rate", "accuracy", "compliance"))
            fmt = _pct if is_ratio else _num
            delta = (
                "n/a"
                if d.delta is None
                else (f"{d.delta * 100:+.1f} pp" if is_ratio else f"{d.delta:+.1f}")
            )
            lines.append(f"| `{name}` | {fmt(d.baseline)} | {fmt(d.candidate)} | {delta} |")
        lines += ["", "| Category | Baseline | Candidate | Δ |", "|---|---|---|---|"]
        for cd in sorted(comparison.categories.values(), key=lambda x: x.delta or 0.0):
            lines.append(
                f"| {cd.category} | {_pct(cd.baseline_pass_rate)} | {_pct(cd.candidate_pass_rate)} | "
                f"{'n/a' if cd.delta is None else f'{cd.delta * 100:+.1f} pp'} |"
            )
        if comparison.new_failures:
            lines += [
                "",
                "New failures: " + ", ".join(f"`{t}`" for t in comparison.new_failures[:15]),
            ]
    if summary.critical_failures:
        lines += [
            "",
            "Critical failures: " + ", ".join(f"`{t}`" for t in summary.critical_failures),
        ]
    lines += ["", f"Full details: `report.md` in `results/{manifest.run_id}/`."]
    return "\n".join(lines) + "\n"


def failing_outcomes(results: Sequence[CaseResult]) -> int:
    return sum(r.outcome is not Outcome.PASS for r in results)
