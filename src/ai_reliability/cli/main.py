"""`aireliability` command-line interface. Thin layer over application services."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from ai_reliability import __version__
from ai_reliability.config.loader import load_model
from ai_reliability.config.models import DriftConfig, RedTeamConfig, RunConfig, RunnerSettings
from ai_reliability.domain.enums import GateDecision, RegressionStatus, TestStatus, TracePrivacy
from ai_reliability.errors import ReliabilityError
from ai_reliability.runner.observer import NullObserver, RunObserver

if TYPE_CHECKING:
    from ai_reliability.services.workspace import Workspace

T = TypeVar("T")

app = typer.Typer(
    help="AI Reliability Lab - evaluation, regression gates and red-teaming for LLM systems.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
systems_app = typer.Typer(help="Inspect registered systems under test.", no_args_is_help=True)
suites_app = typer.Typer(help="Inspect test suites.", no_args_is_help=True)
gates_app = typer.Typer(help="Evaluate quality gates.", no_args_is_help=True)
baseline_app = typer.Typer(
    help="Declare and inspect baselines (append-only).", no_args_is_help=True
)
runs_app = typer.Typer(help="List and verify stored runs.", no_args_is_help=True)
redteam_app = typer.Typer(help="Run red-team campaigns.", no_args_is_help=True)
incidents_app = typer.Typer(help="Incident-to-eval workflow.", no_args_is_help=True)
drift_app = typer.Typer(help="Evaluation-output drift analysis.", no_args_is_help=True)
review_app = typer.Typer(help="Human review workflow.", no_args_is_help=True)
traces_app = typer.Typer(help="Trace import and analysis.", no_args_is_help=True)
compare_app = typer.Typer(help="Canary and shadow comparisons.", no_args_is_help=True)
metrics_app = typer.Typer(help="Prometheus exposition of stored results.", no_args_is_help=True)
demo_app = typer.Typer(help="Offline demos (synthetic / mock systems only).", no_args_is_help=True)
for name, sub in (
    ("systems", systems_app),
    ("suites", suites_app),
    ("gates", gates_app),
    ("baseline", baseline_app),
    ("runs", runs_app),
    ("redteam", redteam_app),
    ("incidents", incidents_app),
    ("drift", drift_app),
    ("review", review_app),
    ("traces", traces_app),
    ("canary", compare_app),
    ("metrics", metrics_app),
    ("demo", demo_app),
):
    app.add_typer(sub, name=name)

console = Console()
RootOpt = Annotated[
    Path, typer.Option("--root", help="Project root (configs/, results/, incidents/).")
]
ResultsOpt = Annotated[
    Path | None, typer.Option("--results", help="Results directory (default <root>/results).")
]


def _workspace(root: Path) -> Workspace:
    from ai_reliability.services.workspace import Workspace

    return Workspace(root)


def _fail(message: str, code: int = 1) -> typer.Exit:
    console.print(f"[red]error:[/red] {message}")
    return typer.Exit(code)


def _run(coro: Coroutine[Any, Any, T]) -> T:
    try:
        return asyncio.run(coro)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.4g}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1%}"


class _ProgressObserver(NullObserver):
    def __init__(self) -> None:
        self.done = 0
        self.total = 0

    def on_run_started(self, run_id: str, total: int, skipped: int) -> None:
        self.total, self.done = total, skipped
        console.print(
            f"run [bold]{run_id}[/bold]: {total} executions ({skipped} resumed from checkpoint)"
        )

    def on_case_finished(self, run_id, result, in_flight) -> None:  # type: ignore[no-untyped-def]
        self.done += 1
        if self.done % 25 == 0 or self.done == self.total:
            console.print(f"  {self.done}/{self.total} done", highlight=False)


def _observer(metrics_port: int | None, system: str, suite: str) -> RunObserver:
    progress = _ProgressObserver()
    if metrics_port is None:
        return progress
    from ai_reliability.observability.metrics import PrometheusObserver
    from ai_reliability.runner.observer import FanOutObserver

    prom = PrometheusObserver(system_id=system, suite_id=suite)
    prom.serve(metrics_port)
    console.print(f"Prometheus metrics on http://127.0.0.1:{metrics_port}/metrics")
    return FanOutObserver([progress, prom])


def _print_decision(decision) -> None:  # type: ignore[no-untyped-def]
    color = {GateDecision.PASS: "green", GateDecision.WARNING: "yellow", GateDecision.FAIL: "red"}[
        decision.decision
    ]
    console.print(
        f"\nRelease decision: [bold {color}]{decision.decision.value.upper()}[/bold {color}]"
    )
    table = Table("gate", "outcome", "reason")
    for g in decision.gates:
        table.add_row(g.gate_id, g.outcome.value, g.reason)
    console.print(table)
    for reason in decision.blocking_reasons:
        console.print(f"  [red]blocking:[/red] {reason}")
    for warning in decision.warnings:
        console.print(f"  [yellow]warning:[/yellow] {warning}")


def _print_artifacts(art) -> None:  # type: ignore[no-untyped-def]
    if art.summary is None:
        console.print(
            f"[yellow]Run {art.manifest.run_id} interrupted; resume with `aireliability resume "
            f"{art.manifest.run_id}`[/yellow]"
        )
        return
    s = art.summary
    console.print(f"\nRun [bold]{art.manifest.run_id}[/bold] -> {art.run_dir}")
    console.print(
        f"pass rate {_pct(s.metric('pass_rate'))} over {s.total_executions} executions; "
        f"critical failures: {len(s.critical_failures)}; p95 latency {s.metric('p95_latency_ms') or 0:.0f} ms"
    )
    table = Table("category", "pass", "total", "pass rate")
    for c in s.categories.values():
        table.add_row(c.category, str(c.passed), str(c.total), _pct(c.pass_rate))
    console.print(table)
    if art.comparison is not None:
        counts = art.comparison.counts
        console.print(
            "vs baseline: " + ", ".join(f"{k.value}={counts.get(k, 0)}" for k in RegressionStatus)
        )
    if art.decision is not None:
        _print_decision(art.decision)


@app.callback(invoke_without_command=True)
def main(version: Annotated[bool, typer.Option("--version", help="Show version.")] = False) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit


@app.command()
def doctor(root: RootOpt = Path()) -> None:
    """Report Python, CPU, RAM, disk, Docker, DuckDB and optional integrations."""
    from ai_reliability.services.doctor import run_checks

    table = Table("check", "status", "detail")
    for check in run_checks(root.resolve()):
        color = {"ok": "green", "warn": "yellow"}.get(check.status, "cyan")
        table.add_row(check.name, f"[{color}]{check.status}[/{color}]", check.detail)
    console.print(table)


# -- systems / suites ----------------------------------------------------------
@systems_app.command("list")
def systems_list(root: RootOpt = Path()) -> None:
    table = Table("system_id", "type", "source", "description")
    for config, source in _workspace(root).systems.entries():
        table.add_row(config.system_id, config.type, source, config.description)
    console.print(table)


@systems_app.command("inspect")
def systems_inspect(system: str, root: RootOpt = Path()) -> None:
    ws = _workspace(root)
    try:
        config = ws.systems.resolve(system)
        console.print_json(ws.systems.descriptor(config).model_dump_json())
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc


@suites_app.command("list")
def suites_list(root: RootOpt = Path()) -> None:
    ws = _workspace(root)
    table = Table("suite", "tests", "description")
    for suite_id, description in ws.suites.entries():
        try:
            n = str(len(ws.suites.get(suite_id)))
        except ReliabilityError:
            n = "?"
        table.add_row(suite_id, n, description)
    console.print(table)


@suites_app.command("inspect")
def suites_inspect(
    suite: str, root: RootOpt = Path(), show_tests: Annotated[bool, typer.Option("--tests")] = False
) -> None:
    ws = _workspace(root)
    try:
        s = ws.suites.resolve(suite)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(
        f"[bold]{s.suite_id}[/bold] v{s.version} tier={s.tier} tests={len(s)}\n{s.description}"
    )
    by_cat: dict[str, int] = {}
    for t in s:
        by_cat[t.category] = by_cat.get(t.category, 0) + 1
    console.print(", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())))
    if show_tests:
        table = Table("test_id", "category", "severity", "status", "evaluators")
        for t in s:
            table.add_row(
                t.test_id,
                t.category,
                t.severity.value,
                t.metadata.status.value,
                ",".join(t.evaluators),
            )
        console.print(table)


# -- run / resume / compare / gates -----------------------------------------------
@app.command()
def run(
    config: Path,
    root: RootOpt = Path(),
    metrics_port: Annotated[
        int | None, typer.Option(help="Expose Prometheus metrics during the run.")
    ] = None,
    fail_on_gate: Annotated[
        bool, typer.Option(help="Exit 2 when the release decision is FAIL.")
    ] = False,
) -> None:
    """Execute an evaluation run from a YAML RunConfig."""
    from ai_reliability.services.evaluation import run_evaluation

    ws = _workspace(root)
    try:
        cfg = load_model(ws.resolve_path(str(config)), RunConfig)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    art = _run(run_evaluation(ws, cfg, observer=_observer(metrics_port, cfg.system, cfg.suite)))
    _print_artifacts(art)
    if fail_on_gate and art.decision is not None and art.decision.decision is GateDecision.FAIL:
        raise typer.Exit(2)


@app.command()
def resume(run_id: str, root: RootOpt = Path(), results: ResultsOpt = None) -> None:
    """Resume an interrupted run without repeating completed executions."""
    from ai_reliability.services.evaluation import resume_evaluation

    ws = _workspace(root)
    _print_artifacts(
        _run(resume_evaluation(ws, run_id, results_dir=results, observer=_ProgressObserver()))
    )


@app.command()
def compare(
    baseline: str,
    candidate: str,
    root: RootOpt = Path(),
    results: ResultsOpt = None,
    gates: Annotated[
        Path | None, typer.Option(help="Gate policy to apply to the candidate.")
    ] = None,
) -> None:
    """Compare two completed runs (writes results/comparisons/<baseline>__<candidate>/)."""
    from ai_reliability.gates.config import load_gate_policy
    from ai_reliability.gates.engine import evaluate_gates
    from ai_reliability.regression.compare import compare_runs
    from ai_reliability.reporting.markdown import render_release_report
    from ai_reliability.storage.parquet import write_comparisons

    ws = _workspace(root)
    store = ws.store(results)
    try:
        report = compare_runs(
            baseline,
            store.load_results(baseline),
            candidate,
            store.load_results(candidate),
            baseline_summary=store.load_summary(baseline),
            candidate_summary=store.load_summary(candidate),
        )
        out = store.root / "comparisons" / f"{baseline}__{candidate}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "comparison.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        write_comparisons(out / "regressions.parquet", report.cases)
        table = Table("category", "n", "baseline", "candidate", "delta", "regressed", "improved")
        for d in report.categories.values():
            table.add_row(
                d.category,
                str(d.n),
                _pct(d.baseline_pass_rate),
                _pct(d.candidate_pass_rate),
                "n/a" if d.delta is None else f"{d.delta:+.1%}",
                str(d.regressed),
                str(d.improved),
            )
        console.print(table)
        console.print("counts: " + ", ".join(f"{k.value}={v}" for k, v in report.counts.items()))
        if report.new_failures:
            console.print("[red]new failures:[/red] " + ", ".join(report.new_failures))
        if report.fixed_failures:
            console.print("[green]fixed:[/green] " + ", ".join(report.fixed_failures))
        for stat in report.statistics:
            console.print(
                f"{stat.name}: statistic={stat.statistic} p={stat.p_value} CI=[{stat.ci_low}, {stat.ci_high}]"
            )
        if gates is not None:
            decision = evaluate_gates(
                load_gate_policy(ws.resolve_path(str(gates))), store.load_summary(candidate), report
            )
            (out / "gates.json").write_text(decision.model_dump_json(indent=2), encoding="utf-8")
            (out / "release-report.md").write_text(
                render_release_report(
                    store.load_manifest(candidate), store.load_summary(candidate), decision, report
                ),
                encoding="utf-8",
            )
            _print_decision(decision)
        console.print(f"written to {out}")
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc


@gates_app.command("check")
def gates_check(
    run_id: str,
    gates: Annotated[Path, typer.Option(help="Gate policy YAML.")],
    baseline: Annotated[
        str | None, typer.Option(help="Baseline run ID or baseline:<name>.")
    ] = None,
    root: RootOpt = Path(),
    results: ResultsOpt = None,
    strict: Annotated[bool, typer.Option(help="Exit 2 on FAIL, 0 otherwise.")] = True,
) -> None:
    """Apply a gate policy to a stored run (optionally against a baseline)."""
    from ai_reliability.gates.config import load_gate_policy
    from ai_reliability.gates.engine import evaluate_gates
    from ai_reliability.regression.compare import compare_runs
    from ai_reliability.services.evaluation import resolve_baseline

    ws = _workspace(root)
    store = ws.store(results)
    try:
        summary = store.load_summary(run_id)
        comparison = None
        if baseline:
            base_id = resolve_baseline(ws, baseline, store.root)
            comparison = compare_runs(
                base_id,
                store.load_results(base_id),
                run_id,
                store.load_results(run_id),
                baseline_summary=store.load_summary(base_id),
                candidate_summary=summary,
            )
        decision = evaluate_gates(
            load_gate_policy(ws.resolve_path(str(gates))), summary, comparison
        )
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    _print_decision(decision)
    if strict and decision.decision is GateDecision.FAIL:
        raise typer.Exit(2)


@baseline_app.command("set")
def baseline_set(
    run_id: str,
    name: Annotated[str, typer.Option()] = "default",
    note: str = "",
    root: RootOpt = Path(),
    results: ResultsOpt = None,
) -> None:
    """Declare a completed run as the baseline NAME (history is kept, never overwritten)."""
    try:
        record = _workspace(root).index(results).declare_baseline(name, run_id, note or None)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"baseline '{record.name}' -> {record.run_id} ({record.declared_at})")


@baseline_app.command("show")
def baseline_show(
    name: Annotated[str, typer.Argument(help="Baseline name.")] = "default",
    root: RootOpt = Path(),
    results: ResultsOpt = None,
) -> None:
    for rec in _workspace(root).index(results).baseline_history(name):
        console.print(f"{rec.declared_at}  {rec.run_id}  {rec.note or ''}")


@runs_app.command("list")
def runs_list(root: RootOpt = Path(), results: ResultsOpt = None, limit: int = 20) -> None:
    table = Table("run_id", "status", "system", "suite", "pass rate", "decision")
    for r in _workspace(root).index(results).list_runs(limit):
        table.add_row(
            r.run_id, r.status.value, r.system_id, r.suite_id, _pct(r.pass_rate), r.decision or "-"
        )
    console.print(table)


@runs_app.command("verify")
def runs_verify(run_id: str, root: RootOpt = Path(), results: ResultsOpt = None) -> None:
    """Check artifact checksums of a completed run."""
    try:
        _workspace(root).store(results).verify(run_id)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"[green]{run_id}: all artifacts intact[/green]")


@app.command()
def report(
    run_id: str,
    root: RootOpt = Path(),
    results: ResultsOpt = None,
    output: Annotated[
        Path | None, typer.Option(help="Output directory (default <root>/reports/<run_id>).")
    ] = None,
    drift: Annotated[Path | None, typer.Option(help="Drift config to include.")] = None,
) -> None:
    """(Re)generate report.md / report.html (+ release-report.md) for a stored run."""
    from ai_reliability.drift.analyze import analyze_drift
    from ai_reliability.reporting.html import markdown_to_html
    from ai_reliability.reporting.markdown import render_plots, render_release_report, render_report
    from ai_reliability.storage.analysis import load_columns

    ws = _workspace(root)
    store = ws.store(results)
    try:
        manifest, summary, rows = (
            store.load_manifest(run_id),
            store.load_summary(run_id),
            store.load_results(run_id),
        )
        comparison, decision = store.load_comparison(run_id), store.load_decision(run_id)
        drift_report = None
        if drift is not None:
            dcfg = load_model(ws.resolve_path(str(drift)), DriftConfig)
            rdir = ws.resolve_path(dcfg.results_dir)
            drift_report = analyze_drift(
                load_columns(rdir, dcfg.reference_runs),
                load_columns(rdir, (dcfg.current_run,)),
                dcfg.thresholds,
            )
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    out = output or ws.root / "reports" / run_id
    (out / "plots").mkdir(parents=True, exist_ok=True)
    plots = render_plots(summary, rows, comparison)
    for name, svg in plots.items():
        (out / "plots" / name).write_text(svg, encoding="utf-8")
    text = render_report(
        manifest,
        summary,
        rows,
        comparison=comparison,
        decision=decision,
        drift=drift_report,
        plots=sorted(plots),
    )
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "report.html").write_text(markdown_to_html(text, f"Report {run_id}"), encoding="utf-8")
    if decision is not None:
        (out / "release-report.md").write_text(
            render_release_report(manifest, summary, decision, comparison), "utf-8"
        )
    console.print(f"report written to {out}")


# -- red team -------------------------------------------------------------------
@redteam_app.command("run")
def redteam_run(config: Path, root: RootOpt = Path()) -> None:
    """Generate and execute a red-team campaign; prints attack success by family."""
    from ai_reliability.clock import SystemClock
    from ai_reliability.redteam.engine import RedTeamEngine
    from ai_reliability.runner.executor import EvaluationRunner
    from ai_reliability.systems.mock.knowledge import KnowledgeBase

    ws = _workspace(root)
    try:
        cfg = load_model(ws.resolve_path(str(config)), RedTeamConfig)
        sys_cfg = ws.systems.resolve(cfg.system)
        from ai_reliability.redteam.catalog import build_redteam_suite

        suite = build_redteam_suite(cfg.families, seed=cfg.seed, canary=cfg.canary)
        clock = SystemClock()
        system = ws.systems.build(sys_cfg, knowledge=KnowledgeBase.from_tests(suite), clock=clock)
        runner = EvaluationRunner(system, ws.evaluators(), settings=cfg.runner, clock=clock)
        run_id = f"redteam-{clock.now():%Y%m%dT%H%M%S}-{sys_cfg.system_id.replace('/', '-')}"
        _, rt = _run(RedTeamEngine(runner, sys_cfg.system_id).run(run_id, suite))
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    table = Table("family", "attacks", "resisted", "succeeded", "errors", "ASR", "canary leaks")
    for f in rt.families.values():
        table.add_row(
            f.family,
            str(f.attacks),
            str(f.resisted),
            str(f.succeeded),
            str(f.errors),
            _pct(f.attack_success_rate),
            str(f.canary_leaks),
        )
    console.print(table)
    console.print(
        f"overall attack success rate: {_pct(rt.attack_success_rate)} ({rt.succeeded}/{rt.attacks})"
    )
    out = ws.resolve_path(cfg.output_dir) / "redteam"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_id}.json").write_text(rt.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"report: {out / f'{run_id}.json'}")
    if rt.attack_success_rate is not None and rt.attack_success_rate > cfg.max_attack_success_rate:
        raise typer.Exit(2)


# -- incidents ------------------------------------------------------------------
@incidents_app.command("list")
def incidents_list(root: RootOpt = Path()) -> None:
    ws = _workspace(root)
    table = Table("incident", "stage")
    for incident_id, stage in ws.incidents.list_incidents():
        table.add_row(incident_id, stage)
    console.print(table)
    tests = ws.incidents.list_tests()
    if tests:
        t2 = Table("incident test", "status", "severity", "reviewer")
        for t in tests:
            t2.add_row(
                t.test_id, t.metadata.status.value, t.severity.value, t.metadata.reviewer or "-"
            )
        console.print(t2)


@incidents_app.command("add")
def incidents_add(path: Path, root: RootOpt = Path()) -> None:
    """Register a raw incident YAML (stored under incidents/raw, which is git-ignored)."""
    from ai_reliability.domain.incident import Incident

    ws = _workspace(root)
    try:
        incident = load_model(path, Incident)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"stored {ws.incidents.add_raw(incident)}")


@incidents_app.command("sanitize")
def incidents_sanitize(
    incident_id: str,
    root: RootOpt = Path(),
    redact: Annotated[list[str] | None, typer.Option(help="Extra literal to redact.")] = None,
) -> None:
    try:
        incident = _workspace(root).incidents.sanitize(
            incident_id, extra_literals=tuple(redact or ())
        )
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(
        f"sanitized {incident_id}: redactions={incident.sanitization.redactions} "
        "(manual review still required)"
    )


@incidents_app.command("convert")
def incidents_convert(incident_id: str, root: RootOpt = Path()) -> None:
    """Produce a DRAFT candidate regression test (not committed, not active)."""
    try:
        test, path = _workspace(root).incidents.convert(incident_id)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(
        f"DRAFT test {test.test_id} written to {path}\nevaluators: {', '.join(test.evaluators)}\n"
        "Review it, then: aireliability incidents review/activate"
    )


@incidents_app.command("review")
def incidents_review(
    test_id: str,
    reviewer: Annotated[str, typer.Option(help="Pseudonymous reviewer id.")],
    root: RootOpt = Path(),
) -> None:
    try:
        test = _workspace(root).incidents.set_status(
            test_id, TestStatus.REVIEWED, reviewer=reviewer
        )
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"{test.test_id}: {test.metadata.status.value}")


@incidents_app.command("activate")
def incidents_activate(test_id: str, root: RootOpt = Path()) -> None:
    try:
        test = _workspace(root).incidents.set_status(test_id, TestStatus.ACTIVE)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    console.print(
        f"{test.test_id}: {test.metadata.status.value} (gated via suites incident_regressions and release_full)"
    )


# -- drift ------------------------------------------------------------------------
@drift_app.command("analyze")
def drift_analyze(config: Path, root: RootOpt = Path()) -> None:
    from ai_reliability.drift.analyze import analyze_drift
    from ai_reliability.storage.analysis import load_columns

    ws = _workspace(root)
    try:
        cfg = load_model(ws.resolve_path(str(config)), DriftConfig)
        rdir = ws.resolve_path(cfg.results_dir)
        rep = analyze_drift(
            load_columns(rdir, cfg.reference_runs),
            load_columns(rdir, (cfg.current_run,)),
            cfg.thresholds,
        )
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    _print_drift(rep)


def _print_drift(rep) -> None:  # type: ignore[no-untyped-def]
    table = Table("feature", "status", "reference", "current", "rel. shift", "PSI", "KS p")
    fmt = _fmt
    for f in rep.features:
        table.add_row(
            f.feature,
            f.status.value,
            fmt(f.reference_value),
            fmt(f.current_value),
            "n/a" if f.relative_shift is None else f"{f.relative_shift:+.1%}",
            fmt(f.psi),
            fmt(f.ks_p_value),
        )
    console.print(table)
    console.print(f"overall: [bold]{rep.status.value}[/bold] - {rep.scope_note}")


# -- human review ---------------------------------------------------------------------
@review_app.command("queue")
def review_queue(
    run_id: str,
    root: RootOpt = Path(),
    results: ResultsOpt = None,
    include_failures: Annotated[bool, typer.Option(help="Also queue FAIL cases.")] = False,
) -> None:
    from ai_reliability.human_review.review import ReviewStore, build_queue

    ws = _workspace(root)
    store = ws.store(results)
    try:
        items = build_queue(store.load_results(run_id), include_failures=include_failures)
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    ReviewStore(store.root / "reviews", run_id).write_queue(items)
    console.print(f"{len(items)} item(s) queued for review")


@review_app.command("pending")
def review_pending(run_id: str, root: RootOpt = Path(), results: ResultsOpt = None) -> None:
    from ai_reliability.human_review.review import ReviewStore

    store = _workspace(root).store(results)
    for item in ReviewStore(store.root / "reviews", run_id).pending():
        console.print(f"[bold]{item.item_id}[/bold] {item.test_id}: {item.reason}")
        if item.response_excerpt:
            console.print(f"  {item.response_excerpt[:300]}", highlight=False, markup=False)


@review_app.command("label")
def review_label(
    run_id: str,
    item_id: str,
    verdict: Annotated[str, typer.Option(help="pass or fail")],
    score: float | None = None,
    comment: str = "",
    reviewer: Annotated[str | None, typer.Option(help="Optional pseudonym.")] = None,
    root: RootOpt = Path(),
    results: ResultsOpt = None,
) -> None:
    from ai_reliability.human_review.review import ReviewStore, ReviewVerdict

    store = _workspace(root).store(results)
    try:
        label = ReviewStore(store.root / "reviews", run_id).add_label(
            item_id, ReviewVerdict(verdict.lower()), score=score, comment=comment, reviewer=reviewer
        )
    except (ReliabilityError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    console.print(f"labelled {label.item_id}: {label.verdict.value}")


# -- traces / canary / shadow -------------------------------------------------------------
@traces_app.command("import")
def traces_import(
    path: Path,
    output: Path,
    privacy: Annotated[
        TracePrivacy, typer.Option(help="Capture policy.")
    ] = TracePrivacy.METADATA_ONLY,
) -> None:
    """Import JSONL traces applying a privacy policy (default METADATA_ONLY: no text kept)."""
    from ai_reliability.traces.model import import_jsonl

    try:
        records = list(import_jsonl(path, privacy=privacy))
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")
    console.print(f"{len(records)} trace(s) written to {output} with policy {privacy.value}")


@traces_app.command("analyze-otel")
def traces_analyze_otel(path: Path) -> None:
    """Summarize model/tool/retrieval spans from an OTLP-JSON export."""
    from ai_reliability.traces.otel import analyze_spans, parse_otlp_json

    try:
        analysis = analyze_spans(parse_otlp_json(path))
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    table = Table("kind", "spans", "errors", "error rate", "p50 ms", "p95 ms")
    for k in analysis.by_kind.values():
        table.add_row(
            k.kind,
            str(k.count),
            str(k.errors),
            _pct(k.error_rate),
            f"{k.p50_ms or 0:.1f}",
            f"{k.p95_ms or 0:.1f}",
        )
    console.print(table)
    console.print(f"{analysis.traces} trace(s), {analysis.spans} span(s)")


@compare_app.command("run")
def canary_run(
    baseline: str, candidate: str, suite: str = "ci_core", root: RootOpt = Path()
) -> None:
    """Run baseline and candidate deployments on the same suite and compare five dimensions."""
    from ai_reliability.clock import SystemClock
    from ai_reliability.comparison.canary import run_canary
    from ai_reliability.runner.executor import EvaluationRunner
    from ai_reliability.systems.mock.knowledge import KnowledgeBase

    ws = _workspace(root)
    try:
        s = ws.suites.resolve(suite).select()
        kb, clock = KnowledgeBase.from_tests(s), SystemClock()
        runners = []
        for ref in (baseline, candidate):
            cfg = ws.systems.resolve(ref)
            runners.append(
                EvaluationRunner(
                    ws.systems.build(cfg, knowledge=kb, clock=clock),
                    ws.evaluators(),
                    settings=RunnerSettings(),
                    clock=clock,
                    pricing=cfg.pricing,
                )
            )
        rep = _run(
            run_canary(
                runners[0],
                runners[1],
                s,
                baseline_run_id=f"canary-base-{baseline}",
                candidate_run_id=f"canary-cand-{candidate}",
            )
        )
    except ReliabilityError as exc:
        raise _fail(str(exc)) from exc
    table = Table("dimension", "baseline", "candidate", "delta", "better")
    for d in rep.dimensions:
        table.add_row(d.dimension, _fmt(d.baseline), _fmt(d.candidate), _fmt(d.delta), d.better)
    console.print(table)
    console.print("regressions: " + (", ".join(rep.comparison.new_failures) or "none"))


@compare_app.command("shadow")
def shadow_run(traces: Path, baseline: str, candidate: str, root: RootOpt = Path()) -> None:
    """Offline shadow replay of sanitized traces (REDACTED_TEXT / FULL_TEXT only) - no live traffic."""
    from ai_reliability.comparison.shadow import run_shadow
    from ai_reliability.traces.model import TraceRecord

    ws = _workspace(root)
    try:
        records = [
            TraceRecord.model_validate_json(line)
            for line in traces.read_text("utf-8").splitlines()
            if line
        ]
        systems = [ws.systems.build(ws.systems.resolve(ref)) for ref in (baseline, candidate)]
        rep = _run(run_shadow(records, systems[0], systems[1]))
    except (ReliabilityError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    console.print_json(rep.model_dump_json())


@metrics_app.command("export")
def metrics_export(root: RootOpt = Path(), results: ResultsOpt = None) -> None:
    """Print Prometheus text exposition of stored run summaries."""
    from ai_reliability.observability.metrics import summaries_exposition

    console.print(
        summaries_exposition(results or _workspace(root).results_dir), highlight=False, markup=False
    )


@metrics_app.command("serve")
def metrics_serve(
    root: RootOpt = Path(),
    results: ResultsOpt = None,
    port: int = 9108,
    host: Annotated[
        str, typer.Option(help="Bind address (use 0.0.0.0 only inside a container).")
    ] = "127.0.0.1",
) -> None:
    """Serve /metrics (stored run summaries) for Prometheus scraping. Read-only."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from ai_reliability.observability.metrics import summaries_exposition

    directory = results or _workspace(root).results_dir

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") != "/metrics":
                self.send_error(404)
                return
            body = summaries_exposition(directory).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return None

    console.print(f"serving http://{host}:{port}/metrics from {directory}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


# -- demos ------------------------------------------------------------------------------
DemoOut = Annotated[
    Path, typer.Option("--output", help="Demo workspace (results go to <output>/results).")
]


def _demo_ws(output: Path) -> Workspace:
    output.mkdir(parents=True, exist_ok=True)
    return _workspace(output)


@demo_app.command("release-gate")
def demo_release_gate(output: DemoOut = Path("demo-output")) -> None:
    """Candidate improves correctness but fails new prompt-injection tests -> release FAIL."""
    from ai_reliability.services.demos import release_gate_demo

    ws = _demo_ws(output)
    res = _run(release_gate_demo(ws, ws.results_dir))
    console.print("[bold]SYNTHETIC / MOCK SYSTEM RESULTS[/bold]")
    console.print(f"baseline  {res.baseline_run_id}: pass rate {_pct(res.baseline_pass_rate)}")
    console.print(f"candidate {res.candidate_run_id}: pass rate {_pct(res.candidate_pass_rate)}")
    console.print(f"correctness category:       {res.correctness_delta * 100:+.1f} pp")
    console.print(f"prompt-injection resistance: {res.injection_delta * 100:+.1f} pp")
    _print_decision(res.decision)
    console.print(f"release report: {res.report_path}")


@demo_app.command("incident-to-eval")
def demo_incident(output: DemoOut = Path("demo-output")) -> None:
    """Synthetic incident -> sanitize -> classify -> DRAFT test -> review -> ACTIVE -> baseline fails, fix passes."""
    from ai_reliability.services.demos import incident_to_eval_demo

    ws = _demo_ws(output)
    res = _run(incident_to_eval_demo(ws, ws.results_dir))
    console.print("[bold]SYNTHETIC / MOCK SYSTEM RESULTS[/bold]")
    console.print(f"incident {res.incident_id}: redactions {res.redactions}")
    console.print(
        f"observed failure: {res.failure.observed_failure.value} ({res.failure.evidence}); "
        f"suspected root cause (unverified): {res.failure.suspected_root_cause}"
    )
    console.print(f"regression test {res.test_id}: {' -> '.join(res.status_history)}")
    console.print(
        f"buggy system v1: {res.baseline_outcome.upper()} | fixed system v2: {res.fixed_outcome.upper()}"
    )


@demo_app.command("drift")
def demo_drift(output: DemoOut = Path("demo-output")) -> None:
    """Synthetic historical runs vs a drifted run: latency, length and quality drift."""
    from ai_reliability.services.demos import drift_demo

    ws = _demo_ws(output)
    res = _run(drift_demo(ws, ws.results_dir))
    console.print("[bold]SYNTHETIC / MOCK SYSTEM RESULTS[/bold]")
    console.print(f"reference: {', '.join(res.reference_runs)}\ncurrent:   {res.current_run}")
    _print_drift(res.report)


@demo_app.command("judge-calibration")
def demo_judge(output: DemoOut = Path("demo-output")) -> None:
    """Scripted judges vs fixture labels: the verbosity-biased judge is detected."""
    from ai_reliability.services.demos import judge_calibration_demo

    res = _run(judge_calibration_demo(_demo_ws(output)))
    console.print("[bold]SYNTHETIC / SCRIPTED JUDGE RESULTS[/bold]")
    table = Table(
        "judge",
        "n",
        "accuracy",
        "precision",
        "recall",
        "false-pass rate",
        "kappa",
        "verbosity flip rate",
    )
    for name, rep, probe in (
        ("unbiased", res.unbiased, res.verbosity_probe_unbiased),
        ("verbosity-biased", res.biased, res.verbosity_probe_biased),
    ):
        table.add_row(
            name,
            str(rep.n),
            _pct(rep.accuracy),
            _pct(rep.precision),
            _pct(rep.recall),
            _pct(rep.false_pass_rate),
            "n/a" if rep.cohen_kappa is None else f"{rep.cohen_kappa:.2f}",
            _pct(probe.flip_rate),
        )
    console.print(table)
    p = res.position_probe_biased
    console.print(
        f"position-biased judge: {p.flips}/{p.n} order-inconsistent pairs ({p.direction})"
    )
    console.print(json.dumps({"confusion_biased": res.biased.confusion.model_dump()}))
