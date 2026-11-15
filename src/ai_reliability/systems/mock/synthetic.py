"""Named synthetic systems that exercise every reliability feature without an LLM.

* ``good``   — answers correctly, resists injection, robust to perturbations.
* ``buggy``  — wrong answers and malformed JSON on a deterministic subset; brittle lookup.
* ``unsafe`` — correct on benign tasks, obeys injected instructions and leaks secrets.
* ``slow``   — correct but with multi-second latency.
* ``flaky``  — correct but returns transient 503s that require retries.
"""

from __future__ import annotations

from ai_reliability.domain.base import DomainModel
from ai_reliability.systems.mock.profile import BehaviorRule, MockBehavior, MockProfile

SAFETY_CATEGORIES = (
    "prompt_injection",
    "jailbreak",
    "privacy",
    "safety",
    "instruction_hierarchy",
    "tool_use",
)


class SyntheticSystemSpec(DomainModel):
    """Profile plus human-readable description of a synthetic system."""

    system_id: str
    description: str
    profile: MockProfile


SYNTHETIC_SYSTEMS: dict[str, SyntheticSystemSpec] = {
    spec.system_id: spec
    for spec in (
        SyntheticSystemSpec(
            system_id="good",
            description="Correct, safe and robust reference system.",
            profile=MockProfile(seed=1, matching="fuzzy"),
        ),
        SyntheticSystemSpec(
            system_id="buggy",
            description="Wrong answers on ~25% of correctness tests, malformed JSON on ~30%.",
            profile=MockProfile(
                seed=2,
                matching="exact",
                rules=(
                    BehaviorRule(
                        behavior=MockBehavior.MALFORMED_JSON,
                        categories=("structured_output",),
                        fraction=0.3,
                    ),
                    BehaviorRule(
                        behavior=MockBehavior.INCORRECT,
                        categories=("correctness", "reasoning"),
                        fraction=0.25,
                    ),
                    BehaviorRule(
                        behavior=MockBehavior.HALLUCINATED, categories=("rag",), fraction=0.5
                    ),
                ),
            ),
        ),
        SyntheticSystemSpec(
            system_id="unsafe",
            description="Correct on benign tasks; follows injected instructions and misuses tools.",
            profile=MockProfile(
                seed=3,
                rules=(
                    BehaviorRule(behavior=MockBehavior.TOOL_MISUSE, categories=("tool_use",)),
                    BehaviorRule(
                        behavior=MockBehavior.INJECTION_COMPLIANT, categories=SAFETY_CATEGORIES
                    ),
                ),
            ),
        ),
        SyntheticSystemSpec(
            system_id="slow",
            description="Correct but slow: 2.5-5.5 s simulated latency.",
            profile=MockProfile(seed=4, base_latency_ms=2500, latency_jitter_ms=3000),
        ),
        SyntheticSystemSpec(
            system_id="flaky",
            description="Correct, but 40% of inputs fail once with a retryable 503.",
            profile=MockProfile(seed=5, transient_failure_rate=0.4, transient_failures_per_input=1),
        ),
    )
}
