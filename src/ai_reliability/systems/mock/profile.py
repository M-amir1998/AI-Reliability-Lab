"""Declarative behaviour profiles for deterministic mock systems."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from ai_reliability.domain.base import DomainModel


class MockBehavior(StrEnum):
    """How a mock system responds to a given input."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNSAFE = "unsafe"
    HALLUCINATED = "hallucinated"
    MALFORMED_JSON = "malformed_json"
    INJECTION_COMPLIANT = "injection_compliant"
    TOOL_MISUSE = "tool_misuse"
    TIMEOUT = "timeout"
    HTTP_FAILURE = "http_failure"
    REFUSAL = "refusal"
    VERBOSE = "verbose"


class BehaviorRule(DomainModel):
    """Assign ``behavior`` to inputs matching all given selectors.

    ``fraction`` selects a deterministic subset of matching tests (hash of
    ``seed``, rule index and test ID), so "10% of correctness tests are wrong"
    is reproducible across runs and machines.
    """

    behavior: MockBehavior
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    test_ids: tuple[str, ...] = ()
    test_id_prefixes: tuple[str, ...] = ()
    fraction: float = Field(default=1.0, ge=0.0, le=1.0)


class MockProfile(DomainModel):
    """Full configuration of a :class:`~ai_reliability.systems.mock.system.MockSystem`."""

    seed: int = 0
    rules: tuple[BehaviorRule, ...] = ()
    default_behavior: MockBehavior = MockBehavior.CORRECT
    unknown_input_behavior: MockBehavior = MockBehavior.CORRECT
    matching: Literal["exact", "fuzzy"] = "fuzzy"
    fuzzy_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    base_latency_ms: float = Field(default=300.0, ge=0.0)
    latency_jitter_ms: float = Field(default=200.0, ge=0.0)
    simulate_delay: bool = False
    transient_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    transient_failures_per_input: int = Field(default=1, ge=0)
    output_token_ratio: float = Field(default=0.25, gt=0.0)
