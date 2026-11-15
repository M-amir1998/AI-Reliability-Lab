"""Property-based tests (Hypothesis) for invariants that must hold for all inputs."""

from __future__ import annotations

import json
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_reliability.domain.enums import GateDecision, Outcome, RegressionStatus, Severity
from ai_reliability.domain.gate import GateOutcome, GatePolicy, GateSpec
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.test_case import ExpectedBehavior, TestInput
from ai_reliability.evaluators.base import parse_json_response
from ai_reliability.evaluators.deterministic.structured import json_equal
from ai_reliability.gates.engine import evaluate_gates
from ai_reliability.metrics.aggregate import summarize
from ai_reliability.privacy.redaction import Redactor
from ai_reliability.redteam.mutations.metamorphic import mutate_test
from ai_reliability.redteam.mutations.operators import OPERATORS
from ai_reliability.regression.compare import classify, compare_runs
from ai_reliability.stats import wilson_interval
from tests.conftest import make_case

pytestmark = pytest.mark.property
OUTCOMES = st.sampled_from(list(Outcome))
SEVERITIES = st.sampled_from(list(Severity))
CATEGORIES = st.sampled_from(["correctness", "prompt_injection", "rag", "tool_use"])


@st.composite
def case_results(draw: st.DrawFn, max_size: int = 40) -> list[CaseResult]:
    n = draw(st.integers(min_value=0, max_value=max_size))
    return [
        CaseResult(
            execution_id=f"e{i}",
            run_id="r",
            test_id=f"t{i}",
            test_hash="h",
            category=draw(CATEGORIES),
            severity=draw(SEVERITIES),
            outcome=draw(OUTCOMES),
            latency_ms=draw(st.floats(min_value=0, max_value=1e5, allow_nan=False)),
        )
        for i in range(n)
    ]


@given(case_results())
def test_ratio_metrics_are_bounded(rows: list[CaseResult]) -> None:
    summary = summarize("r", rows)
    for metric in summary.metrics.values():
        if metric.unit == "ratio" and metric.value is not None:
            assert 0.0 <= metric.value <= 1.0
            if metric.ci_low is not None and metric.ci_high is not None:
                assert (
                    metric.ci_low <= metric.value + 1e-9 and metric.value <= metric.ci_high + 1e-9
                )
    assert sum(c.total for c in summary.categories.values()) == len(rows)


@given(st.integers(min_value=0, max_value=500), st.integers(min_value=1, max_value=500))
def test_wilson_contains_point_estimate(successes: int, n: int) -> None:
    successes = min(successes, n)
    low, high = wilson_interval(successes, n)
    assert 0.0 <= low <= successes / n <= high <= 1.0


@given(case_results(), st.floats(min_value=0, max_value=1))
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_gate_decision_is_fail_iff_a_blocking_reason_exists(
    rows: list[CaseResult], threshold: float
) -> None:
    policy = GatePolicy(
        gates=(
            GateSpec(
                gate_id="acc", kind="metric", metric="pass_rate", min=threshold, on_missing="fail"
            ),
            GateSpec(
                gate_id="pi",
                kind="category",
                category="prompt_injection",
                pass_rate_min=threshold,
                on_missing="skip",
            ),
        )
    )
    decision = evaluate_gates(policy, summarize("r", rows))
    has_fail = any(g.outcome is GateOutcome.FAIL for g in decision.gates) or bool(
        decision.critical_failures
    )
    assert (decision.decision is GateDecision.FAIL) == has_fail
    assert (decision.decision is GateDecision.FAIL) == bool(decision.blocking_reasons)


@given(case_results(max_size=15), st.floats(min_value=0, max_value=1))
def test_adding_a_gate_never_improves_the_decision(
    rows: list[CaseResult], threshold: float
) -> None:
    order = {GateDecision.PASS: 0, GateDecision.WARNING: 1, GateDecision.FAIL: 2}
    base = GatePolicy(
        gates=(
            GateSpec(gate_id="a", kind="metric", metric="pass_rate", min=0.0, on_missing="skip"),
        )
    )
    stricter = base.model_copy(
        update={
            "gates": (
                *base.gates,
                GateSpec(
                    gate_id="b",
                    kind="metric",
                    metric="verified_accuracy",
                    min=threshold,
                    on_missing="warn",
                ),
            )
        }
    )
    summary = summarize("r", rows)
    assert (
        order[evaluate_gates(stricter, summary).decision]
        >= order[evaluate_gates(base, summary).decision]
    )


@given(st.lists(st.tuples(OUTCOMES, OUTCOMES), min_size=1, max_size=30))
def test_regression_counts_partition_tests(pairs: list[tuple[Outcome, Outcome]]) -> None:
    def rows(idx: int) -> list[CaseResult]:
        return [
            CaseResult(
                execution_id=f"{i}",
                run_id="r",
                test_id=f"t{i}",
                test_hash="h",
                category="c",
                severity=Severity.LOW,
                outcome=p[idx],
            )
            for i, p in enumerate(pairs)
        ]

    report = compare_runs("b", rows(0), "c", rows(1))
    assert sum(report.counts.values()) == len(pairs)
    by_id = {case.test_id: case for case in report.cases}
    for i, (b, c) in enumerate(pairs):
        assert by_id[f"t{i}"].status is classify(b is Outcome.PASS, c is Outcome.PASS)
    if report.regression_rate is not None:
        assert 0 <= report.regression_rate <= 1
    assert report.counts[RegressionStatus.REGRESSED] == len(report.new_failures)


JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-(10**6), 10**6) | st.text(max_size=8),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=5), children, max_size=4)
    ),
    max_leaves=12,
)


@given(JSON)
def test_json_semantic_equality_is_reflexive_and_order_insensitive(value: object) -> None:
    assert json_equal(value, value, abs_tol=0, rel_tol=0)  # type: ignore[arg-type]
    text = json.dumps(value, sort_keys=False)
    shuffled = json.loads(json.dumps(value, sort_keys=True))
    assert json_equal(json.loads(text), shuffled, abs_tol=0, rel_tol=0)
    assert parse_json_response(text).ok


@given(st.text(max_size=300))
def test_json_parser_never_crashes(text: str) -> None:
    parsed = parse_json_response(text)
    assert parsed.ok or parsed.error


@given(st.text(alphabet=string.printable, max_size=200))
def test_redaction_is_idempotent_and_leaves_nothing_detectable(text: str) -> None:
    redactor = Redactor()
    once = redactor.redact(text).text
    assert redactor.redact(once).text == once
    assert redactor.findings(once) == {}


@given(
    st.text(min_size=1, max_size=120).filter(lambda s: s.strip()),
    st.sampled_from(sorted(OPERATORS)),
    st.integers(min_value=0, max_value=1000),
)
def test_mutations_are_pure_and_keep_expectations(prompt: str, operator: str, seed: int) -> None:
    case = make_case(ExpectedBehavior(exact="x"), prompt=prompt)
    a, b = mutate_test(case, operator, seed=seed), mutate_test(case, operator, seed=seed)
    assert a == b
    assert (
        a.expected == case.expected
        and a.evaluators == case.evaluators
        and a.category == case.category
    )
    assert isinstance(a.input, TestInput)
