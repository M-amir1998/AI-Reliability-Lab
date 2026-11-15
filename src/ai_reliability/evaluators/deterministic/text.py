"""Text evaluators: exact match, containment, regex, numeric, classification."""

from __future__ import annotations

import math
import re

from ai_reliability.domain.enums import FailureCategory
from ai_reliability.domain.result import EvaluationResult
from ai_reliability.domain.system import SystemResponse
from ai_reliability.domain.test_case import TestCase
from ai_reliability.evaluators.base import (
    BaseEvaluator,
    expected_of,
    normalize_text,
    parse_json_response,
)


class ExactMatchEvaluator(BaseEvaluator):
    """Response equals ``expected.exact`` after optional case/whitespace normalization."""

    name = "exact_match"
    version = "1"

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, expected_of(test_case).exact is not None, "expected.exact")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case)
        assert exp.exact is not None
        norm = {
            "case_sensitive": exp.case_sensitive,
            "collapse_whitespace": exp.normalize_whitespace,
        }
        if normalize_text(response.text, **norm) == normalize_text(exp.exact, **norm):
            return self.passed("exact match")
        return self.failed("response differs from expected value")


class ContainsEvaluator(BaseEvaluator):
    """All of ``contains_all``, at least one of ``contains_any``, none of ``not_contains``."""

    name = "contains"
    version = "1"

    def validate(self, test_case: TestCase) -> None:
        exp = expected_of(test_case)
        self.require(
            test_case,
            bool(exp.contains_all or exp.contains_any or exp.not_contains),
            "contains_all, contains_any or not_contains",
        )

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case)

        def norm(s: str) -> str:
            return normalize_text(s, case_sensitive=exp.case_sensitive, collapse_whitespace=True)

        text = norm(response.text)
        missing = [s for s in exp.contains_all if norm(s) not in text]
        forbidden = [s for s in exp.not_contains if norm(s) in text]
        any_ok = not exp.contains_any or any(norm(s) in text for s in exp.contains_any)
        total = len(exp.contains_all) + len(exp.not_contains) + (1 if exp.contains_any else 0)
        good = total - len(missing) - len(forbidden) - (0 if any_ok else 1)
        score = good / total if total else 1.0
        if not missing and not forbidden and any_ok:
            return self.passed("all containment checks satisfied", score=score)
        problems = []
        if missing:
            problems.append(f"missing {len(missing)} required substring(s)")
        if forbidden:
            problems.append(f"contains {len(forbidden)} forbidden substring(s)")
        if not any_ok:
            problems.append("none of contains_any present")
        return self.failed(
            "; ".join(problems),
            score=score,
            missing=len(missing),
            forbidden=len(forbidden),
        )


class RegexEvaluator(BaseEvaluator):
    """Response matches ``expected.regex`` (search semantics, DOTALL)."""

    name = "regex"
    version = "1"
    failure_category = FailureCategory.FORMAT_FAILURE

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, expected_of(test_case).regex is not None, "expected.regex")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case)
        assert exp.regex is not None
        flags = re.DOTALL | (0 if exp.case_sensitive else re.IGNORECASE)
        if re.search(exp.regex, response.text, flags):
            return self.passed("regex matched")
        return self.failed("regex did not match")


_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?")


def extract_number(text: str) -> float | None:
    """The answer number: ``{"answer": n}`` JSON, else the last number in the text."""
    parsed = parse_json_response(text)
    if parsed.ok and isinstance(parsed.value, dict):
        value = parsed.value.get("answer")
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    matches = _NUMBER.findall(text)
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


class NumericEvaluator(BaseEvaluator):
    """Final number in the response is within absolute/relative tolerance of ``expected.number``."""

    name = "numeric"
    version = "1"

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, expected_of(test_case).number is not None, "expected.number")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case)
        assert exp.number is not None
        got = extract_number(response.text)
        if got is None or not math.isfinite(got):
            return self.failed(
                "no number found in response", category=FailureCategory.FORMAT_FAILURE
            )
        if math.isclose(got, exp.number, rel_tol=exp.rel_tolerance, abs_tol=exp.abs_tolerance):
            return self.passed("within tolerance", observed=got)
        return self.failed("number outside tolerance", observed=got, expected=exp.number)


class ClassificationEvaluator(BaseEvaluator):
    """Predicted label equals ``expected.label`` and belongs to ``allowed_labels``.

    The label is read from a JSON ``{"label": ...}`` response or the whole
    normalized response text.
    """

    name = "classification"
    version = "1"

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, expected_of(test_case).label is not None, "expected.label")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case)
        assert exp.label is not None
        parsed = parse_json_response(response.text)
        if (
            parsed.ok
            and isinstance(parsed.value, dict)
            and isinstance(parsed.value.get("label"), str)
        ):
            raw = str(parsed.value["label"])
        else:
            raw = response.text
        label = raw.strip().strip(".").casefold()
        allowed = {a.casefold() for a in exp.allowed_labels}
        if allowed and label not in allowed:
            return self.failed(
                "predicted label is not in the allowed label set",
                category=FailureCategory.FORMAT_FAILURE,
                predicted=label[:64],
            )
        if label == exp.label.casefold():
            return self.passed("label correct", predicted=label[:64])
        return self.failed("wrong label", predicted=label[:64], expected=exp.label)
