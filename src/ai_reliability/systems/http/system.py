"""Generic HTTP system adapter and in-process callable adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from pydantic import JsonValue

from ai_reliability.clock import Clock, SystemClock
from ai_reliability.domain.enums import ErrorCode
from ai_reliability.domain.system import SystemDescriptor, SystemResponse, Timing
from ai_reliability.domain.test_case import TestInput
from ai_reliability.systems.http.endpoint_safety import EndpointPolicy, validate_endpoint
from ai_reliability.systems.http.mappers import ResponseMapper, canonical_mapper
from ai_reliability.systems.http.transport import auth_headers, post_json

RequestBuilder = Callable[[TestInput, SystemDescriptor], JsonValue]


def canonical_request(test: TestInput, descriptor: SystemDescriptor) -> JsonValue:
    """Default request body: the canonical TestInput plus sampling settings."""
    return {
        "input": test.model_dump(mode="json", exclude={"prompt_ref"}),
        "temperature": descriptor.temperature,
        "seed": descriptor.seed,
    }


class HTTPSystem:
    """POSTs each test input as JSON and maps the JSON reply into a SystemResponse."""

    def __init__(
        self,
        descriptor: SystemDescriptor,
        *,
        policy: EndpointPolicy | None = None,
        mapper: ResponseMapper = canonical_mapper,
        request_builder: RequestBuilder = canonical_request,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not descriptor.endpoint:
            raise ValueError(f"system {descriptor.system_id} has no endpoint")
        self._url = validate_endpoint(descriptor.endpoint, policy or EndpointPolicy())
        self._descriptor = descriptor
        self._mapper = mapper
        self._build = request_builder
        self._timeout_s = timeout_s
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._clock = clock or SystemClock()

    @property
    def descriptor(self) -> SystemDescriptor:
        return self._descriptor

    async def execute(self, test: TestInput) -> SystemResponse:
        outcome = await post_json(
            self._client,
            self._url,
            self._build(test, self._descriptor),
            headers=auth_headers(self._descriptor.api_key_env),
            timeout_s=self._timeout_s,
            clock=self._clock,
        )
        response = outcome.error or self._mapper(outcome.body)
        return response.model_copy(update={"timing": Timing(e2e_ms=outcome.latency_ms)})

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


InProcessHandler = Callable[[TestInput], Awaitable[JsonValue]]


class CallableSystem:
    """Adapts an in-process async callable (e.g. a Python RAG pipeline) to the SUT port.

    Exceptions raised by the callable are converted to ``SYSTEM_ERROR`` responses
    so a crashing pipeline is reported as a system error, not an evaluator failure.
    """

    def __init__(
        self,
        descriptor: SystemDescriptor,
        handler: InProcessHandler,
        *,
        mapper: ResponseMapper = canonical_mapper,
        clock: Clock | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._handler = handler
        self._mapper = mapper
        self._clock = clock or SystemClock()

    @property
    def descriptor(self) -> SystemDescriptor:
        return self._descriptor

    async def execute(self, test: TestInput) -> SystemResponse:
        start = self._clock.monotonic()
        try:
            body = await self._handler(test)
        except Exception as exc:  # noqa: BLE001 - any pipeline crash is a system error
            response = SystemResponse.failure(ErrorCode.SYSTEM_ERROR, type(exc).__name__)
        else:
            response = self._mapper(body)
        elapsed = (self._clock.monotonic() - start) * 1000.0
        if response.timing.e2e_ms is None:
            response = response.model_copy(update={"timing": Timing(e2e_ms=elapsed)})
        return response
