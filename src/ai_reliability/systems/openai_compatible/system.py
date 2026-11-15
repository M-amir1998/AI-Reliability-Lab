"""OpenAI-compatible chat-completions adapter (OpenAI, vLLM, llama.cpp, InferenceLab, ...).

Uses ``httpx`` directly so no vendor SDK is required. Untrusted context
(documents, tool outputs) is delimited explicitly in the user message; this is
a formatting convention, not a security boundary.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ai_reliability.clock import Clock, SystemClock
from ai_reliability.domain.content import ToolCall
from ai_reliability.domain.enums import ErrorCode
from ai_reliability.domain.system import SystemDescriptor, SystemResponse, Timing, TokenUsage
from ai_reliability.domain.test_case import TestInput
from ai_reliability.systems.http.endpoint_safety import EndpointPolicy, validate_endpoint
from ai_reliability.systems.http.mappers import parse_arguments
from ai_reliability.systems.http.transport import auth_headers, post_json


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Function(_Lenient):
    name: str
    arguments: JsonValue = None


class _ToolCall(_Lenient):
    id: str | None = None
    function: _Function


class _Message(_Lenient):
    content: str | None = None
    tool_calls: list[_ToolCall] = Field(default_factory=list)


class _Choice(_Lenient):
    message: _Message


class _Usage(_Lenient):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class _ChatCompletion(_Lenient):
    model: str | None = None
    choices: list[_Choice]
    usage: _Usage | None = None


def build_messages(test: TestInput) -> list[JsonValue]:
    """Render a TestInput as chat messages, preserving the instruction hierarchy."""
    messages: list[JsonValue] = []
    if test.system_prompt:
        messages.append({"role": "system", "content": test.system_prompt})
    if test.developer_prompt:
        messages.append({"role": "system", "content": f"[developer]\n{test.developer_prompt}"})
    user = test.prompt
    if test.documents:
        docs = "\n\n".join(
            f'<document id="{d.doc_id}">\n{d.text}\n</document>' for d in test.documents
        )
        user = f"{user}\n\n<untrusted_context>\n{docs}\n</untrusted_context>"
    if test.tool_outputs:
        outs = "\n\n".join(
            f'<tool_output tool="{o.tool}">\n{o.output}\n</tool_output>' for o in test.tool_outputs
        )
        user = f"{user}\n\n<untrusted_tool_outputs>\n{outs}\n</untrusted_tool_outputs>"
    messages.append({"role": "user", "content": user})
    return messages


def build_request(test: TestInput, descriptor: SystemDescriptor, max_tokens: int) -> JsonValue:
    body: dict[str, JsonValue] = {
        "model": descriptor.model,
        "messages": build_messages(test),
        "max_tokens": max_tokens,
    }
    if descriptor.temperature is not None:
        body["temperature"] = descriptor.temperature
    if descriptor.seed is not None:
        body["seed"] = descriptor.seed
    if test.response_format == "json":
        body["response_format"] = {"type": "json_object"}
    if test.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {"type": "object", "additionalProperties": True},
                },
            }
            for t in test.tools
        ]
    return body


def parse_completion(body: JsonValue) -> SystemResponse:
    try:
        completion = _ChatCompletion.model_validate(body)
    except ValidationError:
        return SystemResponse.failure(ErrorCode.INVALID_RESPONSE, "not a chat completion")
    if not completion.choices:
        return SystemResponse.failure(ErrorCode.INVALID_RESPONSE, "no choices returned")
    message = completion.choices[0].message
    usage = completion.usage
    return SystemResponse(
        text=message.content or "",
        tool_calls=tuple(
            ToolCall(
                name=c.function.name, arguments=parse_arguments(c.function.arguments), call_id=c.id
            )
            for c in message.tool_calls
        ),
        usage=None
        if usage is None
        else TokenUsage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens),
        model=completion.model,
    )


class OpenAICompatibleSystem:
    """Evaluates any endpoint implementing ``POST {base_url}/chat/completions``."""

    def __init__(
        self,
        descriptor: SystemDescriptor,
        *,
        policy: EndpointPolicy | None = None,
        max_tokens: int = 1024,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
        default_system_prompt: str | None = None,
    ) -> None:
        self._default_system_prompt = default_system_prompt
        if not descriptor.endpoint or not descriptor.model:
            raise ValueError(f"system {descriptor.system_id} needs endpoint and model")
        base = validate_endpoint(descriptor.endpoint, policy or EndpointPolicy())
        self._url = base.rstrip("/") + "/chat/completions"
        self._descriptor = descriptor
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._clock = clock or SystemClock()

    @property
    def descriptor(self) -> SystemDescriptor:
        return self._descriptor

    async def execute(self, test: TestInput) -> SystemResponse:
        if test.system_prompt is None and self._default_system_prompt is not None:
            test = test.model_copy(update={"system_prompt": self._default_system_prompt})
        outcome = await post_json(
            self._client,
            self._url,
            build_request(test, self._descriptor, self._max_tokens),
            headers=auth_headers(self._descriptor.api_key_env),
            timeout_s=self._timeout_s,
            clock=self._clock,
        )
        response = outcome.error or parse_completion(outcome.body)
        return response.model_copy(update={"timing": Timing(e2e_ms=outcome.latency_ms)})

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
