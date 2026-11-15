"""Defensive parsers turning untrusted endpoint JSON into canonical responses.

External payloads are parsed with permissive pydantic models (unknown fields
ignored) and never evaluated as code.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ai_reliability.domain.content import (
    ApprovalRequest,
    Citation,
    RetrievedDocument,
    ToolCall,
    ToolResult,
)
from ai_reliability.domain.enums import ErrorCode, ToolDecision
from ai_reliability.domain.system import SystemResponse, TokenUsage


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ResponseMapper(Protocol):
    """Converts a JSON body into a :class:`SystemResponse`."""

    def __call__(self, body: JsonValue) -> SystemResponse: ...


def parse_arguments(raw: JsonValue) -> dict[str, JsonValue]:
    """Parse tool arguments that may arrive as a JSON object or a JSON-encoded string."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"_unparsed": raw[:2000]}
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}
    return {}


def _invalid(message: str) -> SystemResponse:
    return SystemResponse.failure(ErrorCode.INVALID_RESPONSE, message)


class _Usage(_Lenient):
    input_tokens: int = 0
    output_tokens: int = 0


class _Doc(_Lenient):
    doc_id: str
    text: str = ""
    score: float | None = None
    source: str | None = None


class _Cite(_Lenient):
    doc_id: str
    quote: str | None = None
    claim: str | None = None


class _AgentToolCall(_Lenient):
    tool: str
    arguments: JsonValue = None
    call_id: str | None = None
    decision: ToolDecision | None = None
    approved: bool | None = None


class _AgentToolResult(_Lenient):
    tool: str
    call_id: str | None = None
    output: str = ""
    is_error: bool = False


class _Approval(_Lenient):
    tool: str
    reason: str = ""


class CanonicalPayload(_Lenient):
    """Default HTTP response contract: ``{"text": ..., "usage": {...}}``."""

    text: str = ""
    usage: _Usage | None = None
    model: str | None = None


class AgentPayload(_Lenient):
    """Canonical agent response (AgentRuntime-shaped)."""

    final_answer: str = ""
    status: str | None = None
    tool_calls: list[_AgentToolCall] = Field(default_factory=list)
    tool_results: list[_AgentToolResult] = Field(default_factory=list)
    approval_requests: list[_Approval] = Field(default_factory=list)
    usage: _Usage | None = None


class RagPayload(_Lenient):
    """Canonical RAG response: answer, retrieved documents and citations."""

    answer: str = ""
    retrieved: list[_Doc] = Field(default_factory=list)
    citations: list[_Cite] = Field(default_factory=list)
    usage: _Usage | None = None


def _usage(u: _Usage | None) -> TokenUsage | None:
    return (
        None
        if u is None
        else TokenUsage(input_tokens=u.input_tokens, output_tokens=u.output_tokens)
    )


def canonical_mapper(body: JsonValue) -> SystemResponse:
    try:
        payload = CanonicalPayload.model_validate(body)
    except ValidationError:
        return _invalid("response does not match canonical contract")
    return SystemResponse(text=payload.text, usage=_usage(payload.usage), model=payload.model)


def agent_mapper(body: JsonValue) -> SystemResponse:
    try:
        payload = AgentPayload.model_validate(body)
    except ValidationError:
        return _invalid("response does not match agent contract")
    return SystemResponse(
        text=payload.final_answer,
        run_status=payload.status,
        tool_calls=tuple(
            ToolCall(
                name=c.tool,
                arguments=parse_arguments(c.arguments),
                call_id=c.call_id,
                decision=c.decision,
                approved=c.approved,
            )
            for c in payload.tool_calls
        ),
        tool_results=tuple(
            ToolResult(tool=r.tool, call_id=r.call_id, output=r.output, is_error=r.is_error)
            for r in payload.tool_results
        ),
        approval_requests=tuple(
            ApprovalRequest(tool=a.tool, reason=a.reason) for a in payload.approval_requests
        ),
        usage=_usage(payload.usage),
    )


def rag_mapper(body: JsonValue) -> SystemResponse:
    try:
        payload = RagPayload.model_validate(body)
    except ValidationError:
        return _invalid("response does not match RAG contract")
    return SystemResponse(
        text=payload.answer,
        retrieved_documents=tuple(
            RetrievedDocument(doc_id=d.doc_id, text=d.text, score=d.score, source=d.source)
            for d in payload.retrieved
        ),
        citations=tuple(
            Citation(doc_id=c.doc_id, quote=c.quote, claim=c.claim) for c in payload.citations
        ),
        usage=_usage(payload.usage),
    )


MAPPERS: dict[str, ResponseMapper] = {
    "canonical": canonical_mapper,
    "agent": agent_mapper,
    "rag": rag_mapper,
}
