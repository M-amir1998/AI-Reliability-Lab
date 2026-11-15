"""Injectable clocks so timing logic is testable without real sleeps."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Time source used by the runner, rate limiter, retry policy and slow systems."""

    def monotonic(self) -> float:
        """Seconds from an arbitrary fixed point; only differences are meaningful."""
        ...

    def now(self) -> datetime:
        """Current wall-clock time (UTC)."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Suspend for ``seconds``."""
        ...


class SystemClock:
    """Real clock."""

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


class FakeClock:
    """Deterministic clock: ``sleep`` advances virtual time instantly."""

    def __init__(self, start: datetime | None = None) -> None:
        self._start = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._elapsed = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._elapsed

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._elapsed)

    def advance(self, seconds: float) -> None:
        self._elapsed += max(0.0, seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)
        await asyncio.sleep(0)
