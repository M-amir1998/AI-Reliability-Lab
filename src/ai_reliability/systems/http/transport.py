"""Shared HTTP transport with uniform, retry-aware error classification."""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from pydantic import JsonValue

from ai_reliability.clock import Clock
from ai_reliability.domain.enums import ErrorCode
from ai_reliability.domain.system import SystemResponse

# 429 is handled separately (RATE_LIMITED). Plain 500s are not retried: they are
# usually deterministic server bugs, and retrying them hides real failures.
_TRANSIENT_STATUS = frozenset({408, 425, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class HttpOutcome:
    """Either a parsed JSON body or a canonical error response, plus measured latency."""

    body: JsonValue
    error: SystemResponse | None
    latency_ms: float


def auth_headers(api_key_env: str | None, *, scheme: str = "Bearer") -> dict[str, str]:
    """Build an Authorization header from an environment variable name (value never logged)."""
    if not api_key_env:
        return {}
    value = os.environ.get(api_key_env)
    if not value:
        return {}
    return {"Authorization": f"{scheme} {value}"}


def classify_status(status: int) -> SystemResponse:
    """Map a non-2xx status code to a canonical error response."""
    if status == 429:
        return SystemResponse.failure(
            ErrorCode.RATE_LIMITED, "HTTP 429", retryable=True, status_code=status
        )
    retryable = status in _TRANSIENT_STATUS
    return SystemResponse.failure(
        ErrorCode.HTTP_ERROR, f"HTTP {status}", retryable=retryable, status_code=status
    )


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: JsonValue,
    *,
    headers: dict[str, str],
    timeout_s: float,
    clock: Clock,
) -> HttpOutcome:
    """POST ``payload`` and return the JSON body, or a classified error. Never raises for I/O."""
    start = clock.monotonic()

    def elapsed() -> float:
        return (clock.monotonic() - start) * 1000.0

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException:
        return HttpOutcome(
            None,
            SystemResponse.failure(ErrorCode.TIMEOUT, "request timed out", retryable=True),
            elapsed(),
        )
    except httpx.TransportError as exc:
        return HttpOutcome(
            None,
            SystemResponse.failure(ErrorCode.NETWORK_ERROR, type(exc).__name__, retryable=True),
            elapsed(),
        )
    latency = elapsed()
    if response.status_code >= 400:
        return HttpOutcome(None, classify_status(response.status_code), latency)
    try:
        body: JsonValue = response.json()
    except ValueError:
        return HttpOutcome(
            None,
            SystemResponse.failure(ErrorCode.INVALID_RESPONSE, "response is not valid JSON"),
            latency,
        )
    return HttpOutcome(body, None, latency)
