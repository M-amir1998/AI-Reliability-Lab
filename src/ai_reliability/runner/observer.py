"""Run observers: progress, metrics and tracing hooks (no-op by default)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ai_reliability.domain.result import CaseResult


class RunObserver(Protocol):
    """Receives runner events. Implementations must be cheap and never raise."""

    def on_run_started(self, run_id: str, total: int, skipped: int) -> None: ...

    def on_case_started(self, run_id: str, test_id: str, in_flight: int) -> None: ...

    def on_case_finished(self, run_id: str, result: CaseResult, in_flight: int) -> None: ...

    def on_run_finished(self, run_id: str, duration_s: float, interrupted: bool) -> None: ...


class NullObserver:
    """Default observer that ignores all events."""

    def on_run_started(self, run_id: str, total: int, skipped: int) -> None:
        return None

    def on_case_started(self, run_id: str, test_id: str, in_flight: int) -> None:
        return None

    def on_case_finished(self, run_id: str, result: CaseResult, in_flight: int) -> None:
        return None

    def on_run_finished(self, run_id: str, duration_s: float, interrupted: bool) -> None:
        return None


class FanOutObserver:
    """Forwards events to several observers."""

    def __init__(self, observers: Sequence[RunObserver]) -> None:
        self._observers = tuple(observers)

    def on_run_started(self, run_id: str, total: int, skipped: int) -> None:
        for o in self._observers:
            o.on_run_started(run_id, total, skipped)

    def on_case_started(self, run_id: str, test_id: str, in_flight: int) -> None:
        for o in self._observers:
            o.on_case_started(run_id, test_id, in_flight)

    def on_case_finished(self, run_id: str, result: CaseResult, in_flight: int) -> None:
        for o in self._observers:
            o.on_case_finished(run_id, result, in_flight)

    def on_run_finished(self, run_id: str, duration_s: float, interrupted: bool) -> None:
        for o in self._observers:
            o.on_run_finished(run_id, duration_s, interrupted)
