"""Typed configuration models (validated from YAML; no free-form dictionaries)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import CapturePolicy, TestStatus
from ai_reliability.domain.system import RagConfig, SystemResponse, ToolConfig
from ai_reliability.systems.mock.profile import MockProfile
from ai_reliability.systems.mock.scripted import ScriptRule

SystemType = Literal[
    "synthetic",
    "mock",
    "scripted",
    "http",
    "openai_compatible",
    "inferencelab",
    "agent",
    "agentruntime",
    "rag",
]


class Pricing(DomainModel):
    """Per-model token prices. Always configuration; never hard-coded."""

    input_per_1k_usd: float = Field(ge=0.0)
    output_per_1k_usd: float = Field(ge=0.0)

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1000.0 * self.input_per_1k_usd
            + output_tokens / 1000.0 * self.output_per_1k_usd
        )


class RateLimitConfig(DomainModel):
    """Client-side quotas for external endpoints."""

    requests_per_second: float | None = Field(default=None, gt=0)
    tokens_per_minute: int | None = Field(default=None, gt=0)


class PromptSelector(DomainModel):
    """Reference to a versioned prompt in the prompt registry."""

    prompt_id: str
    version: str


class ScriptedConfig(DomainModel):
    rules: tuple[ScriptRule, ...] = ()
    default: SystemResponse | None = None


class SystemConfig(DomainModel):
    """Configuration of one system under test. Secrets are referenced by env-var name only."""

    system_id: str
    type: SystemType
    description: str = ""
    synthetic: str | None = None
    mock: MockProfile | None = None
    scripted: ScriptedConfig | None = None
    model: str | None = None
    revision: str | None = None
    endpoint: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int = Field(default=1024, gt=0)
    timeout_s: float = Field(default=60.0, gt=0)
    allow_private_network: bool = False
    allowed_hosts: tuple[str, ...] = ()
    prompt: PromptSelector | None = None
    tool_config: ToolConfig | None = None
    rag_config: RagConfig | None = None
    pricing: Pricing | None = None
    rate_limit: RateLimitConfig | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _type_fields(self) -> SystemConfig:
        if self.type == "synthetic" and not self.synthetic:
            raise ValueError("synthetic systems need 'synthetic: <name>'")
        if self.type == "mock" and self.mock is None:
            raise ValueError("mock systems need a 'mock' profile")
        if self.type == "scripted" and self.scripted is None:
            raise ValueError("scripted systems need 'scripted' rules")
        if (
            self.type
            in {
                "http",
                "openai_compatible",
                "inferencelab",
                "agent",
                "agentruntime",
                "rag",
            }
            and not self.endpoint
        ):
            raise ValueError(f"{self.type} systems need an 'endpoint'")
        if self.type in {"openai_compatible", "inferencelab"} and not self.model:
            raise ValueError(f"{self.type} systems need a 'model'")
        return self


class RunnerSettings(DomainModel):
    """Bounded asynchronous execution settings."""

    concurrency: int = Field(default=8, ge=1, le=256)
    timeout_s: float = Field(default=60.0, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_initial_s: float = Field(default=0.5, ge=0)
    backoff_max_s: float = Field(default=8.0, ge=0)
    evaluator_timeout_s: float = Field(default=30.0, gt=0)


class SuiteFilter(DomainModel):
    tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    statuses: tuple[TestStatus, ...] = (TestStatus.ACTIVE,)


class RunConfig(DomainModel):
    """A complete, reproducible evaluation run definition."""

    run_id: str | None = None
    system: str
    suite: str
    filters: SuiteFilter = Field(default_factory=SuiteFilter)
    repetitions: int = Field(default=1, ge=1, le=100)
    seed: int = 0
    runner: RunnerSettings = Field(default_factory=RunnerSettings)
    capture: CapturePolicy = CapturePolicy.FAILED_CASES
    gates: str | None = None
    baseline: str | None = None
    output_dir: str = "results"
    labels: dict[str, str] = Field(default_factory=dict)


class DriftThresholds(DomainModel):
    psi_warn: float = 0.1
    psi_drift: float = 0.25
    ks_alpha: float = 0.01
    relative_shift_warn: float = 0.1
    relative_shift_drift: float = 0.25


class DriftConfig(DomainModel):
    """Compare a current run against one or more reference runs."""

    reference_runs: tuple[str, ...] = Field(min_length=1)
    current_run: str
    results_dir: str = "results"
    thresholds: DriftThresholds = Field(default_factory=DriftThresholds)


class RedTeamConfig(DomainModel):
    """Red-team campaign: which attack families to generate and which system to attack."""

    system: str
    families: tuple[str, ...] = ()
    seed: int = 0
    canary: str | None = None
    runner: RunnerSettings = Field(default_factory=RunnerSettings)
    output_dir: str = "results"
    max_attack_success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
