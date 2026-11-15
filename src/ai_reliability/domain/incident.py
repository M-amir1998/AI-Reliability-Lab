"""Production incidents and failure cases (observed failure kept apart from root cause)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import FailureCategory, Severity
from ai_reliability.domain.test_case import ExpectedBehavior, TestInput


class ObservedBehavior(DomainModel):
    """What the system actually did during the incident."""

    response_text: str = ""
    tool_calls: tuple[str, ...] = ()
    error: str | None = None


class SanitizationRecord(DomainModel):
    """Audit record of sanitization. Stores counts per redaction kind, never originals."""

    sanitized: bool = False
    redactions: dict[str, int] = Field(default_factory=dict)
    sanitizer_version: str | None = None
    manual_review_required: bool = True
    reviewed_by: str | None = None


class Incident(DomainModel):
    """A production incident, as recorded by an operator.

    Raw incidents are assumed to be unsafe: they must pass sanitization (and
    manual review) before any derived artifact may be committed.
    """

    incident_id: str
    system_id: str
    category: str
    description: str
    severity: Severity = Severity.HIGH
    occurred_at: datetime | None = None
    trace_id: str | None = None
    input: TestInput
    observed: ObservedBehavior
    expected: ExpectedBehavior | None = None
    expected_description: str = ""
    suspected_root_cause: str | None = None
    tags: frozenset[str] = frozenset()
    sanitization: SanitizationRecord = Field(default_factory=SanitizationRecord)


class FailureCase(DomainModel):
    """A classified failure. ``observed_failure`` is fact; ``suspected_root_cause`` is not."""

    case_id: str
    test_id: str | None = None
    incident_id: str | None = None
    observed_failure: FailureCategory
    evidence: str
    suspected_root_cause: str | None = None
    confidence: str = "unverified"
