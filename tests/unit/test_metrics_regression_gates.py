from __future__ import annotations

import math
from pathlib import Path

import pytest

from ai_reliability.config.loader import load_model
from ai_reliability.config.models import SystemConfig
from ai_reliability.domain.enums import ErrorCode, GateDecision, Outcome, RegressionStatus, Severity
from ai_reliability.domain.gate import GateOutcome, GatePolicy, GateSpec
from ai_reliability.domain.result import CaseResult, EvaluationResult
from ai_reliability.errors import ConfigurationError
from ai_reliability.gates.config import load_gate_policy, parse_gate_policy
from ai_reliability.gates.engine import evaluate_gates
from ai_reliability.metrics.aggregate import summarize
from ai_reliability.regression.compare import classify, compare_runs
from ai_reliability.services.demos import DEMO_BASELINE, DEMO_CANDIDATE, RELEASE_POLICY
from ai_reliability.stats import (
    cohen_kappa,
    mcnemar_exact,
    paired_bootstrap_diff,
    percentile,
    wilson_interval,
)
from tests.conftest import REPO_ROOT


def result(
    test_id: str,
    outcome: Outcome,
    *,
    category: str = "correctness",
    severity: Severity = Severity.MEDIUM,
    rep: int = 0,
    latency: float = 100.0,
    test_hash: str = "h",
    error: ErrorCode | None = None,
    evaluator: str = "exact_match",
) -> CaseResult:
    return CaseResult(
        execution_id=f"{test_id}-{rep}",
        run_id="r",
        test_id=test_id,
        test_hash=test_hash,
        category=category,
        severity=severity,
        repetition=rep,
        outcome=outcome,
        latency_ms=latency,
        error_code=error,
        evaluations=(
            EvaluationResult(evaluator=evaluator, evaluator_version="1", outcome=outcome),
        ),
    )


# -- metrics --------------------------------------------------------------------------
def test_summary_metrics() -> None:
    rows = [
        result("a", Outcome.PASS, latency=100),
        result("b", Outcome.FAIL, latency=200, severity=Severity.CRITICAL),
        result("c", Outcome.ERROR, latency=300, error=ErrorCode.TIMEOUT),
        result("d", Outcome.NEEDS_REVIEW, category="safety", latency=400, evaluator="json_schema"),
    ]
    s = summarize("r", rows)
    assert s.metric("pass_rate") == 0.25
    assert s.metric("verified_accuracy") == pytest.approx(1 / 3)  # NEEDS_REVIEW excluded
    assert s.metric("timeout_rate") == 0.25 and s.metric("system_error_rate") == 0.0
    assert s.metric("critical_failure_rate") == 1.0 and s.critical_failures == ("b",)
    assert s.metric("p50_latency_ms") == 250.0
    assert s.metric("structured_output_validity") == 0.0
    assert s.categories["correctness"].total == 3 and s.categories["safety"].pass_rate == 0.0
    m = s.metrics["pass_rate"]
    assert m.ci_low is not None and m.ci_high is not None and m.ci_low <= 0.25 <= m.ci_high
    assert "consistency_rate" not in s.metrics  # only with repetitions


def test_consistency_with_repetitions() -> None:
    s = summarize(
        "r",
        [
            result("a", Outcome.PASS, rep=0),
            result("a", Outcome.FAIL, rep=1),
            result("b", Outcome.PASS, rep=0),
            result("b", Outcome.PASS, rep=1),
        ],
    )
    assert s.metric("consistency_rate") == 0.5 and s.metric("test_pass_rate") == 0.5


# -- regression ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("b", "c", "status"),
    [
        (True, True, RegressionStatus.UNCHANGED_PASS),
        (False, False, RegressionStatus.UNCHANGED_FAIL),
        (True, False, RegressionStatus.REGRESSED),
        (False, True, RegressionStatus.IMPROVED),
        (None, True, RegressionStatus.NOT_COMPARABLE),
    ],
)
def test_classify(b: bool | None, c: bool | None, status: RegressionStatus) -> None:
    assert classify(b, c) is status


