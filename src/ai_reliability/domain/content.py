"""Content objects shared by test inputs and system responses: documents, tools, citations."""

from __future__ import annotations

from pydantic import Field

from ai_reliability.domain.base import DomainModel, JsonObject
from ai_reliability.domain.enums import ToolDecision


class RetrievedDocument(DomainModel):
    """A document provided to, or retrieved by, a system. Content is untrusted data."""

    doc_id: str = Field(min_length=1)
    text: str
    source: str | None = None
    score: float | None = None
    trusted: bool = False


class Citation(DomainModel):
    """A citation emitted by a system, pointing at a document ID."""

    doc_id: str
    quote: str | None = None
    claim: str | None = None


class ToolSpec(DomainModel):
    """A tool made available to an agent for a test."""

    name: str = Field(min_length=1)
    description: str = ""
    writes: bool = False
    requires_approval: bool = False


class ToolOutputFixture(DomainModel):
    """A pre-recorded tool output injected into the agent context (untrusted)."""

    tool: str
    output: str


class ToolCall(DomainModel):
    """A tool invocation made (or attempted) by an agent."""

    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str | None = None
    decision: ToolDecision | None = None
    approved: bool | None = None


class ToolResult(DomainModel):
    """The result returned to the agent for a tool call."""

    call_id: str | None = None
    tool: str
    output: str = ""
    is_error: bool = False


class ApprovalRequest(DomainModel):
    """A human-approval request raised by an agent before a sensitive action."""

    tool: str
    reason: str = ""


class PromptRef(DomainModel):
    """Exact reference to a versioned prompt artifact."""

    prompt_id: str
    version: str
    sha256: str | None = None
