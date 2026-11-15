"""Agent system adapters (AgentRuntime-ready, without a hard dependency).

The canonical agent response contract is::

    {
      "final_answer": "...",
      "status": "completed" | "failed" | "awaiting_approval",
      "tool_calls": [{"tool": "...", "arguments": {...}, "decision": "allow|deny|require_approval",
                      "approved": true|false|null, "call_id": "..."}],
      "tool_results": [{"tool": "...", "call_id": "...", "output": "...", "is_error": false}],
      "approval_requests": [{"tool": "...", "reason": "..."}],
      "usage": {"input_tokens": 0, "output_tokens": 0}
    }

``AgentRuntimeSystemAdapter`` targets an AgentRuntime-style HTTP endpoint with
that contract; the exact AgentRuntime route is configuration, not code.
"""

from __future__ import annotations

import httpx
from pydantic import JsonValue

from ai_reliability.clock import Clock
from ai_reliability.domain.enums import SystemKind
from ai_reliability.domain.system import SystemDescriptor
from ai_reliability.domain.test_case import TestInput
from ai_reliability.systems.http.endpoint_safety import EndpointPolicy
from ai_reliability.systems.http.mappers import agent_mapper
from ai_reliability.systems.http.system import CallableSystem, HTTPSystem, InProcessHandler


def agent_request(test: TestInput, descriptor: SystemDescriptor) -> JsonValue:
    """Request body for agent endpoints: task, untrusted context, available tools."""
    return {
        "task": test.prompt,
        "system_prompt": test.system_prompt,
        "developer_prompt": test.developer_prompt,
        "context_documents": [d.model_dump(mode="json") for d in test.documents],
        "tool_output_fixtures": [o.model_dump(mode="json") for o in test.tool_outputs],
        "tools": [t.model_dump(mode="json") for t in test.tools],
        "policy_version": descriptor.tool_config.policy_version if descriptor.tool_config else None,
    }


class AgentSystemAdapter(HTTPSystem):
    """HTTP agent adapter using the canonical agent contract."""

    def __init__(
        self,
        descriptor: SystemDescriptor,
        *,
        policy: EndpointPolicy | None = None,
        timeout_s: float = 120.0,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            descriptor.model_copy(update={"kind": SystemKind.AGENT}),
            policy=policy,
            mapper=agent_mapper,
            request_builder=agent_request,
            timeout_s=timeout_s,
            client=client,
            clock=clock,
        )


class AgentRuntimeSystemAdapter(AgentSystemAdapter):
    """Adapter preset for an AgentRuntime deployment exposing the canonical agent contract."""


def in_process_agent(
    descriptor: SystemDescriptor, handler: InProcessHandler, *, clock: Clock | None = None
) -> CallableSystem:
    """Wrap an in-process agent callable returning the canonical agent JSON."""
    return CallableSystem(
        descriptor.model_copy(update={"kind": SystemKind.AGENT}),
        handler,
        mapper=agent_mapper,
        clock=clock,
    )
