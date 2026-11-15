"""RAG system adapters.

Canonical RAG response contract::

    {"answer": "...",
     "retrieved": [{"doc_id": "...", "text": "...", "score": 0.8}],
     "citations": [{"doc_id": "...", "quote": "...", "claim": "..."}],
     "usage": {"input_tokens": 0, "output_tokens": 0}}

No vector database is required: evaluation consumes whatever documents the
pipeline reports as retrieved, or fixture documents supplied by the test.
"""

from __future__ import annotations

import httpx
from pydantic import JsonValue

from ai_reliability.clock import Clock
from ai_reliability.domain.enums import SystemKind
from ai_reliability.domain.system import SystemDescriptor
from ai_reliability.domain.test_case import TestInput
from ai_reliability.systems.http.endpoint_safety import EndpointPolicy
from ai_reliability.systems.http.mappers import rag_mapper
from ai_reliability.systems.http.system import CallableSystem, HTTPSystem, InProcessHandler


def rag_request(test: TestInput, descriptor: SystemDescriptor) -> JsonValue:
    """Request body for RAG endpoints: the query plus optional fixture documents."""
    return {
        "query": test.prompt,
        "fixture_documents": [d.model_dump(mode="json") for d in test.documents],
        "top_k": descriptor.rag_config.top_k if descriptor.rag_config else None,
    }


class RAGSystemAdapter(HTTPSystem):
    """HTTP adapter for RAG pipelines using the canonical RAG contract."""

    def __init__(
        self,
        descriptor: SystemDescriptor,
        *,
        policy: EndpointPolicy | None = None,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            descriptor.model_copy(update={"kind": SystemKind.RAG}),
            policy=policy,
            mapper=rag_mapper,
            request_builder=rag_request,
            timeout_s=timeout_s,
            client=client,
            clock=clock,
        )


def in_process_rag(
    descriptor: SystemDescriptor, handler: InProcessHandler, *, clock: Clock | None = None
) -> CallableSystem:
    """Wrap an in-process RAG pipeline returning the canonical RAG JSON."""
    return CallableSystem(
        descriptor.model_copy(update={"kind": SystemKind.RAG}),
        handler,
        mapper=rag_mapper,
        clock=clock,
    )
