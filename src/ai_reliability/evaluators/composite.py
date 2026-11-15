"""Composite evaluators: combine components while keeping every component result visible."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ai_reliability.domain.enums import Outcome
from ai_reliability.domain.result import EvaluationResult
from ai_reliability.domain.system import SystemResponse
from ai_reliability.domain.test_case import TestCase
from ai_reliability.evaluators.base import Evaluator


def combine_outcomes(outcomes: Sequence[Outcome], mode: Literal["all", "any"]) -> Outcome:
    """Combine component outcomes. SKIPPED components are ignored; ERROR dominates.

    ``all``: PASS only if every non-skipped component passes.
    ``any``: PASS if at least one non-skipped component passes.
    """
    relevant = [o for o in outcomes if o is not Outcome.SKIPPED]
    if not relevant:
        return Outcome.SKIPPED
    if Outcome.ERROR in relevant:
        return Outcome.ERROR
    if Outcome.NEEDS_REVIEW in relevant and mode == "all":
        return Outcome.NEEDS_REVIEW
    passes = [o is Outcome.PASS for o in relevant]
    ok = all(passes) if mode == "all" else any(passes)
    return Outcome.PASS if ok else Outcome.FAIL


class CompositeEvaluator:
    """Runs component evaluators; the composite score is a transparent weighted mean.

    ``score = sum(w_i * s_i) / sum(w_i)`` over components that produced a score.
    The pass/fail outcome is NOT derived from the score: it uses ``mode``.
    """

    def __init__(
        self,
        name: str,
        components: Sequence[Evaluator],
        *,
        mode: Literal["all", "any"] = "all",
        weights: Sequence[float] | None = None,
        version: str = "1",
    ) -> None:
        if not components:
            raise ValueError("composite needs at least one component")
        if weights is not None and len(weights) != len(components):
            raise ValueError("weights must align with components")
        self.name = name
        self.mode = mode
        self._components = tuple(components)
        self._weights = tuple(weights) if weights is not None else (1.0,) * len(components)
        component_versions = "+".join(f"{c.name}@{c.version}" for c in self._components)
        self.version = f"{version}[{component_versions}]"

    @property
    def components(self) -> tuple[Evaluator, ...]:
        return self._components

    def validate(self, test_case: TestCase) -> None:
        for component in self._components:
            component.validate(test_case)

    async def evaluate(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        results = [await c.evaluate(test_case, response) for c in self._components]
        outcome = combine_outcomes([r.outcome for r in results], self.mode)
        scored = [
            (w, r.score) for w, r in zip(self._weights, results, strict=True) if r.score is not None
        ]
        total_w = sum(w for w, _ in scored)
        score = sum(w * s for w, s in scored) / total_w if total_w > 0 else None
        failing = [r for r in results if r.outcome in {Outcome.FAIL, Outcome.ERROR}]
        reason = (
            "; ".join(f"{r.evaluator}: {r.reason}" for r in failing)
            if failing
            else f"{sum(r.passed for r in results)}/{len(results)} components passed"
        )
        return EvaluationResult(
            evaluator=self.name,
            evaluator_version=self.version,
            outcome=outcome,
            score=score,
            reason=reason,
            failure_category=failing[0].failure_category if failing else None,
            components=tuple(results),
        )
