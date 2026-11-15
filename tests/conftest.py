"""Shared fixtures. Everything is offline and deterministic (fake clocks, mock systems)."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest

from ai_reliability.clock import FakeClock
from ai_reliability.domain.content import Citation, RetrievedDocument, ToolCall
from ai_reliability.domain.enums import Severity
from ai_reliability.domain.system import SystemResponse, Timing
from ai_reliability.domain.test_case import ExpectedBehavior, TestCase, TestInput, TestMetadata
from ai_reliability.evaluators.registry import EvaluatorRegistry, default_evaluator_registry
from ai_reliability.testsuites.registry import TestSuiteRegistry

T = TypeVar("T")
REPO_ROOT = Path(__file__).resolve().parents[1]


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion (tests stay synchronous and deterministic)."""
    return asyncio.run(coro)


def make_case(
    expected: ExpectedBehavior | None = None,
    evaluators: tuple[str, ...] = ("exact_match",),
    *,
    test_id: str = "t.case",
    category: str = "correctness",
    prompt: str = "question?",
    severity: Severity = Severity.MEDIUM,
    documents: tuple[RetrievedDocument, ...] = (),
    tags: frozenset[str] = frozenset(),
    system_prompt: str | None = None,
) -> TestCase:
    return TestCase(
        test_id=test_id,
        category=category,
        input=TestInput(prompt=prompt, documents=documents, system_prompt=system_prompt),
        expected=expected,
        evaluators=evaluators,
        tags=tags,
        metadata=TestMetadata(severity=severity),
    )


def response(
    text: str = "",
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    citations: tuple[Citation, ...] = (),
    documents: tuple[RetrievedDocument, ...] = (),
    latency_ms: float | None = 100.0,
    cost_usd: float | None = None,
) -> SystemResponse:
    return SystemResponse(
        text=text,
        tool_calls=tool_calls,
        citations=citations,
        retrieved_documents=documents,
        timing=Timing(e2e_ms=latency_ms),
        cost_usd=cost_usd,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(scope="session")
def evaluators() -> EvaluatorRegistry:
    return default_evaluator_registry()


@pytest.fixture(scope="session")
def suites() -> TestSuiteRegistry:
    return TestSuiteRegistry()


@pytest.fixture
def workspace(tmp_path: Path):  # type: ignore[no-untyped-def]
    from ai_reliability.services.workspace import Workspace

    return Workspace(tmp_path)
