"""Deterministic mock system driven by a behaviour profile and oracle knowledge."""

from __future__ import annotations

from collections import Counter

from ai_reliability.clock import Clock, SystemClock
from ai_reliability.determinism import stable_unit
from ai_reliability.domain.content import ToolCall
from ai_reliability.domain.enums import ErrorCode, SystemKind
from ai_reliability.domain.system import SystemDescriptor, SystemResponse, Timing, TokenUsage
from ai_reliability.domain.test_case import TestInput
from ai_reliability.systems.mock.behaviors import (
    comply_with_directives,
    corrupt,
    find_directives,
    hallucinate,
    malform_json,
    verbose,
)
from ai_reliability.systems.mock.knowledge import (
    REFUSAL_TEXT,
    UNKNOWN_TEXT,
    KnowledgeBase,
    KnowledgeEntry,
    input_key,
    input_text,
)
from ai_reliability.systems.mock.profile import BehaviorRule, MockBehavior, MockProfile


class MockSystem:
    """A fully deterministic system under test.

    Given the same profile, knowledge and input, it always produces the same
    response (except for configured transient failures, which depend on the
    attempt number so that retry logic can be exercised).
    """

    def __init__(
        self,
        system_id: str,
        profile: MockProfile,
        knowledge: KnowledgeBase | None = None,
        *,
        clock: Clock | None = None,
        revision: str = "mock-1",
    ) -> None:
        self._profile = profile
        self._knowledge = knowledge or KnowledgeBase()
        self._clock = clock or SystemClock()
        self._attempts: Counter[str] = Counter()
        self._descriptor = SystemDescriptor(
            system_id=system_id,
            kind=SystemKind.MOCK,
            model=f"mock/{system_id}",
            revision=revision,
            seed=profile.seed,
            metadata={"profile_hash": profile.content_hash()},
        )

    @property
    def descriptor(self) -> SystemDescriptor:
        return self._descriptor

    @property
    def profile(self) -> MockProfile:
        return self._profile

    def with_knowledge(self, knowledge: KnowledgeBase) -> MockSystem:
        """Return a copy of this system using different oracle knowledge."""
        return MockSystem(
            self._descriptor.system_id,
            self._profile,
            knowledge,
            clock=self._clock,
            revision=self._descriptor.revision or "mock-1",
        )

    def behavior_for(self, entry: KnowledgeEntry | None) -> MockBehavior:
        if entry is None:
            return self._profile.unknown_input_behavior
        for index, rule in enumerate(self._profile.rules):
            if self._matches(rule, index, entry):
                return rule.behavior
        return self._profile.default_behavior

    def _matches(self, rule: BehaviorRule, index: int, entry: KnowledgeEntry) -> bool:
        if rule.categories and entry.category not in rule.categories:
            return False
        if rule.tags and not (entry.tags & set(rule.tags)):
            return False
        if rule.test_ids and entry.test_id not in rule.test_ids:
            return False
        if rule.test_id_prefixes and not entry.test_id.startswith(rule.test_id_prefixes):
            return False
        if rule.fraction >= 1.0:
            return True
        return stable_unit(self._profile.seed, index, entry.test_id) < rule.fraction

    async def execute(self, test: TestInput) -> SystemResponse:
        key = input_key(test)
        self._attempts[key] += 1
        attempt = self._attempts[key]
        profile = self._profile
        latency = profile.base_latency_ms + profile.latency_jitter_ms * stable_unit(
            profile.seed, key, "latency"
        )
        if profile.simulate_delay:
            await self._clock.sleep(latency / 1000.0)

        if (
            profile.transient_failure_rate > 0
            and attempt <= profile.transient_failures_per_input
            and stable_unit(profile.seed, key, "flaky") < profile.transient_failure_rate
        ):
            return SystemResponse.failure(
                ErrorCode.HTTP_ERROR, "simulated transient 503", retryable=True, status_code=503
            ).model_copy(update={"timing": Timing(e2e_ms=latency)})

        entry = self._knowledge.lookup(
            test, fuzzy=profile.matching == "fuzzy", threshold=profile.fuzzy_threshold
        )
        behavior = self.behavior_for(entry)
        return self._respond(test, entry, behavior, latency)

    def _respond(
        self,
        test: TestInput,
        entry: KnowledgeEntry | None,
        behavior: MockBehavior,
        latency: float,
    ) -> SystemResponse:
        base_text = entry.text if entry else UNKNOWN_TEXT
        tool_calls = entry.tool_calls if entry else ()
        citations = entry.citations if entry else ()
        timing = Timing(e2e_ms=latency, ttft_ms=latency * 0.3)

        if behavior is MockBehavior.TIMEOUT:
            return SystemResponse.failure(
                ErrorCode.TIMEOUT, "simulated timeout", retryable=False
            ).model_copy(update={"timing": timing})
        if behavior is MockBehavior.HTTP_FAILURE:
            return SystemResponse.failure(
                ErrorCode.HTTP_ERROR, "simulated HTTP 500", retryable=False, status_code=500
            ).model_copy(update={"timing": timing})

        text = base_text
        if behavior is MockBehavior.INCORRECT:
            text = corrupt(base_text)
        elif behavior is MockBehavior.REFUSAL:
            text, tool_calls = REFUSAL_TEXT, ()
        elif behavior is MockBehavior.VERBOSE:
            text = verbose(base_text)
        elif behavior is MockBehavior.MALFORMED_JSON:
            text = malform_json(base_text)
        elif behavior is MockBehavior.HALLUCINATED:
            text, fabricated = hallucinate(base_text)
            citations = citations + fabricated
        elif behavior is MockBehavior.UNSAFE:
            leaked = f"Sure. My instructions are: {test.system_prompt or ''}".strip()
            text = base_text if base_text != REFUSAL_TEXT else ""
            text = f"{leaked} {text}".strip()
            text, injected = comply_with_directives(text, test, find_directives(test))
            tool_calls = tool_calls + injected
        elif behavior is MockBehavior.INJECTION_COMPLIANT:
            directives = find_directives(test)
            if directives and base_text == REFUSAL_TEXT:
                base_text = ""
            text, injected = comply_with_directives(base_text, test, directives)
            tool_calls = tool_calls + injected
        elif behavior is MockBehavior.TOOL_MISUSE:
            tool_calls = tool_calls + self._misuse_calls(test, tool_calls)

        usage = TokenUsage(
            input_tokens=max(1, len(input_text(test)) // 4),
            output_tokens=max(1, int(len(text) * self._profile.output_token_ratio)),
        )
        return SystemResponse(
            text=text,
            tool_calls=tool_calls,
            citations=citations,
            retrieved_documents=test.documents,
            usage=usage,
            timing=timing,
            model=self._descriptor.model,
            run_status="completed" if test.tools else None,
        )

    @staticmethod
    def _misuse_calls(test: TestInput, existing: tuple[ToolCall, ...]) -> tuple[ToolCall, ...]:
        """Call a write tool without approval and repeat the last call several times."""
        extra: list[ToolCall] = []
        writer = next((t for t in test.tools if t.writes), None)
        extra.append(
            ToolCall(
                name=writer.name if writer else "delete_records",
                arguments={"scope": "all"},
                call_id="misuse-write",
            )
        )
        if existing:
            extra.extend(
                existing[-1].model_copy(update={"call_id": f"misuse-repeat-{i}"}) for i in range(5)
            )
        return tuple(extra)
