"""Versioned evaluator registry. Instances are built per run; there is no global mutable state."""

from __future__ import annotations

from collections.abc import Iterable

from ai_reliability.domain.test_case import TestCase, TestSuite
from ai_reliability.errors import InvalidTestCase, UnknownComponent
from ai_reliability.evaluators.agent.evaluators import (
    ToolCallEvaluator,
    ToolPolicyEvaluator,
    ToolTraceEvaluator,
)
from ai_reliability.evaluators.base import Evaluator
from ai_reliability.evaluators.composite import CompositeEvaluator
from ai_reliability.evaluators.deterministic.operational import (
    CostEvaluator,
    LatencyEvaluator,
    PolicyEvaluator,
)
from ai_reliability.evaluators.deterministic.structured import (
    JSONSchemaEvaluator,
    JSONSemanticEvaluator,
    JSONValidEvaluator,
    UnitTestEvaluator,
)
from ai_reliability.evaluators.deterministic.text import (
    ClassificationEvaluator,
    ContainsEvaluator,
    ExactMatchEvaluator,
    NumericEvaluator,
    RegexEvaluator,
)
from ai_reliability.evaluators.rag.evaluators import (
    CitationEvaluator,
    ContextUtilizationEvaluator,
    GroundednessEvaluator,
    HallucinationEvaluator,
    RetrievalRelevanceEvaluator,
)
from ai_reliability.evaluators.safety.evaluators import (
    CanaryLeakEvaluator,
    InjectionResistanceEvaluator,
)


class EvaluatorRegistry:
    """Maps evaluator names to instances; names are unique, versions are recorded per run."""

    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, evaluator: Evaluator) -> None:
        if evaluator.name in self._evaluators:
            raise ValueError(f"evaluator {evaluator.name!r} already registered")
        self._evaluators[evaluator.name] = evaluator

    def get(self, name: str) -> Evaluator:
        try:
            return self._evaluators[name]
        except KeyError:
            raise UnknownComponent(f"unknown evaluator {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._evaluators

    def names(self) -> list[str]:
        return sorted(self._evaluators)

    def versions(self, names: Iterable[str] | None = None) -> dict[str, str]:
        selected = self.names() if names is None else sorted(set(names))
        return {n: self.get(n).version for n in selected}

    def validate_test(self, test: TestCase) -> None:
        """Check that every referenced evaluator exists and the test supplies what it needs."""
        for name in test.evaluators:
            if name not in self._evaluators:
                raise InvalidTestCase(f"{test.test_id}: unknown evaluator {name!r}")
            self._evaluators[name].validate(test)

    def validate_suite(self, suite: TestSuite) -> None:
        for test in suite:
            self.validate_test(test)


def default_evaluator_registry() -> EvaluatorRegistry:
    """All built-in deterministic evaluators plus built-in composites.

    LLM judges are intentionally *not* registered by default (ADR-001); add one
    explicitly with :meth:`EvaluatorRegistry.register`.
    """
    registry = EvaluatorRegistry()
    simple: list[Evaluator] = [
        ExactMatchEvaluator(),
        ContainsEvaluator(),
        RegexEvaluator(),
        NumericEvaluator(),
        ClassificationEvaluator(),
        JSONValidEvaluator(),
        JSONSchemaEvaluator(),
        JSONSemanticEvaluator(),
        UnitTestEvaluator(),
        LatencyEvaluator(),
        CostEvaluator(),
        PolicyEvaluator(),
        CanaryLeakEvaluator(),
        InjectionResistanceEvaluator(),
        CitationEvaluator(),
        RetrievalRelevanceEvaluator(),
        GroundednessEvaluator(),
        ContextUtilizationEvaluator(),
        HallucinationEvaluator(),
        ToolCallEvaluator(),
        ToolPolicyEvaluator(),
        ToolTraceEvaluator(),
    ]
    for evaluator in simple:
        registry.register(evaluator)
    registry.register(
        CompositeEvaluator(
            "structured_output",
            [registry.get("json_valid"), registry.get("json_schema"), registry.get("unit_test")],
        )
    )
    registry.register(
        CompositeEvaluator(
            "rag_grounded",
            [registry.get("citation"), registry.get("groundedness"), registry.get("hallucination")],
        )
    )
    registry.register(
        CompositeEvaluator(
            "agent_safe",
            [
                registry.get("tool_policy"),
                registry.get("tool_trace"),
                registry.get("injection_resistance"),
            ],
        )
    )
    return registry
