"""Registry of red-team attack families."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ai_reliability.domain.test_case import TestCase, TestSuite
from ai_reliability.errors import UnknownComponent
from ai_reliability.redteam.common import make_canary
from ai_reliability.redteam.injection.fixtures import (
    direct_injection,
    indirect_injection,
    instruction_hierarchy,
    nested_injection,
    tool_output_injection,
)
from ai_reliability.redteam.jailbreak.fixtures import jailbreak, sensitive_extraction
from ai_reliability.redteam.tool_safety.fixtures import malformed_input, resource_abuse, tool_abuse

FamilyGenerator = Callable[[int, str], tuple[TestCase, ...]]

FAMILIES: dict[str, FamilyGenerator] = {
    "direct_injection": direct_injection,
    "indirect_injection": indirect_injection,
    "tool_output_injection": tool_output_injection,
    "nested_injection": nested_injection,
    "instruction_hierarchy": instruction_hierarchy,
    "jailbreak": jailbreak,
    "sensitive_extraction": sensitive_extraction,
    "tool_abuse": tool_abuse,
    "resource_abuse": resource_abuse,
    "malformed_input": malformed_input,
}

PROMPT_INJECTION_FAMILIES = (
    "direct_injection",
    "indirect_injection",
    "tool_output_injection",
    "nested_injection",
)


def family_of(test: TestCase) -> str | None:
    return next((t.split(":", 1)[1] for t in test.tags if t.startswith("family:")), None)


def build_redteam_suite(
    families: Sequence[str] = (),
    *,
    seed: int = 0,
    canary: str | None = None,
    suite_id: str = "redteam",
) -> TestSuite:
    """Generate a deterministic attack suite. ``seed=0`` reproduces the documented fixtures."""
    selected = tuple(families) or tuple(FAMILIES)
    unknown = [f for f in selected if f not in FAMILIES]
    if unknown:
        raise UnknownComponent(f"unknown red-team families: {', '.join(unknown)}")
    secret = canary or make_canary(seed)
    tests = tuple(t for family in selected for t in FAMILIES[family](seed, secret))
    return TestSuite(
        suite_id=suite_id,
        version=f"1-seed{seed}",
        tier="redteam",
        description=f"Red-team families: {', '.join(selected)}",
        tests=tests,
    )
