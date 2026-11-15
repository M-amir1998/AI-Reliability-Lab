"""Structured-output evaluators: JSON validity, schema, semantic equality, assertions."""

from __future__ import annotations

import math
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from ai_reliability.domain.enums import FailureCategory
from ai_reliability.domain.result import EvaluationResult
from ai_reliability.domain.system import SystemResponse
from ai_reliability.domain.test_case import Assertion, TestCase
from ai_reliability.errors import InvalidTestCase
from ai_reliability.evaluators.base import BaseEvaluator, expected_of, parse_json_response

_MISSING = object()


class JSONValidEvaluator(BaseEvaluator):
    """Response parses as JSON (optionally inside one fenced code block)."""

    name = "json_valid"
    version = "1"
    failure_category = FailureCategory.FORMAT_FAILURE

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        parsed = parse_json_response(response.text)
        if parsed.ok:
            return self.passed("valid JSON", fenced=parsed.fenced)
        return self.failed(parsed.error)


class JSONSchemaEvaluator(BaseEvaluator):
    """Response is valid JSON conforming to ``expected.json_schema`` (Draft 2020-12).

    Covers types, required fields, enums and value constraints expressible in
    JSON Schema. Each violation is counted; the first few paths are reported.
    """

    name = "json_schema"
    version = "1"
    failure_category = FailureCategory.FORMAT_FAILURE

    def validate(self, test_case: TestCase) -> None:
        schema = expected_of(test_case).json_schema
        self.require(test_case, schema is not None, "expected.json_schema")
        try:
            Draft202012Validator.check_schema(schema)  # type: ignore[arg-type]
        except SchemaError as exc:
            raise InvalidTestCase(
                f"{test_case.test_id}: invalid JSON schema: {exc.message}"
            ) from exc

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        schema = expected_of(test_case).json_schema
        assert schema is not None
        parsed = parse_json_response(response.text)
        if not parsed.ok:
            return self.failed(parsed.error, violations=-1)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(parsed.value), key=lambda e: list(e.absolute_path))
        if not errors:
            return self.passed("schema valid", fenced=parsed.fenced)
        paths = ", ".join("/" + "/".join(str(p) for p in e.absolute_path) for e in errors[:3])
        kinds = ",".join(sorted({str(e.validator) for e in errors}))
        return self.failed(
            f"{len(errors)} schema violation(s) at {paths}",
            violations=len(errors),
            validators=kinds,
        )


def json_equal(a: JsonValue, b: JsonValue, *, abs_tol: float, rel_tol: float) -> bool:
    """Semantic JSON equality: key order ignored, numbers compared with tolerance."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(float(a), float(b), abs_tol=abs_tol, rel_tol=rel_tol)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            json_equal(a[k], b[k], abs_tol=abs_tol, rel_tol=rel_tol) for k in a
        )
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            json_equal(x, y, abs_tol=abs_tol, rel_tol=rel_tol) for x, y in zip(a, b, strict=True)
        )
    return a == b


class JSONSemanticEvaluator(BaseEvaluator):
    """Parsed response is semantically equal to ``expected.json_expected``."""

    name = "json_semantic"
    version = "1"

    def validate(self, test_case: TestCase) -> None:
        self.require(
            test_case, expected_of(test_case).json_expected is not None, "expected.json_expected"
        )

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case)
        parsed = parse_json_response(response.text)
        if not parsed.ok:
            return self.failed(parsed.error, category=FailureCategory.FORMAT_FAILURE)
        tol = max(exp.abs_tolerance, 1e-9)
        if json_equal(parsed.value, exp.json_expected, abs_tol=tol, rel_tol=exp.rel_tolerance):
            return self.passed("semantically equal")
        return self.failed("JSON content differs from expected")


def resolve_pointer(document: JsonValue, pointer: str) -> object:
    """Resolve an RFC 6901 JSON pointer; returns a sentinel when absent."""
    if pointer in {"", "/"}:
        return document
    current: object = document
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                return _MISSING
            current = current[int(token)]
        else:
            return _MISSING
    return current


_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
    "null": (type(None),),
}


def run_assertion(assertion: Assertion, text: str, parsed: JsonValue, parsed_ok: bool) -> bool:
    """Evaluate one declarative assertion. Pure data comparison; nothing is executed."""
    if assertion.path == "$text":
        target: object = text
    elif not parsed_ok:
        return assertion.op == "not_exists"
    else:
        target = resolve_pointer(parsed, assertion.path)
    op, value = assertion.op, assertion.value
    if op == "exists":
        return target is not _MISSING
    if op == "not_exists":
        return target is _MISSING
    if target is _MISSING:
        return False
    if op == "equals":
        return target == value
    if op == "not_equals":
        return target != value
    if op == "contains":
        return isinstance(target, str | list) and value in target
    if op == "not_contains":
        return isinstance(target, str | list) and value not in target
    if op == "matches":
        return isinstance(target, str) and isinstance(value, str) and bool(re.search(value, target))
    if op in {"gte", "lte"}:
        if not (isinstance(target, int | float) and isinstance(value, int | float)):
            return False
        if isinstance(target, bool):
            return False
        return target >= value if op == "gte" else target <= value
    if op == "len_between":
        if not (isinstance(value, list) and len(value) == 2 and hasattr(target, "__len__")):
            return False
        low, high = value
        size = len(target)
        return isinstance(low, int) and isinstance(high, int) and low <= size <= high
    if op == "type_is":
        types = _TYPES.get(str(value), ())
        if value in {"number", "integer"} and isinstance(target, bool):
            return False
        return isinstance(target, types)
    if op == "in_set":
        return isinstance(value, list) and target in value
    return False


class UnitTestEvaluator(BaseEvaluator):
    """Runs declarative assertions ("unit tests") against the response.

    Every assertion is reported individually; the score is the fraction passed.
    Model output is treated purely as data — no code from the response is run.
    """

    name = "unit_test"
    version = "1"

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, bool(expected_of(test_case).assertions), "expected.assertions")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case)
        parsed = parse_json_response(response.text)
        outcomes = {
            a.name: run_assertion(a, response.text, parsed.value, parsed.ok) for a in exp.assertions
        }
        passed = sum(outcomes.values())
        score = passed / len(outcomes)
        failed = sorted(name for name, ok in outcomes.items() if not ok)
        if not failed:
            return self.passed(f"{passed}/{len(outcomes)} assertions passed", score=score)
        category = FailureCategory.FORMAT_FAILURE if not parsed.ok else FailureCategory.WRONG_ANSWER
        return self.failed(
            f"{len(failed)} assertion(s) failed: {', '.join(failed[:5])}",
            category=category,
            score=score,
            passed=passed,
            total=len(outcomes),
        )