def test_compare_runs_all_statuses() -> None:
    base = [
        result("same", Outcome.PASS),
        result("reg", Outcome.PASS, category="prompt_injection"),
        result("fix", Outcome.FAIL),
        result("bad", Outcome.FAIL),
        result("gone", Outcome.PASS),
        result("changed", Outcome.PASS, test_hash="h1"),
    ]
    cand = [
        result("same", Outcome.PASS),
        result("reg", Outcome.FAIL, category="prompt_injection"),
        result("fix", Outcome.PASS),
        result("bad", Outcome.ERROR),
        result("new", Outcome.PASS),
        result("changed", Outcome.PASS, test_hash="h2"),
    ]
    report = compare_runs(
        "b",
        base,
        "c",
        cand,
        baseline_summary=summarize("b", base),
        candidate_summary=summarize("c", cand),
    )
    status = {c.test_id: c.status for c in report.cases}
    assert status == {
        "same": RegressionStatus.UNCHANGED_PASS,
        "reg": RegressionStatus.REGRESSED,
        "fix": RegressionStatus.IMPROVED,
        "bad": RegressionStatus.UNCHANGED_FAIL,
        "gone": RegressionStatus.NOT_COMPARABLE,
        "new": RegressionStatus.NOT_COMPARABLE,
        "changed": RegressionStatus.NOT_COMPARABLE,
    }
    assert report.regression_rate == pytest.approx(1 / 4)
    assert report.new_failures == ("reg",) and report.fixed_failures == ("fix",)
    assert report.categories["prompt_injection"].delta == -1.0
    assert {s.name for s in report.statistics} == {
        "mcnemar_exact",
        "paired_bootstrap_pass_rate_diff",
    }
    assert "pass_rate" in report.metrics


def test_pass_threshold_with_repetitions() -> None:
    base = [result("a", Outcome.PASS, rep=i) for i in range(3)]
    cand = [
        result("a", Outcome.PASS, rep=0),
        result("a", Outcome.PASS, rep=1),
        result("a", Outcome.FAIL, rep=2),
    ]
    assert compare_runs("b", base, "c", cand).cases[0].status is RegressionStatus.REGRESSED
    lenient = compare_runs("b", base, "c", cand, pass_threshold=0.5)
    assert lenient.cases[0].status is RegressionStatus.UNCHANGED_PASS


# -- gates ---------------------------------------------------------------------------------
def _summary(pass_rate_fail: int = 0, critical: bool = False):  # type: ignore[no-untyped-def]
    rows = [
        result(f"t{i}", Outcome.FAIL if i < pass_rate_fail else Outcome.PASS) for i in range(10)
    ]
    if critical:
        rows.append(result("crit", Outcome.FAIL, severity=Severity.CRITICAL))
    return summarize("r", rows)


def test_metric_gate_pass_fail_warn() -> None:
    policy = GatePolicy(
        gates=(
            GateSpec(gate_id="acc", kind="metric", metric="pass_rate", min=0.85),
            GateSpec(
                gate_id="lat", kind="metric", metric="p95_latency_ms", max=50, on_violation="warn"
            ),
        )
    )
    good = evaluate_gates(policy, _summary(1))
    assert good.decision is GateDecision.WARNING and [g.outcome for g in good.gates] == [
        GateOutcome.PASS,
        GateOutcome.WARNING,
    ]
    bad = evaluate_gates(policy, _summary(3))
    assert bad.decision is GateDecision.FAIL and "pass_rate = 0.7" in bad.blocking_reasons[0]


@pytest.mark.parametrize(
    ("mode", "outcome"),
    [("warn", GateOutcome.WARNING), ("fail", GateOutcome.FAIL), ("skip", GateOutcome.SKIPPED)],
)
def test_missing_metric_policy(mode: str, outcome: GateOutcome) -> None:
    spec = GateSpec(
        gate_id="reg", kind="metric", metric="regression_rate", max=0.02, on_missing=mode
    )  # type: ignore[arg-type]
    decision = evaluate_gates(GatePolicy(gates=(spec,)), _summary())
    assert decision.gates[0].outcome is outcome and "baseline" in decision.gates[0].reason


def test_category_gate_vetoes_overall_improvement() -> None:
    base = [result(f"q{i}", Outcome.FAIL if i < 3 else Outcome.PASS) for i in range(10)]
    base += [result(f"pi{i}", Outcome.PASS, category="prompt_injection") for i in range(5)]
    cand = [result(f"q{i}", Outcome.PASS) for i in range(10)]
    cand += [
        result(f"pi{i}", Outcome.FAIL if i == 0 else Outcome.PASS, category="prompt_injection")
        for i in range(5)
    ]
    bs, cs = summarize("b", base), summarize("c", cand)
    assert (cs.metric("pass_rate") or 0) > (bs.metric("pass_rate") or 0)  # overall better...
    comparison = compare_runs("b", base, "c", cand, baseline_summary=bs, candidate_summary=cs)
    policy = GatePolicy(
        gates=(
            GateSpec(gate_id="overall", kind="metric", metric="pass_rate", min=0.8),
            GateSpec(gate_id="pi", kind="category", category="prompt_injection", max_regressions=0),
        )
    )
    decision = evaluate_gates(policy, cs, comparison)
    assert decision.decision is GateDecision.FAIL  # ...but a safety regression blocks release
    assert (
        decision.gates[0].outcome is GateOutcome.PASS
        and decision.gates[1].outcome is GateOutcome.FAIL
    )


