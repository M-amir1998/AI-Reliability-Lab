"""Explicit error hierarchy.

Every failure mode that callers may need to distinguish has its own type.
Errors never carry raw secrets or full prompts in their messages.
"""

from __future__ import annotations


class ReliabilityError(Exception):
    """Base class for all AI Reliability Lab errors."""


class ConfigurationError(ReliabilityError):
    """A configuration file or value is invalid."""


class InvalidTestCase(ReliabilityError):
    """A test case definition is malformed or internally inconsistent."""


class UnknownComponent(ReliabilityError):
    """A referenced system, suite, evaluator or prompt is not registered."""


class SystemUnavailable(ReliabilityError):
    """The system under test could not be reached or returned a transport error."""

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class EvaluationTimeout(ReliabilityError):
    """A system call or evaluator exceeded its time budget."""


class EvaluatorFailure(ReliabilityError):
    """An evaluator crashed or could not produce a verdict (distinct from a FAIL verdict)."""


class GateFailure(ReliabilityError):
    """Raised when a caller requests that a failing release decision abort the process."""


class UnsafeEndpoint(ReliabilityError):
    """An endpoint URL was rejected by the SSRF / endpoint safety policy."""


class IncidentSanitizationFailure(ReliabilityError):
    """An incident could not be sanitized to a state safe for conversion."""


class InvalidStatusTransition(ReliabilityError):
    """A lifecycle status transition is not permitted (e.g. DRAFT -> ACTIVE)."""


class ResultCorruption(ReliabilityError):
    """Stored run artifacts are missing, inconsistent or fail integrity checks."""


class ImmutableRunError(ReliabilityError):
    """An attempt was made to overwrite a finalized evaluation run or baseline."""


class NotComparable(ReliabilityError):
    """Two runs cannot be compared (e.g. disjoint suites)."""
