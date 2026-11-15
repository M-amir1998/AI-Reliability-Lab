"""Retry policy and client-side rate limiting (clock-injected, deterministic in tests)."""

from __future__ import annotations

import asyncio
from collections import deque

from ai_reliability.clock import Clock
from ai_reliability.determinism import stable_unit
from ai_reliability.domain.enums import ErrorCode
from ai_reliability.domain.system import SystemResponse

_RETRYABLE_CODES = frozenset(
    {ErrorCode.TIMEOUT, ErrorCode.NETWORK_ERROR, ErrorCode.RATE_LIMITED, ErrorCode.HTTP_ERROR}
)


class RetryPolicy:
    """Retries *transport* failures only.

    Retried: network errors, rate limiting, transient HTTP (502/503/504) and
    temporary timeouts — i.e. responses whose adapter marked them ``retryable``.
    Never retried: wrong answers, policy violations, invalid responses — those
    are evaluation outcomes, and retrying them would hide real failures.
    """

    def __init__(self, *, max_attempts: int = 3, initial_s: float = 0.5, max_s: float = 8.0):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self.initial_s = initial_s
        self.max_s = max_s

    def should_retry(self, response: SystemResponse, attempt: int) -> bool:
        error = response.error
        return (
            error is not None
            and error.retryable
            and error.code in _RETRYABLE_CODES
            and attempt < self.max_attempts
        )

    def backoff(self, attempt: int, key: str) -> float:
        """Exponential backoff with deterministic jitter in [0.5, 1.0] of the step."""
        step = min(self.max_s, self.initial_s * 2.0 ** (attempt - 1))
        return step * (0.5 + 0.5 * stable_unit(key, attempt))


class RateLimiter:
    """Sliding-window limiter for requests/second and tokens/minute.

    Token cost is estimated before the call (characters / 4) — an explicit,
    conservative estimate used only for pacing, never reported as usage.
    """

    def __init__(
        self,
        clock: Clock,
        *,
        requests_per_second: float | None = None,
        tokens_per_minute: int | None = None,
    ) -> None:
        self._clock = clock
        self._rps = requests_per_second
        self._tpm = tokens_per_minute
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._rps is not None or self._tpm is not None

    async def acquire(self, estimated_tokens: int = 0) -> None:
        if not self.enabled:
            return
        async with self._lock:
            while True:
                now = self._clock.monotonic()
                wait = self._wait_needed(now, estimated_tokens)
                if wait <= 0:
                    self._requests.append(now)
                    if self._tpm is not None:
                        self._tokens.append((now, estimated_tokens))
                    return
                await self._clock.sleep(wait)

    def _wait_needed(self, now: float, tokens: int) -> float:
        wait = 0.0
        if self._rps is not None:
            while self._requests and now - self._requests[0] >= 1.0:
                self._requests.popleft()
            if len(self._requests) >= self._rps:
                wait = max(wait, 1.0 - (now - self._requests[0]))
        if self._tpm is not None:
            while self._tokens and now - self._tokens[0][0] >= 60.0:
                self._tokens.popleft()
            used = sum(t for _, t in self._tokens)
            if self._tokens and used + tokens > self._tpm:
                wait = max(wait, 60.0 - (now - self._tokens[0][0]))
        return wait
