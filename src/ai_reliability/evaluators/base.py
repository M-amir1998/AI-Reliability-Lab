"""Evaluator contract and helpers for building verdicts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import JsonValue

from ai_reliability.domain.enums import FailureCategory, Outcome
from ai_reliability.domain.result import DetailValue, EvaluationResult
from ai_reliability.domain.system import SystemResponse
from ai_reliability.domain.test_case import ExpectedBehavior, TestCase
from ai_reliability.errors import InvalidTestCase


@runtime_checkable
class Evaluator(Protocol):
    """Judges one response against one test case.

    ``name``/``version`` identify semantics: changing what an evaluator considers
    a pass MUST bump ``version`` so historical runs remain interpretable.
    """

    @property
    def name(self) -> str:
        """Unique evaluator name referenced by test cases."""
        ...

    @property
    def version(self) -> str:
        """Semantic version of the evaluator's pass criteria."""
        ...

    def validate(self, test_case: TestCase) -> None:
        """Raise :class:`InvalidTestCase` if the test lacks what this evaluator needs."""
        ...

    async def evaluate(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        """Return a verdict. Must not raise for bad responses; raise only on internal bugs."""
        ...


class BaseEvaluator:
    """Convenience base: synchronous ``check`` wrapped as async ``evaluate``."""

    name: ClassVar[str] = "base"
    version: ClassVar[str] = "1"
    failure_category: ClassVar[FailureCategory] = FailureCategory.WRONG_ANSWER

    def validate(self, test_case: TestCase) -> None:
        return None

    async def evaluate(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        return self.check(test_case, response)

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        raise NotImplementedError

    # -- verdict helpers ---------------------------------------------------
    def passed(
        self,
        reason: str = "",
        *,
        score: float | None = 1.0,
        extra: Mapping[str, DetailValue] | None = None,
        **details: DetailValue,
    ) -> EvaluationResult:
        return EvaluationResult(
            evaluator=self.name,
            evaluator_version=self.version,
            outcome=Outcome.PASS,
            score=score,
            reason=reason,
            details={**(extra or {}), **details},
        )

    def failed(
        self,
        reason: str,
        *,
        category: FailureCategory | None = None,
        score: float | None = 0.0,
        extra: Mapping[str, DetailValue] | None = None,
        **details: DetailValue,
    ) -> EvaluationResult:
        return EvaluationResult(
            evaluator=self.name,
            evaluator_version=self.version,
            outcome=Outcome.FAIL,
            score=score,
            reason=reason,
            failure_category=category or self.failure_category,
            details={**(extra or {}), **details},
        )

    def skipped(self, reason: str) -> EvaluationResult:
        return EvaluationResult(
            evaluator=self.name,
            evaluator_version=self.version,
            outcome=Outcome.SKIPPED,
            score=None,
            reason=reason,
        )

    def require(self, test_case: TestCase, condition: bool, what: str) -> None:
        if not condition:
            raise InvalidTestCase(f"{test_case.test_id}: evaluator {self.name!r} requires {what}")


def expected_of(test_case: TestCase) -> ExpectedBehavior:
    """Expected behaviour or an empty one (evaluators then validate their own fields)."""
    return test_case.expected or ExpectedBehavior()


def normalize_text(text: str, *, case_sensitive: bool, collapse_whitespace: bool) -> str:
    out = text.strip()
    if collapse_whitespace:
        out = re.sub(r"\s+", " ", out)
    if not case_sensitive:
        out = out.casefold()
    return out


_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n(?P<body>.*?)\n?```\s*$", re.DOTALL)


class JsonParse:
    """Result of strictly parsing a response as JSON (never ``eval``)."""

    __slots__ = ("error", "fenced", "ok", "value")

    def __init__(self, ok: bool, value: JsonValue, error: str, fenced: bool) -> None:
        self.ok = ok
        self.value = value
        self.error = error
        self.fenced = fenced


def parse_json_response(text: str) -> JsonParse:
    """Parse ``text`` as JSON, accepting a single fenced ```json block."""
    fenced = False
    match = _FENCE.match(text)
    if match:
        text, fenced = match.group("body"), True
    try:
        return JsonParse(True, json.loads(text), "", fenced)
    except (ValueError, RecursionError) as exc:
        return JsonParse(False, None, f"invalid JSON: {type(exc).__name__}", fenced)
