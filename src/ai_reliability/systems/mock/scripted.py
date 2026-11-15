"""Scripted system: ordered regex rules mapping prompts to fixed responses."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import SystemKind
from ai_reliability.domain.system import SystemDescriptor, SystemResponse
from ai_reliability.domain.test_case import TestInput
from ai_reliability.systems.mock.knowledge import input_text


class ScriptRule(DomainModel):
    """If ``pattern`` matches the selected input text, return ``response``."""

    pattern: str
    scope: str = Field(default="prompt", pattern="^(prompt|all)$")
    response: SystemResponse

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value)
        return value


class ScriptedSystem:
    """Deterministic system for fixtures: first matching rule wins."""

    def __init__(
        self,
        system_id: str,
        rules: tuple[ScriptRule, ...],
        default: SystemResponse | None = None,
    ) -> None:
        self._rules = tuple((re.compile(r.pattern, re.IGNORECASE | re.DOTALL), r) for r in rules)
        self._default = default or SystemResponse(text="I don't know.")
        self._descriptor = SystemDescriptor(
            system_id=system_id, kind=SystemKind.SCRIPTED, model=f"scripted/{system_id}"
        )
        self.calls = 0

    @property
    def descriptor(self) -> SystemDescriptor:
        return self._descriptor

    async def execute(self, test: TestInput) -> SystemResponse:
        self.calls += 1
        for pattern, rule in self._rules:
            haystack = test.prompt if rule.scope == "prompt" else input_text(test)
            if pattern.search(haystack):
                return rule.response
        return self._default
