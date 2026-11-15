from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from ai_reliability.analysis.failures import cluster_failures, reason_signature
from ai_reliability.comparison.pairwise import deterministic_pairwise, judge_pairwise
from ai_reliability.comparison.shadow import run_shadow
from ai_reliability.config.models import DriftThresholds
from ai_reliability.domain.enums import (
    FailureCategory,
    Outcome,
    RunStatus,
    Severity,
    SystemKind,
    TracePrivacy,
)
from ai_reliability.domain.result import CaseResult, EvaluationResult
from ai_reliability.domain.run import RunManifest
from ai_reliability.domain.system import SystemDescriptor
from ai_reliability.drift.analyze import DriftStatus, analyze_drift
from ai_reliability.drift.methods import categorical_psi, ks_test, psi
from ai_reliability.errors import ImmutableRunError, ResultCorruption, UnknownComponent
from ai_reliability.evaluators.judge.client import Rubric, ScriptedJudge
from ai_reliability.human_review.review import (
    ReviewStore,
    ReviewVerdict,
    blind_pairs,
    build_queue,
    judge_vs_human,
    unblind,
)
from ai_reliability.metrics.aggregate import summarize
from ai_reliability.reporting.html import markdown_to_html
from ai_reliability.reporting.markdown import SYNTHETIC_BANNER, render_plots, render_report
from ai_reliability.storage.analysis import RunColumns, category_pass_rates, load_columns
from ai_reliability.storage.parquet import read_results, write_results
from ai_reliability.storage.registry_db import RunIndex, RunRecord
from ai_reliability.storage.run_store import RunStore
from ai_reliability.systems.mock import KnowledgeBase, MockProfile, MockSystem
from ai_reliability.traces.model import TraceRecord, import_jsonl
from ai_reliability.traces.otel import analyze_spans, parse_otlp_json
from tests.conftest import REPO_ROOT, make_case, run


def row(test_id: str, outcome: Outcome = Outcome.PASS, **kw: object) -> CaseResult:
    defaults: dict[str, object] = {
        "execution_id": f"e-{test_id}",
        "run_id": "run-1",
        "test_id": test_id,
        "test_hash": "h",
        "category": "correctness",
        "severity": Severity.MEDIUM,
        "outcome": outcome,
        "latency_ms": 120.0,
        "tags": ("a",),
        "evaluations": (
            EvaluationResult(
                evaluator="exact_match",
                evaluator_version="1",
                outcome=outcome,
                score=1.0 if outcome is Outcome.PASS else 0.0,
            ),
        ),
    }
    defaults.update(kw)
    return CaseResult.model_validate(defaults)


def manifest(run_id: str = "run-1", kind: SystemKind = SystemKind.MOCK) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        tool_version="t",
        system=SystemDescriptor(system_id="sys", kind=kind),
        suite_id="s",
        suite_version="1",
        test_count=2,
        test_ids_hash="x",
        evaluator_versions={"exact_match": "1"},
        config_hash="c",
    )


# -- storage --------------------------------------------------------------------------------
def test_parquet_roundtrip(tmp_path: Path) -> None:
    rows = [
        row("a"),
        row("b", Outcome.FAIL, failure_category=FailureCategory.WRONG_ANSWER, response_text="x"),
    ]
    write_results(tmp_path / "r.parquet", rows)
    assert read_results(tmp_path / "r.parquet") == rows
    with pytest.raises(ResultCorruption):
        read_results(tmp_path / "missing.parquet")


