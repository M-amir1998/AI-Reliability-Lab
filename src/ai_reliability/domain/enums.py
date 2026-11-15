"""Enumerations shared across the domain."""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    """Impact of a test failing. CRITICAL failures can block a release on their own."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}


class Outcome(StrEnum):
    """Outcome of one evaluator, or of one test execution after aggregation.

    ``ERROR`` means the system or evaluator could not produce a verdict. For gating
    purposes ERROR counts as not-passed; it is reported separately from FAIL so that
    infrastructure problems are never confused with quality problems.
    """

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


class TestStatus(StrEnum):
    """Lifecycle of a test case. Only ACTIVE tests run in release gates by default."""

    __test__ = False  # not a pytest test class

    DRAFT = "draft"
    REVIEWED = "reviewed"
    ACTIVE = "active"
    RETIRED = "retired"


class TestSource(StrEnum):
    """How a test case was created."""

    __test__ = False

    HAND_WRITTEN = "hand_written"
    TEMPLATE = "template"
    INCIDENT = "incident"
    MUTATION = "mutation"
    REDTEAM = "redteam"
    METAMORPHIC = "metamorphic"


class RegressionStatus(StrEnum):
    """Per-test classification when comparing a candidate run with a baseline run."""

    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED_PASS = "unchanged_pass"
    UNCHANGED_FAIL = "unchanged_fail"
    NOT_COMPARABLE = "not_comparable"


class GateDecision(StrEnum):
    """Release decision produced by quality gates."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class FailureCategory(StrEnum):
    """Observed-failure taxonomy. These describe *what* failed, never *why*."""

    WRONG_ANSWER = "wrong_answer"
    HALLUCINATION = "hallucination"
    INSTRUCTION_FAILURE = "instruction_failure"
    FORMAT_FAILURE = "format_failure"
    SAFETY_FAILURE = "safety_failure"
    TOOL_FAILURE = "tool_failure"
    RETRIEVAL_FAILURE = "retrieval_failure"
    CITATION_FAILURE = "citation_failure"
    TIMEOUT = "timeout"
    SYSTEM_ERROR = "system_error"
    PERFORMANCE = "performance"


class ToolDecision(StrEnum):
    """Policy outcome for a tool invocation."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ErrorCode(StrEnum):
    """Transport / system level error codes reported by adapters."""

    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    SYSTEM_ERROR = "system_error"


class CapturePolicy(StrEnum):
    """Which response text is retained in stored results."""

    METADATA_ONLY = "metadata_only"
    FAILED_CASES = "failed_cases"
    ALL = "all"


class TracePrivacy(StrEnum):
    """How much of a trace/incident text is retained on import."""

    HASH_ONLY = "hash_only"
    METADATA_ONLY = "metadata_only"
    REDACTED_TEXT = "redacted_text"
    FULL_TEXT = "full_text"


class SystemKind(StrEnum):
    """Kind of system under test (informational; all kinds share one interface)."""

    MOCK = "mock"
    SCRIPTED = "scripted"
    HTTP = "http"
    OPENAI_COMPATIBLE = "openai_compatible"
    AGENT = "agent"
    RAG = "rag"


class RunStatus(StrEnum):
    """Lifecycle of an evaluation run."""

    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
