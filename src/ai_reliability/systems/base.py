"""The single port through which every system under test is executed."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ai_reliability.domain.system import SystemDescriptor, SystemResponse
from ai_reliability.domain.test_case import TestInput


@runtime_checkable
class SystemUnderTest(Protocol):
    """Anything that can be evaluated: LLM, RAG pipeline, agent or custom endpoint.

    Implementations must not raise for transport problems; they return a
    :class:`SystemResponse` with ``error`` set (and ``retryable`` where appropriate)
    so the runner can apply a uniform retry policy.
    """

    @property
    def descriptor(self) -> SystemDescriptor:
        """Registry record describing this system (no secrets)."""
        ...

    async def execute(self, test: TestInput) -> SystemResponse:
        """Run the system on one input and return its canonical response."""
        ...


class ClosableSystem(SystemUnderTest, Protocol):
    """A system holding resources (e.g. HTTP connection pools)."""

    async def aclose(self) -> None: ...