def test_run_store_immutability_and_checksums(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    m = manifest()
    store.create(m, "config: 1\n")
    rows = [row("a"), row("b", Outcome.FAIL)]
    store.finalize(
        m, rows, summarize("run-1", rows), documents={"report.md": "# r"}, plots={"p.svg": "<svg/>"}
    )
    assert store.load_manifest("run-1").status is RunStatus.COMPLETED
    assert store.load_results("run-1") == rows and store.list_run_ids() == ["run-1"]
    store.verify("run-1")
    with pytest.raises(ImmutableRunError):
        store.finalize(m, rows, summarize("run-1", rows))
    with pytest.raises(ImmutableRunError):
        store.create(m, "")
    (tmp_path / "run-1" / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ResultCorruption):
        store.verify("run-1")
    with pytest.raises(UnknownComponent):
        store.load_manifest("nope")


@pytest.mark.parametrize("bad", ["../x", "a/b", "C:x", ".hidden", "", "a\\b", "x" * 200])
def test_run_ids_cannot_escape_results_dir(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        RunStore(tmp_path).run_dir(bad)


def test_run_index_baselines_are_append_only(tmp_path: Path) -> None:
    index = RunIndex(tmp_path / "registry.sqlite")
    record = RunRecord(
        run_id="r1",
        created_at="t",
        completed_at=None,
        status=RunStatus.RUNNING,
        system_id="s",
        suite_id="x",
        suite_version="1",
        config_hash="c",
        git_sha=None,
        pass_rate=None,
        decision=None,
    )
    index.upsert_run(record)
    with pytest.raises(ImmutableRunError):
        index.declare_baseline("main", "r1")  # not completed
    index.upsert_run(record.model_copy(update={"status": RunStatus.COMPLETED}))
    index.upsert_run(record.model_copy(update={"run_id": "r2", "status": RunStatus.COMPLETED}))
    with pytest.raises(ImmutableRunError):
        index.upsert_run(record)  # completed runs are immutable
    index.declare_baseline("main", "r1")
    index.declare_baseline("main", "r2")
    assert index.current_baseline("main") == "r2"
    assert [b.run_id for b in index.baseline_history("main")] == ["r1", "r2"]
    with pytest.raises(UnknownComponent):
        index.declare_baseline("main", "ghost")


def test_duckdb_analysis(tmp_path: Path) -> None:
    for run_id, outcome in (("r1", Outcome.PASS), ("r2", Outcome.FAIL)):
        (tmp_path / run_id).mkdir()
        write_results(
            tmp_path / run_id / "results.parquet",
            [row("a", outcome, run_id=run_id), row("b", run_id=run_id, category="rag")],
        )
    cols = load_columns(tmp_path, ["r1", "r2"])
    assert len(cols.latency_ms) == 4 and sum(cols.passed) == 3
    rates = {(r, c): p for r, c, p, _ in category_pass_rates(tmp_path, ["r1", "r2"])}
    assert rates[("r2", "correctness")] == 0.0 and rates[("r1", "rag")] == 1.0
    with pytest.raises(UnknownComponent):
        load_columns(tmp_path, ["missing"])


# -- drift -------------------------------------------------------------------------------------
def test_drift_methods_on_known_distributions() -> None:
    rng = np.random.default_rng(0)
    ref = rng.normal(100, 10, 2000).tolist()
    same = rng.normal(100, 10, 2000).tolist()
    shifted = rng.normal(130, 10, 2000).tolist()
    assert (psi(ref, same) or 1) < 0.05 < (psi(ref, shifted) or 0)
    assert (ks_test(ref, same) or (0, 0))[1] > 0.01 > (ks_test(ref, shifted) or (0, 1))[1]
    assert categorical_psi(["a", "b"] * 50, ["a", "b"] * 50) == pytest.approx(0)
    assert (categorical_psi(["a"] * 90 + ["b"] * 10, ["a"] * 10 + ["b"] * 90) or 0) > 0.25
    assert psi([], [1.0]) is None and ks_test([1.0], [2.0]) is None
    assert psi([5.0] * 20, [5.0] * 20) == pytest.approx(0, abs=1e-6)


def _cols(latency: list[float], passed: list[float]) -> RunColumns:
    n = len(latency)
    return RunColumns(
        ("r",), latency, [10.0] * n, [40.0] * n, passed, [0.0] * n, ["c"] * n, ["none"] * n
    )


def test_analyze_drift_statuses() -> None:
    rng = np.random.default_rng(1)
    ref = _cols(rng.normal(300, 30, 500).tolist(), [1.0] * 480 + [0.0] * 20)
    stable = analyze_drift(
        ref, _cols(rng.normal(300, 30, 500).tolist(), [1.0] * 478 + [0.0] * 22), DriftThresholds()
    )
    assert stable.status is DriftStatus.STABLE
    drifted = analyze_drift(
        ref, _cols(rng.normal(600, 30, 500).tolist(), [1.0] * 400 + [0.0] * 100), DriftThresholds()
    )
    status = {f.feature: f.status for f in drifted.features}
    assert status["latency_ms"] is DriftStatus.DRIFT and status["pass_rate"] is DriftStatus.DRIFT
    tiny = analyze_drift(_cols([1.0], [1.0]), _cols([2.0], [1.0]), DriftThresholds())
    assert {f.feature: f.status for f in tiny.features}[
        "latency_ms"
    ] is DriftStatus.INSUFFICIENT_DATA


# -- failures / reporting --------------------------------------------------------------------------
def test_failure_clustering() -> None:
    rows = [
        row(
            f"t{i}",
            Outcome.FAIL,
            failure_category=FailureCategory.WRONG_ANSWER,
            failure_reason=f"numeric: number outside tolerance {i}",
        )
        for i in range(3)
    ]
    rows.append(
        row(
            "s",
            Outcome.FAIL,
            category="safety",
            failure_category=FailureCategory.SAFETY_FAILURE,
            failure_reason="canary_leak: leaked (plain)",
        )
    )
    clusters = cluster_failures([*rows, row("ok")])
    assert [c.count for c in clusters] == [3, 1] and clusters[0].evaluator == "numeric"
    assert reason_signature("x: value 12 vs 'abc'") == "value # vs #"


def test_report_and_html_rendering() -> None:
    rows = [
        row("a"),
        row(
            "b",
            Outcome.FAIL,
            failure_category=FailureCategory.WRONG_ANSWER,
            failure_reason="exact_match: response differs",
        ),
    ]
    summary = summarize("run-1", rows)
    plots = render_plots(summary, rows, None)
    assert {
        "pass_rate_by_category.svg",
        "failure_categories.svg",
        "latency_distribution.svg",
    } <= set(plots)
    assert all(svg.startswith("<svg") for svg in plots.values())
    report = render_report(manifest(), summary, rows, plots=sorted(plots))
    for section in (
        "## System",
        "## Overall metrics",
        "## Category metrics",
        "## Safety",
        "## Regressions",
        "## Failures",
        "## Quality gates",
        "## Latency",
        "## Cost",
        "## Drift",
        "## Limitations",
    ):
        assert section in report
    assert SYNTHETIC_BANNER in report
    assert SYNTHETIC_BANNER not in render_report(
        manifest(kind=SystemKind.OPENAI_COMPATIBLE), summary, rows
    )
    html = markdown_to_html("# T\n| a |\n|---|\n| <script>alert(1)</script> |\n- `x`", "t")
    assert "<script>" not in html and "&lt;script&gt;" in html and "<table>" in html


# -- traces -------------------------------------------------------------------------------------------
def test_trace_import_defaults_to_metadata_only() -> None:
    records = list(import_jsonl(REPO_ROOT / "examples" / "traces" / "sample_traces.jsonl"))
    assert len(records) == 3 and all(
        r.input_text is None and r.response_text is None for r in records
    )
    assert all(len(r.input_sha256) == 64 for r in records) and not records[0].replayable
    redacted = list(
        import_jsonl(
            REPO_ROOT / "examples" / "traces" / "sample_traces.jsonl",
            privacy=TracePrivacy.REDACTED_TEXT,
        )
    )
    assert "someone@example.com" not in (redacted[1].input_text or "") and redacted[1].replayable


def test_otel_analysis_drops_content_attributes() -> None:
    spans = parse_otlp_json(REPO_ROOT / "examples" / "traces" / "otel_export.json")
    assert {s.kind for s in spans} == {"model", "tool", "retrieval"}
    assert all("SHOULD NOT BE KEPT" not in json.dumps(s.attributes) for s in spans)
    analysis = analyze_spans(spans)
    assert analysis.traces == 2 and analysis.by_kind["model"].errors == 1
    assert analysis.slowest_traces[0][0] == "a2"


# -- comparison / review ---------------------------------------------------------------------------
def test_deterministic_pairwise_reports_ties_and_sign_test() -> None:
    a = [row("x"), row("y", Outcome.FAIL), row("z")]
    b = [row("x"), row("y"), row("z", Outcome.FAIL)]
    report = deterministic_pairwise(a, b, "A", "B")
    assert (report.wins_a, report.wins_b, report.ties) == (1, 1, 1)
    assert report.win_rate_b_excluding_ties == 0.5 and report.sign_test_p_value == 1.0


def test_judge_pairwise_counts_order_inconsistency() -> None:
    case = make_case(test_id="q")
    case = case.model_copy(update={"expected": case.expected or None})
    tests = [make_case(test_id=f"q{i}").model_copy(update={"expected": None}) for i in range(2)]
    rubric = Rubric(rubric_id="r", version="1", criteria="c")
    biased = run(
        judge_pairwise(ScriptedJudge("position"), rubric, tests, ["a", "b"], ["c", "d"], "A", "B")
    )
    assert biased.inconsistent_judgements == 2 and biased.ties == 2


def test_blinding_roundtrip() -> None:
    pairs, key = blind_pairs(
        [(f"p{i}", "q", f"A{i}", f"B{i}") for i in range(10)], "sysA", "sysB", seed=1
    )
    assert {p.left[0] for p in pairs} == {"A", "B"}  # sides actually shuffled
    prefs = {p.pair_id: "left" if p.left.startswith("A") else "right" for p in pairs}
    assert set(unblind(prefs, key).values()) == {"A"}


def test_review_queue_and_labels(tmp_path: Path) -> None:
    rows = [row("a", Outcome.NEEDS_REVIEW, response_text="maybe"), row("b", Outcome.FAIL), row("c")]
    assert [i.test_id for i in build_queue(rows)] == ["a"]
    queue = build_queue(rows, include_failures=True)
    store = ReviewStore(tmp_path, "run-1")
    store.write_queue(queue)
    store.add_label("e-a", ReviewVerdict.PASS, score=0.8, reviewer="rev-7")
    assert [i.item_id for i in store.pending()] == ["e-b"]
    with pytest.raises(UnknownComponent):
        store.add_label("ghost", ReviewVerdict.FAIL)
    agreement = judge_vs_human({"e-a": True}, store.labels())
    assert agreement.n == 1 and agreement.accuracy == 1.0


def test_shadow_replays_only_replayable_traces() -> None:
    traces = [
        TraceRecord(
            trace_id="t1", input_text="hello", input_sha256="x", privacy=TracePrivacy.REDACTED_TEXT
        ),
        TraceRecord(trace_id="t2", input_sha256="y"),
    ]
    a = MockSystem("a", MockProfile(), KnowledgeBase())
    b = MockSystem(
        "b",
        MockProfile(default_behavior="refusal", unknown_input_behavior="refusal"),
        KnowledgeBase(),
    )  # type: ignore[arg-type]
    report = run(run_shadow(traces, a, b))
    assert report.replayed == 1 and report.skipped_not_replayable == 1
    assert report.agreement_rate == 0.0 and report.candidate.refusals == 1
