"""System registry and factory: configuration -> descriptor -> SystemUnderTest."""

from __future__ import annotations

from pathlib import Path

import httpx

from ai_reliability.clock import Clock
from ai_reliability.config.loader import load_model
from ai_reliability.config.models import SystemConfig
from ai_reliability.domain.enums import SystemKind
from ai_reliability.domain.system import SystemDescriptor
from ai_reliability.errors import ConfigurationError, UnknownComponent
from ai_reliability.systems.agent.adapter import AgentRuntimeSystemAdapter, AgentSystemAdapter
from ai_reliability.systems.base import SystemUnderTest
from ai_reliability.systems.http.endpoint_safety import EndpointPolicy
from ai_reliability.systems.http.system import HTTPSystem
from ai_reliability.systems.mock.knowledge import KnowledgeBase
from ai_reliability.systems.mock.scripted import ScriptedSystem
from ai_reliability.systems.mock.synthetic import SYNTHETIC_SYSTEMS
from ai_reliability.systems.mock.system import MockSystem
from ai_reliability.systems.openai_compatible.system import OpenAICompatibleSystem
from ai_reliability.systems.prompts import PromptRegistry
from ai_reliability.systems.rag.adapter import RAGSystemAdapter

_KIND = {
    "synthetic": SystemKind.MOCK,
    "mock": SystemKind.MOCK,
    "scripted": SystemKind.SCRIPTED,
    "http": SystemKind.HTTP,
    "openai_compatible": SystemKind.OPENAI_COMPATIBLE,
    "inferencelab": SystemKind.OPENAI_COMPATIBLE,
    "agent": SystemKind.AGENT,
    "agentruntime": SystemKind.AGENT,
    "rag": SystemKind.RAG,
}


class SystemRegistry:
    """Known systems: built-in synthetic systems plus YAML configs from directories."""

    def __init__(self, prompts: PromptRegistry | None = None) -> None:
        self._configs: dict[str, SystemConfig] = {}
        self._sources: dict[str, str] = {}
        self._prompts = prompts or PromptRegistry()
        for name, spec in SYNTHETIC_SYSTEMS.items():
            self.register(
                SystemConfig(
                    system_id=f"synthetic/{name}",
                    type="synthetic",
                    synthetic=name,
                    description=spec.description,
                ),
                source="builtin",
            )

    @classmethod
    def from_directory(
        cls, directory: Path, prompts: PromptRegistry | None = None
    ) -> SystemRegistry:
        registry = cls(prompts)
        if directory.is_dir():
            for path in sorted(directory.glob("*.yaml")):
                registry.register(load_model(path, SystemConfig), source=str(path))
        return registry

    def register(self, config: SystemConfig, *, source: str = "api") -> None:
        if config.system_id in self._configs:
            raise ConfigurationError(f"duplicate system_id {config.system_id!r} ({source})")
        self._configs[config.system_id] = config
        self._sources[config.system_id] = source

    def resolve(self, ref: str) -> SystemConfig:
        """Resolve a system ID or a path to a YAML system config."""
        if ref in self._configs:
            return self._configs[ref]
        path = Path(ref)
        if path.suffix in {".yaml", ".yml"} and path.is_file():
            return load_model(path, SystemConfig)
        raise UnknownComponent(f"unknown system {ref!r}; see `aireliability systems list`")

    def entries(self) -> list[tuple[SystemConfig, str]]:
        return [(self._configs[k], self._sources[k]) for k in sorted(self._configs)]

    def descriptor(self, config: SystemConfig) -> SystemDescriptor:
        """Registry record for a config; records prompt hash, never secret values."""
        prompt_ref = None
        if config.prompt is not None:
            prompt_ref = self._prompts.get(config.prompt.prompt_id, config.prompt.version).ref()
        return SystemDescriptor(
            system_id=config.system_id,
            kind=_KIND[config.type],
            model=config.model,
            revision=config.revision,
            prompt=prompt_ref,
            tool_config=config.tool_config,
            rag_config=config.rag_config,
            endpoint=config.endpoint,
            api_key_env=config.api_key_env,
            temperature=config.temperature,
            seed=config.seed,
            metadata={**config.metadata, "type": config.type},
        )

    def build(
        self,
        config: SystemConfig,
        *,
        knowledge: KnowledgeBase | None = None,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> SystemUnderTest:
        """Instantiate the adapter for ``config``. Mocks receive oracle ``knowledge``."""
        descriptor = self.descriptor(config)
        policy = EndpointPolicy(
            allow_private_network=config.allow_private_network, allowed_hosts=config.allowed_hosts
        )
        if config.type in {"synthetic", "mock"}:
            if config.type == "synthetic":
                name = config.synthetic or ""
                if name not in SYNTHETIC_SYSTEMS:
                    raise UnknownComponent(f"unknown synthetic system {name!r}")
                profile = SYNTHETIC_SYSTEMS[name].profile
            else:
                assert config.mock is not None  # guaranteed by SystemConfig validation
                profile = config.mock
            return MockSystem(
                config.system_id,
                profile,
                knowledge,
                clock=clock,
                revision=config.revision or "mock-1",
            )
        if config.type == "scripted":
            assert config.scripted is not None
            return ScriptedSystem(config.system_id, config.scripted.rules, config.scripted.default)
        if config.type in {"openai_compatible", "inferencelab"}:
            default_prompt = None
            if config.prompt is not None:
                default_prompt = self._prompts.get(
                    config.prompt.prompt_id, config.prompt.version
                ).template
            return OpenAICompatibleSystem(
                descriptor,
                policy=policy,
                max_tokens=config.max_tokens,
                timeout_s=config.timeout_s,
                client=client,
                clock=clock,
                default_system_prompt=default_prompt,
            )
        if config.type == "agent":
            return AgentSystemAdapter(
                descriptor, policy=policy, timeout_s=config.timeout_s, client=client, clock=clock
            )
        if config.type == "agentruntime":
            return AgentRuntimeSystemAdapter(
                descriptor, policy=policy, timeout_s=config.timeout_s, client=client, clock=clock
            )
        if config.type == "rag":
            return RAGSystemAdapter(
                descriptor, policy=policy, timeout_s=config.timeout_s, client=client, clock=clock
            )
        return HTTPSystem(
            descriptor, policy=policy, timeout_s=config.timeout_s, client=client, clock=clock
        )
