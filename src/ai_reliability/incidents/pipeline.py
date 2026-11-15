"""Incident-to-eval: sanitize -> classify -> minimal reproduction -> DRAFT regression test.

Nothing here commits or activates anything automatically: conversion yields a
DRAFT test that a human must review (DRAFT -> REVIEWED) and activate
(REVIEWED -> ACTIVE) before release gates run it by default.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from ai_reliability.domain.enums import FailureCategory, TestSource, TestStatus
from ai_reliability.domain.incident import (
    FailureCase,
    Incident,
    ObservedBehavior,
    SanitizationRecord,
)
from ai_reliability.domain.test_case import ExpectedBehavior, TestCase, TestInput, TestMetadata
from ai_reliability.errors import (
    IncidentSanitizationFailure,
    InvalidStatusTransition,
    InvalidTestCase,
)
from ai_reliability.privacy.redaction import Redactor

# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def _redact_input(test_input: TestInput, redactor: Redactor, counts: Counter[str]) -> TestInput:
    def r(text: str | None) -> str | None:
        if text is None:
            return None
        result = redactor.redact(text)
        counts.update(result.counts)
        return result.text

    return test_input.model_copy(
        update={
            "prompt": r(test_input.prompt) or "",
            "system_prompt": r(test_input.system_prompt),
            "developer_prompt": r(test_input.developer_prompt),
            "documents": tuple(
                d.model_copy(update={"text": r(d.text) or ""}) for d in test_input.documents
            ),
            "tool_outputs": tuple(
                o.model_copy(update={"output": r(o.output) or ""}) for o in test_input.tool_outputs
            ),
            "variables": {k: r(v) or "" for k, v in test_input.variables.items()},
        }
    )


def _all_text(incident: Incident) -> Iterable[str]:
    i = incident.input
    yield from (incident.description, incident.expected_description, i.prompt)
    yield from (t for t in (i.system_prompt, i.developer_prompt) if t)
    yield from (d.text for d in i.documents)
    yield from (o.output for o in i.tool_outputs)
    yield from i.variables.values()
    yield incident.observed.response_text
    if incident.observed.error:
        yield incident.observed.error
    if incident.expected and incident.expected.reference_text:
        yield incident.expected.reference_text


def sanitize_incident(
    incident: Incident, redactor: Redactor | None = None, *, extra_literals: Iterable[str] = ()
) -> Incident:
    """Redact secrets/PII from every free-text field, then verify nothing detectable remains.

    ``extra_literals`` lets operators redact known sensitive strings (customer names,
    internal hostnames) that patterns cannot recognise. The result is still marked
    ``manual_review_required``: pattern redaction is never assumed to be complete.
    """
    # Declared canaries are synthetic by contract and must survive, or the reproduction
    # could no longer exhibit the leak it is meant to catch.
    canary = (
        incident.expected.safety.canary if incident.expected and incident.expected.safety else None
    )
    red = redactor or Redactor(
        extra_literals=extra_literals, preserve_literals=(canary,) if canary else ()
    )
    counts: Counter[str] = Counter()

    def r(text: str) -> str:
        result = red.redact(text)
        counts.update(result.counts)
        return result.text

    expected = incident.expected
    if expected is not None and expected.reference_text is not None:
        expected = expected.model_copy(update={"reference_text": r(expected.reference_text)})
    sanitized = incident.model_copy(
        update={
            "description": r(incident.description),
            "expected_description": r(incident.expected_description),
            "input": _redact_input(incident.input, red, counts),
            "observed": ObservedBehavior(
                response_text=r(incident.observed.response_text),
                tool_calls=incident.observed.tool_calls,
                error=r(incident.observed.error) if incident.observed.error else None,
            ),
            "expected": expected,
            "sanitization": SanitizationRecord(
                sanitized=True,
                redactions=dict(sorted(counts.items())),
                sanitizer_version=red.version,
                manual_review_required=True,
            ),
        }
    )
    residual = Counter[str]()
    for text in _all_text(sanitized):
        residual.update(red.findings(text))
    if residual:
        raise IncidentSanitizationFailure(
            f"incident {incident.incident_id}: residual sensitive patterns after redaction: "
            + ", ".join(sorted(residual))
        )
    return sanitized


# ---------------------------------------------------------------------------
# Classification (observed failure only; root cause stays a hypothesis)
# ---------------------------------------------------------------------------

_SAFETY_CATEGORIES = frozenset(
    {"prompt_injection", "jailbreak", "privacy", "safety", "instruction_hierarchy"}
)


def classify_incident(incident: Incident) -> FailureCase:
    """Rule-based classification of the *observed* failure."""
    observed = incident.observed
    error = (observed.error or "").lower()
    if "timeout" in error or "timed out" in error:
        category, evidence = FailureCategory.TIMEOUT, "observed error mentions a timeout"
    elif error:
        category, evidence = FailureCategory.SYSTEM_ERROR, "system returned an error"
    elif incident.category in _SAFETY_CATEGORIES:
        category, evidence = (
            FailureCategory.SAFETY_FAILURE,
            f"safety-relevant category {incident.category!r}",
        )
    elif incident.category in {"tool_use", "agent"} or observed.tool_calls:
        category, evidence = (
            FailureCategory.TOOL_FAILURE,
            f"tool calls observed: {', '.join(observed.tool_calls)}",
        )
    elif incident.category == "rag":
        category, evidence = (
            FailureCategory.HALLUCINATION,
            "RAG answer not supported by retrieved context",
        )
    elif incident.category == "structured_output":
        category, evidence = (
            FailureCategory.FORMAT_FAILURE,
            "structured output did not meet the contract",
        )
    else:
        category, evidence = (
            FailureCategory.WRONG_ANSWER,
            "response differs from expected behaviour",
        )
    return FailureCase(
        case_id=f"fc-{incident.incident_id}",
        incident_id=incident.incident_id,
        observed_failure=category,
        evidence=evidence,
        suspected_root_cause=incident.suspected_root_cause,
        confidence="unverified",
    )


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def infer_evaluators(expected: ExpectedBehavior) -> tuple[str, ...]:
    """Choose deterministic evaluators from whichever expectation fields are set."""
    names: list[str] = []
    if expected.exact is not None:
        names.append("exact_match")
    if expected.number is not None:
        names.append("numeric")
    if expected.label is not None:
        names.append("classification")
    if expected.regex is not None:
        names.append("regex")
    if expected.contains_all or expected.contains_any or expected.not_contains:
        names.append("contains")
    if expected.json_schema is not None:
        names.extend(["json_valid", "json_schema"])
    if expected.json_expected is not None:
        names.append("json_semantic")
    if expected.assertions:
        names.append("unit_test")
    if expected.safety is not None:
        if expected.safety.canary:
            names.append("canary_leak")
        if expected.safety.injection_markers:
            names.append("injection_resistance")
        if (
            expected.safety.must_refuse is not None
            or expected.safety.forbidden_patterns
            or expected.safety.max_output_chars
        ):
            names.append("policy")
    if expected.tools is not None:
        names.append("tool_policy")
        if expected.tools.required_calls or expected.tools.argument_types:
            names.append("tool_call")
        if expected.tools.max_calls is not None or expected.tools.max_repeated_calls is not None:
            names.append("tool_trace")
    if expected.citations is not None:
        names.append("citation")
    if expected.rag is not None:
        names.append("hallucination")
    return tuple(dict.fromkeys(names))


def minimal_reproduction(test_input: TestInput) -> TestInput:
    """Drop fields not needed to reproduce the failure (template variables, prompt refs)."""
    return test_input.model_copy(update={"variables": {}, "prompt_ref": None})


def incident_to_test(incident: Incident) -> TestCase:
    """Convert a *sanitized* incident into a DRAFT regression test case."""
    if not incident.sanitization.sanitized:
        raise IncidentSanitizationFailure(
            f"incident {incident.incident_id} must be sanitized before conversion"
        )
    if incident.expected is None:
        raise InvalidTestCase(
            f"incident {incident.incident_id} has no machine-checkable expected behaviour; "
            "add 'expected' before converting"
        )
    evaluators = infer_evaluators(incident.expected)
    if not evaluators:
        raise InvalidTestCase(
            f"incident {incident.incident_id}: expected behaviour selects no evaluator"
        )
    failure = classify_incident(incident)
    return TestCase(
        test_id=f"incident.{incident.incident_id}",
        category=incident.category,
        input=minimal_reproduction(incident.input),
        expected=incident.expected,
        evaluators=evaluators,
        tags=incident.tags
        | {"incident", "regression", f"failure:{failure.observed_failure.value}"},
        metadata=TestMetadata(
            severity=incident.severity,
            status=TestStatus.DRAFT,
            source=TestSource.INCIDENT,
            incident_id=incident.incident_id,
            trace_id=incident.trace_id,
            description=f"Regression test for incident {incident.incident_id}: {incident.description}"[
                :500
            ],
        ),
    )


# ---------------------------------------------------------------------------
# Review lifecycle
# ---------------------------------------------------------------------------

_ALLOWED: dict[TestStatus, frozenset[TestStatus]] = {
    TestStatus.DRAFT: frozenset({TestStatus.REVIEWED, TestStatus.RETIRED}),
    TestStatus.REVIEWED: frozenset({TestStatus.ACTIVE, TestStatus.DRAFT, TestStatus.RETIRED}),
    TestStatus.ACTIVE: frozenset({TestStatus.RETIRED}),
    TestStatus.RETIRED: frozenset(),
}


def transition(test: TestCase, new_status: TestStatus, *, reviewer: str | None = None) -> TestCase:
    """Move a test through DRAFT -> REVIEWED -> ACTIVE (-> RETIRED). Skipping review is rejected."""
    current = test.metadata.status
    if new_status not in _ALLOWED[current]:
        raise InvalidStatusTransition(
            f"{test.test_id}: {current.value} -> {new_status.value} not allowed"
        )
    if new_status is TestStatus.REVIEWED and not reviewer:
        raise InvalidStatusTransition(
            f"{test.test_id}: review requires a (pseudonymous) reviewer id"
        )
    metadata = test.metadata.model_copy(
        update={"status": new_status, "reviewer": reviewer or test.metadata.reviewer}
    )
    return test.model_copy(update={"metadata": metadata})
