"""Versioned prompt artifacts. Runs record the exact prompt id, version and hash."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from ai_reliability.config.loader import load_model
from ai_reliability.domain.base import DomainModel, sha256_hex
from ai_reliability.domain.content import PromptRef
from ai_reliability.errors import UnknownComponent


class PromptArtifact(DomainModel):
    """An immutable prompt version."""

    prompt_id: str
    version: str
    template: str = Field(min_length=1)
    description: str = ""

    @property
    def sha256(self) -> str:
        return sha256_hex(self.template)

    def ref(self) -> PromptRef:
        return PromptRef(prompt_id=self.prompt_id, version=self.version, sha256=self.sha256)


class PromptRegistry:
    """Loads prompt artifacts from ``<dir>/*.yaml``; (id, version) pairs are unique."""

    def __init__(self) -> None:
        self._prompts: dict[tuple[str, str], PromptArtifact] = {}

    @classmethod
    def from_directory(cls, directory: Path) -> PromptRegistry:
        registry = cls()
        if directory.is_dir():
            for path in sorted(directory.glob("*.yaml")):
                registry.register(load_model(path, PromptArtifact))
        return registry

    def register(self, prompt: PromptArtifact) -> None:
        key = (prompt.prompt_id, prompt.version)
        existing = self._prompts.get(key)
        if existing is not None and existing.sha256 != prompt.sha256:
            raise ValueError(
                f"prompt {prompt.prompt_id}@{prompt.version} already registered with different text;"
                " bump the version instead of editing a released prompt"
            )
        self._prompts[key] = prompt

    def get(self, prompt_id: str, version: str) -> PromptArtifact:
        try:
            return self._prompts[(prompt_id, version)]
        except KeyError:
            raise UnknownComponent(f"prompt {prompt_id}@{version} not found") from None

    def all_prompts(self) -> list[PromptArtifact]:
        return [self._prompts[k] for k in sorted(self._prompts)]
