"""Compact, typed builders for hand-written test cases."""

from __future__ import annotations

from pydantic import JsonValue

from ai_reliability.domain.content import (
    Citation,
    RetrievedDocument,
    ToolCall,
    ToolOutputFixture,
    ToolSpec,
)
from ai_reliability.domain.enums import Severity, TestSource
from ai_reliability.domain.test_case import (
    ExpectedBehavior,
    TestCase,
    TestInput,
    TestMetadata,
)


def case(
    test_id: str,
    category: str,
    prompt: str,
    evaluators: tuple[str, ...],
    *,
    expected: ExpectedBehavior | None = None,
    tags: tuple[str, ...] = (),
    severity: Severity = Severity.MEDIUM,
    description: str = "",
    system_prompt: str | None = None,
    developer_prompt: str | None = None,
    documents: tuple[RetrievedDocument, ...] = (),
    tool_outputs: tuple[ToolOutputFixture, ...] = (),
    tools: tuple[ToolSpec, ...] = (),
    response_format: str = "text",
    source: TestSource = TestSource.HAND_WRITTEN,
) -> TestCase:
    """Build a test case with sensible defaults."""
    return TestCase(
        test_id=test_id,
        category=category,
        input=TestInput(
            prompt=prompt,
            system_prompt=system_prompt,
            developer_prompt=developer_prompt,
            documents=documents,
            tool_outputs=tool_outputs,
            tools=tools,
            response_format="json" if response_format == "json" else "text",
        ),
        expected=expected,
        evaluators=evaluators,
        tags=frozenset(tags),
        metadata=TestMetadata(severity=severity, source=source, description=description),
    )


def doc(doc_id: str, text: str, source: str | None = None) -> RetrievedDocument:
    return RetrievedDocument(doc_id=doc_id, text=text, source=source or f"fixture://{doc_id}")


def cite(doc_id: str, quote: str | None = None, claim: str | None = None) -> Citation:
    return Citation(doc_id=doc_id, quote=quote, claim=claim)


def call(name: str, **arguments: JsonValue) -> ToolCall:
    return ToolCall(name=name, arguments=dict(arguments), call_id=f"ref-{name}")


def tool(
    name: str, description: str = "", *, writes: bool = False, requires_approval: bool = False
) -> ToolSpec:
    return ToolSpec(
        name=name, description=description, writes=writes, requires_approval=requires_approval
    )
