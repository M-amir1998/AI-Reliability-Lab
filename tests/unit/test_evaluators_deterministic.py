"""Contract tests for deterministic evaluators: positive, negative, malformed, edge cases."""

from __future__ import annotations

import pytest

from ai_reliability.domain.enums import FailureCategory, Outcome
from ai_reliability.domain.test_case import Assertion, ExpectedBehavior, SafetyExpectations
from ai_reliability.errors import InvalidTestCase
from ai_reliability.evaluators.base import parse_json_response
from ai_reliability.evaluators.deterministic.operational import looks_like_refusal
from ai_reliability.evaluators.deterministic.structured import json_equal, resolve_pointer
from ai_reliability.evaluators.deterministic.text import extract_number
from ai_reliability.evaluators.registry import EvaluatorRegistry
from tests.conftest import make_case, response, run


def check(
    evaluators: EvaluatorRegistry, name: str, expected: ExpectedBehavior, text: str, **kw: object
):  # type: ignore[no-untyped-def]
    case = make_case(expected, (name,))
    evaluators.get(name).validate(case)
    return run(evaluators.get(name).evaluate(case, response(text, **kw)))  # type: ignore[arg-type]


# -- exact / contains / regex -------------------------------------------------
@pytest.mark.parametrize(
    ("text", "outcome"),
    [
        ("Canberra", Outcome.PASS),
        ("  canberra \n", Outcome.PASS),
        ("Sydney", Outcome.FAIL),
        ("", Outcome.FAIL),
    ],
)
def test_exact_match(evaluators: EvaluatorRegistry, text: str, outcome: Outcome) -> None:
    assert (
        check(evaluators, "exact_match", ExpectedBehavior(exact="Canberra"), text).outcome
        is outcome
    )


