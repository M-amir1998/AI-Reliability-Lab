from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_reliability.domain.enums import Outcome, Severity, TestStatus
from ai_reliability.domain.gate import GateSpec
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.test_case import ExpectedBehavior, TestCase, TestMetadata, TestSuite
from ai_reliability.errors import InvalidTestCase
from tests.conftest import make_case


def test_test_case_json_roundtrip() -> None:
    case = make_case(ExpectedBehavior(exact="a"), tags=frozenset({"b", "a"}))
    restored = TestCase.model_validate_json(case.model_dump_json())
    assert restored == case


def test_tags_serialize_sorted_so_hashes_are_stable() -> None:
    a = make_case(ExpectedBehavior(exact="x"), tags=frozenset({"z", "a", "m"}))
    assert a.model_dump(mode="json")["tags"] == ["a", "m", "z"]
    b = make_case(ExpectedBehavior(exact="x"), tags=frozenset({"m", "z", "a"}))
    assert a.content_hash() == b.content_hash()


def test_definition_hash_ignores_lifecycle_metadata() -> None:
    draft = make_case(ExpectedBehavior(exact="x")).model_copy(
        update={"metadata": TestMetadata(status=TestStatus.DRAFT)}
    )
    active = draft.model_copy(update={"metadata": TestMetadata(status=TestStatus.ACTIVE)})
    assert draft.definition_hash() == active.definition_hash()
    changed = draft.model_copy(update={"expected": ExpectedBehavior(exact="y")})
    assert changed.definition_hash() != draft.definition_hash()


@pytest.mark.parametrize("bad_id", ["", " x", "a/b", "x" * 200, "-leading"])
def test_invalid_test_ids_rejected(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        make_case(ExpectedBehavior(exact="x"), test_id=bad_id)


def test_evaluators_required_and_invalid_regex_rejected() -> None:
    with pytest.raises(ValidationError):
        make_case(ExpectedBehavior(exact="x"), evaluators=())
    with pytest.raises(ValidationError):
        ExpectedBehavior(regex="([unclosed")


def test_domain_models_are_frozen_and_forbid_extra() -> None:
    case = make_case(ExpectedBehavior(exact="x"))
    with pytest.raises(ValidationError):
        case.test_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ExpectedBehavior.model_validate({"exact": "x", "unknown_field": 1})


def test_suite_rejects_duplicate_ids_and_filters() -> None:
    a = make_case(ExpectedBehavior(exact="x"), test_id="a", tags=frozenset({"smoke"}))
    b = make_case(ExpectedBehavior(exact="x"), test_id="b", category="safety")
    c = make_case(ExpectedBehavior(exact="x"), test_id="c").model_copy(
        update={"metadata": TestMetadata(status=TestStatus.DRAFT)}
    )
    with pytest.raises(InvalidTestCase):
        TestSuite(suite_id="s", tests=(a, a))
    suite = TestSuite(suite_id="s", tests=(a, b, c))
    assert [t.test_id for t in suite.select()] == ["a", "b"]  # DRAFT excluded by default
    assert [t.test_id for t in suite.select(tags=["smoke"])] == ["a"]
    assert [t.test_id for t in suite.select(exclude_tags=["smoke"])] == ["b"]
    assert [t.test_id for t in suite.select(categories=["safety"])] == ["b"]
    assert len(suite.select(statuses=(TestStatus.DRAFT, TestStatus.ACTIVE))) == 3
    assert suite.categories == ("correctness", "safety")


def test_severity_ranking() -> None:
    ranks = [s.rank for s in (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)]
    assert ranks == sorted(ranks)
    assert make_case(severity=Severity.CRITICAL).is_critical


def test_gate_spec_validation() -> None:
    with pytest.raises(ValidationError):
        GateSpec(gate_id="g", kind="metric", metric="pass_rate")  # no bound
    with pytest.raises(ValidationError):
        GateSpec(gate_id="g", kind="category", category="x")  # no threshold
    with pytest.raises(ValidationError):
        GateSpec(gate_id="g", kind="critical_failures")  # no max
    GateSpec(gate_id="g", kind="category", category="x", max_regressions=0)


def test_case_result_properties() -> None:
    result = CaseResult(
        execution_id="e",
        run_id="r",
        test_id="t",
        test_hash="h",
        category="c",
        severity=Severity.LOW,
        outcome=Outcome.ERROR,
    )
    assert result.is_error and not result.passed