def test_critical_override_blocks_even_without_gates() -> None:
    policy = GatePolicy(
        gates=(GateSpec(gate_id="acc", kind="metric", metric="pass_rate", min=0.5),)
    )
    decision = evaluate_gates(policy, _summary(critical=True))
    assert decision.decision is GateDecision.FAIL
    assert any("critical-severity override" in r for r in decision.blocking_reasons)
    lenient = policy.model_copy(update={"block_on_critical_failure": False})
    assert evaluate_gates(lenient, _summary(critical=True)).decision is GateDecision.PASS


def test_delta_metrics_need_baseline() -> None:
    spec = GateSpec(
        gate_id="d", kind="metric", metric="pass_rate_delta", min=-0.01, on_missing="fail"
    )
    assert evaluate_gates(GatePolicy(gates=(spec,)), _summary()).decision is GateDecision.FAIL


def test_parse_gate_policy_mapping_list_and_errors() -> None:
    mapping = parse_gate_policy(
        {
            "gates": {
                "a": {"metric": "pass_rate", "min": 0.9},
                "b": {"category": "jailbreak", "pass_rate_min": 1.0},
                "c": {"kind": "critical_failures", "max": 0},
            }
        }
    )
    assert [g.kind for g in mapping.gates] == ["metric", "category", "critical_failures"]
    listed = parse_gate_policy({"gates": [{"gate_id": "x", "metric": "pass_rate", "max": 1}]})
    assert listed.gates[0].gate_id == "x"
    for bad in ({}, {"gates": 3}, {"gates": {"a": {"metric": "pass_rate"}}}, {"gates": {"a": 5}}):
        with pytest.raises(ConfigurationError):
            parse_gate_policy(bad)


def test_shipped_release_policy_matches_demo_policy() -> None:
    shipped = load_gate_policy(REPO_ROOT / "configs" / "gates" / "release.yaml")
    assert shipped.gates == RELEASE_POLICY.gates
    assert shipped.block_on_critical_failure == RELEASE_POLICY.block_on_critical_failure


def test_shipped_demo_systems_match_demo_definitions() -> None:
    systems = REPO_ROOT / "configs" / "systems"
    assert load_model(systems / "demo-baseline.yaml", SystemConfig).mock == DEMO_BASELINE.mock
    assert load_model(systems / "demo-candidate.yaml", SystemConfig).mock == DEMO_CANDIDATE.mock


# -- statistics ----------------------------------------------------------------------------
def test_wilson_interval_properties() -> None:
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.2 < high < 0.35
    assert wilson_interval(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_mcnemar_and_bootstrap_and_kappa() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) < 0.01
    assert math.isclose(mcnemar_exact(3, 3), 1.0)
    diff, low, high = paired_bootstrap_diff([1, 1, 0, 0], [1, 1, 1, 1], seed=1)
    assert diff == 0.5 and low <= diff <= high
    assert paired_bootstrap_diff([1, 0], [0, 1], seed=3) == paired_bootstrap_diff(
        [1, 0], [0, 1], seed=3
    )
    assert cohen_kappa([True, False], [True, False]) == 1.0
    assert cohen_kappa([True, True], [True, True]) is None
    assert percentile([], 50) is None


def test_all_shipped_configs_load(tmp_path: Path) -> None:
    from ai_reliability.config.models import DriftConfig, RedTeamConfig, RunConfig
    from ai_reliability.services.workspace import Workspace

    ws = Workspace(REPO_ROOT)  # parses configs/systems, prompts, suites
    assert {"demo-baseline", "demo-candidate", "inferencelab-local", "agentruntime-local"} <= {
        c.system_id for c, _ in ws.systems.entries()
    }
    ws.evaluators().validate_suite(ws.suites.get("example_custom"))
    for path in (REPO_ROOT / "configs" / "runs").glob("*.yaml"):
        load_model(path, RunConfig)
    for path in (REPO_ROOT / "configs" / "redteam").glob("*.yaml"):
        load_model(path, RedTeamConfig)
    load_model(REPO_ROOT / "configs" / "drift" / "example.yaml", DriftConfig)
    descriptor = ws.systems.descriptor(ws.systems.resolve("openai-compatible-example"))
    assert descriptor.prompt is not None and descriptor.prompt.sha256
    assert "api_key" not in descriptor.model_dump_json().replace("api_key_env", "")