def test_exact_match_case_sensitive(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(exact="Na", case_sensitive=True)
    assert check(evaluators, "exact_match", exp, "NA").outcome is Outcome.FAIL
    assert check(evaluators, "exact_match", exp, "Na").outcome is Outcome.PASS


def test_exact_match_requires_expectation(evaluators: EvaluatorRegistry) -> None:
    with pytest.raises(InvalidTestCase):
        evaluators.get("exact_match").validate(make_case(ExpectedBehavior(), ("exact_match",)))


def test_contains_scoring_and_reasons(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(
        contains_all=("alpha", "beta"), not_contains=("gamma",), contains_any=("x", "y")
    )
    good = check(evaluators, "contains", exp, "ALPHA beta x")
    assert good.outcome is Outcome.PASS and good.score == 1.0
    bad = check(evaluators, "contains", exp, "alpha gamma")
    assert bad.outcome is Outcome.FAIL
    assert bad.details["missing"] == 1 and bad.details["forbidden"] == 1
    assert 0.0 <= (bad.score or 0) < 1.0


def test_regex(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(regex=r"^[a-z]+ [a-z]+$", case_sensitive=True)
    assert check(evaluators, "regex", exp, "deep blue").outcome is Outcome.PASS
    result = check(evaluators, "regex", exp, "Deep Blue")
    assert (
        result.outcome is Outcome.FAIL and result.failure_category is FailureCategory.FORMAT_FAILURE
    )


# -- numeric / classification ---------------------------------------------------
@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("391", 391.0),
        ("The answer is 1,024.", 1024.0),
        ('{"answer": 3.5}', 3.5),
        ("first 2 then 7", 7.0),
        ("-4e2", -400.0),
        ("no digits", None),
    ],
)
def test_extract_number(text: str, value: float | None) -> None:
    assert extract_number(text) == value


def test_numeric_tolerance(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(number=3.14, abs_tolerance=0.01)
    assert check(evaluators, "numeric", exp, "3.141").outcome is Outcome.PASS
    assert check(evaluators, "numeric", exp, "3.2").outcome is Outcome.FAIL
    malformed = check(evaluators, "numeric", exp, "pi")
    assert malformed.failure_category is FailureCategory.FORMAT_FAILURE


def test_classification(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(label="positive", allowed_labels=("positive", "negative", "neutral"))
    assert check(evaluators, "classification", exp, "Positive.").outcome is Outcome.PASS
    assert check(evaluators, "classification", exp, '{"label": "positive"}').outcome is Outcome.PASS
    wrong = check(evaluators, "classification", exp, "negative")
    assert wrong.outcome is Outcome.FAIL and wrong.failure_category is FailureCategory.WRONG_ANSWER
    invalid = check(evaluators, "classification", exp, "very positive vibes")
    assert invalid.failure_category is FailureCategory.FORMAT_FAILURE


# -- structured output ----------------------------------------------------------------
def test_parse_json_response_accepts_single_fence() -> None:
    assert parse_json_response('```json\n{"a": 1}\n```').fenced
    assert parse_json_response('{"a": 1}').value == {"a": 1}
    assert not parse_json_response("{'a': 1}").ok  # never eval() Python literals
    assert not parse_json_response("[" * 5000).ok  # deep nesting handled, no crash


SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "severity": {"enum": ["low", "high"]}},
    "required": ["name", "severity"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    ("text", "outcome", "violations"),
    [
        ('{"name": "x", "severity": "low"}', Outcome.PASS, None),
        ('{"name": "x", "severity": "medium"}', Outcome.FAIL, 1),  # enum
        ('{"name": "x"}', Outcome.FAIL, 1),  # required
        ('{"name": 1, "severity": "low", "extra": true}', Outcome.FAIL, 2),  # type + additional
        ('{"name": "x", "severity": ', Outcome.FAIL, -1),  # malformed
    ],
)
def test_json_schema(
    evaluators: EvaluatorRegistry, text: str, outcome: Outcome, violations: int | None
) -> None:
    result = check(evaluators, "json_schema", ExpectedBehavior(json_schema=SCHEMA), text)
    assert result.outcome is outcome
    if violations is not None:
        assert result.details["violations"] == violations


def test_json_schema_invalid_schema_is_invalid_test(evaluators: EvaluatorRegistry) -> None:
    case = make_case(ExpectedBehavior(json_schema={"type": "not-a-type"}), ("json_schema",))
    with pytest.raises(InvalidTestCase):
        evaluators.get("json_schema").validate(case)


def test_json_semantic_and_equality_rules(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(json_expected={"a": 1, "b": [1.0, 2]}, abs_tolerance=0.001)
    assert (
        check(evaluators, "json_semantic", exp, '{"b": [1, 2.0004], "a": 1}').outcome
        is Outcome.PASS
    )
    assert check(evaluators, "json_semantic", exp, '{"a": 1}').outcome is Outcome.FAIL
    assert not json_equal(True, 1, abs_tol=0, rel_tol=0)  # bool is not a number
    assert not json_equal([1, 2], [2, 1], abs_tol=0, rel_tol=0)  # list order matters


def test_json_pointer_resolution() -> None:
    doc = {"a/b": {"~k": [10, 20]}}
    assert resolve_pointer(doc, "/a~1b/~0k/1") == 20
    assert resolve_pointer(doc, "/missing") is not None  # sentinel, not None value


def test_unit_test_assertions(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(
        assertions=(
            Assertion(name="id", path="/id", op="equals", value=7),
            Assertion(name="tags", path="/tags", op="len_between", value=[1, 2]),
            Assertion(name="kind", path="/kind", op="in_set", value=["a", "b"]),
            Assertion(name="score", path="/score", op="gte", value=0.5),
            Assertion(name="flag_bool", path="/flag", op="type_is", value="boolean"),
            Assertion(name="text", path="$text", op="contains", value='"id"'),
            Assertion(name="absent", path="/secret", op="not_exists"),
        )
    )
    good = check(
        evaluators,
        "unit_test",
        exp,
        '{"id": 7, "tags": ["x"], "kind": "a", "score": 0.9, "flag": true}',
    )
    assert good.outcome is Outcome.PASS and good.score == 1.0
    bad = check(
        evaluators, "unit_test", exp, '{"id": 8, "tags": [], "kind": "z", "score": true, "flag": 1}'
    )
    assert bad.outcome is Outcome.FAIL and bad.details["passed"] == 2  # text + absent
    malformed = check(evaluators, "unit_test", exp, "not json")
    assert malformed.failure_category is FailureCategory.FORMAT_FAILURE


# -- operational ------------------------------------------------------------------------
def test_latency_and_cost(evaluators: EvaluatorRegistry) -> None:
    exp = ExpectedBehavior(max_latency_ms=500, max_cost_usd=0.01)
    assert check(evaluators, "latency", exp, "x", latency_ms=200).outcome is Outcome.PASS
    assert check(evaluators, "latency", exp, "x", latency_ms=900).outcome is Outcome.FAIL
    assert check(evaluators, "latency", exp, "x", latency_ms=None).outcome is Outcome.SKIPPED
    assert check(evaluators, "cost", exp, "x", cost_usd=0.001).outcome is Outcome.PASS
    assert check(evaluators, "cost", exp, "x", cost_usd=0.5).outcome is Outcome.FAIL
    assert (
        check(evaluators, "cost", exp, "x").outcome is Outcome.SKIPPED
    )  # no pricing: never invented


@pytest.mark.parametrize(
    ("text", "refused"),
    [
        ("I can't help with that request.", True),
        ("I cannot share that.", True),
        ("I'm unable to do that", True),
        ("Use kill -9 <PID>.", False),
        ("Sure, here it is", False),
    ],
)
def test_refusal_heuristic(text: str, refused: bool) -> None:
    assert looks_like_refusal(text) is refused


def test_policy_evaluator(evaluators: EvaluatorRegistry) -> None:
    must_refuse = ExpectedBehavior(safety=SafetyExpectations(must_refuse=True))
    assert (
        check(evaluators, "policy", must_refuse, "I can't help with that.").outcome is Outcome.PASS
    )
    assert (
        check(evaluators, "policy", must_refuse, "Sure!").failure_category
        is FailureCategory.SAFETY_FAILURE
    )
    over = check(
        evaluators,
        "policy",
        ExpectedBehavior(safety=SafetyExpectations(must_refuse=False)),
        "I can't help",
    )
    assert over.failure_category is FailureCategory.INSTRUCTION_FAILURE
    bounded = ExpectedBehavior(
        safety=SafetyExpectations(max_output_chars=10, forbidden_patterns=(r"rm -rf",))
    )
    assert check(evaluators, "policy", bounded, "rm -rf /").outcome is Outcome.FAIL
    assert check(evaluators, "policy", bounded, "x" * 11).outcome is Outcome.FAIL
    assert check(evaluators, "policy", bounded, "ok").outcome is Outcome.PASS
