"""System-under-test descriptors and canonical responses."""

from __future__ import annotations

from pydantic import Field

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.content import (
    ApprovalRequest,
    Citation,
    PromptRef,
    RetrievedDocument,
    ToolCall,
    ToolResult,
)
from ai_reliability.domain.enums import ErrorCode, SystemKind


class TokenUsage(DomainModel):
    """Token counts as reported by the system. Never estimated silently."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class Timing(DomainModel):
    """Latency breakdown. Components the system does not report stay ``None``."""

    e2e_ms: float | None = Field(default=None, ge=0)
    ttft_ms: float | None = Field(default=None, ge=0)
    tool_ms: float | None = Field(default=None, ge=0)
    retrieval_ms: float | None = Field(default=None, ge=0)


class ResponseError(DomainModel):
    """A transport or system-level failure (as opposed to a wrong answer)."""

    code: ErrorCode
    message: str = ""
    retryable: bool = False
    status_code: int | None = None


class SystemResponse(DomainModel):
    """Canonical response from any system: plain LLM, RAG pipeline or agent.

    All fields are untrusted data produced by the system under test.
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    approval_requests: tuple[ApprovalRequest, ...] = ()
    retrieved_documents: tuple[RetrievedDocument, ...] = ()
    citations: tuple[Citation, ...] = ()
    usage: TokenUsage | None = None
    timing: Timing = Field(default_factory=Timing)
    error: ResponseError | None = None
    run_status: str | None = None
    model: str | None = None
    attempts: int = Field(default=1, ge=1)
    cost_usd: float | None = Field(default=None, ge=0)

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def failure(
        cls,
        code: ErrorCode,
        message: str = "",
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> SystemResponse:
        return cls(
            error=ResponseError(
                code=code, message=message, retryable=retryable, status_code=status_code
            )
        )


class ToolConfig(DomainModel):
    """Recorded tool configuration of an agent system (names only, no credentials)."""

    tools: tuple[str, ...] = ()
    policy_version: str | None = None


class RagConfig(DomainModel):
    """Recorded retrieval configuration of a RAG system."""

    retriever: str | None = None
    top_k: int | None = None
    index_version: str | None = None


class SystemDescriptor(DomainModel):
    """Registry record of a system under test. Must never contain secrets.

    Credentials are referenced only by environment-variable *name* (``api_key_env``).
    """

    system_id: str
    kind: SystemKind
    model: str | None = None
    revision: str | None = None
    prompt: PromptRef | None = None
    tool_config: ToolConfig | None = None
    rag_config: RagConfig | None = None
    endpoint: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    seed: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
