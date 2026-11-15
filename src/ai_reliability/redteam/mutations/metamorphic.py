"""Metamorphic testing: expected behaviour must be invariant under known-safe transformations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import Outcome, TestSource
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.test_case import TestCase, TestMetadata
from ai_reliability.errors import UnknownComponent
from ai_reliability.redteam.mutations.operators import OPERATORS


class MetamorphicTest(DomainModel):
    """A source test and a follow-up produced by an answer-preserving transformation.

    Relation: ``outcome_invariant`` — the follow-up must have the same outcome as the
    source (a source PASS turning into a follow-up FAIL is a robustness violation).
    """

    source: TestCase
    follow_up: TestCase
    operator: str
    relation: str = "outcome_invariant"


def mutate_test(test: TestCase, operator: str, *, seed: int = 0) -> TestCase:
    """Derive a mutation test case (same expectation, mutated input, provenance recorded)."""
    op = OPERATORS.get(operator)
    if op is None:
        raise UnknownComponent(f"unknown mutation operator {operator!r}")
    mutated = op.apply(test.input, seed)
    return test.model_copy(
        update={
            "test_id": f"{test.test_id}~{operator}",
            "input": mutated,
            "tags": test.tags | {"mutation", f"mutation:{operator}"},
            "metadata": TestMetadata(
                severity=test.metadata.severity,
                status=test.metadata.status,
                source=TestSource.MUTATION,
                parent_test_id=test.test_id,
                mutation=operator,
                description=f"{operator} mutation of {test.test_id}",
            ),
        }
    )


def build_metamorphic_tests(
    tests: Iterable[TestCase], operators: Sequence[str], *, seed: int = 0
) -> list[MetamorphicTest]:
    """Cross product of tests x operators, skipping no-op mutations."""
    out: list[MetamorphicTest] = []
    for test in tests:
        for operator in operators:
            follow_up = mutate_test(test, operator, seed=seed)
            if follow_up.input == test.input:
                continue
            out.append(MetamorphicTest(source=test, follow_up=follow_up, operator=operator))
    return out


class MetamorphicViolation(DomainModel):
    source_test_id: str
    follow_up_test_id: str
    operator: str
    source_outcome: Outcome
    follow_up_outcome: Outcome


class MetamorphicReport(DomainModel):
    """Violations and per-operator violation rates (over pairs whose source passed)."""

    pairs: int
    violations: tuple[MetamorphicViolation, ...]
    violation_rate_by_operator: dict[str, float]

    @property
    def violation_rate(self) -> float | None:
        return len(self.violations) / self.pairs if self.pairs else None


def check_relations(
    tests: Sequence[MetamorphicTest], results: Sequence[CaseResult]
) -> MetamorphicReport:
    """Compare source vs follow-up outcomes (first repetition of each)."""
    outcome = {r.test_id: r.outcome for r in results if r.repetition == 0}
    violations: list[MetamorphicViolation] = []
    per_op_total: dict[str, int] = defaultdict(int)
    per_op_bad: dict[str, int] = defaultdict(int)
    pairs = 0
    for mt in tests:
        src, fol = outcome.get(mt.source.test_id), outcome.get(mt.follow_up.test_id)
        if src is None or fol is None or src is not Outcome.PASS:
            continue
        pairs += 1
        per_op_total[mt.operator] += 1
        if fol is not src:
            per_op_bad[mt.operator] += 1
            violations.append(
                MetamorphicViolation(
                    source_test_id=mt.source.test_id,
                    follow_up_test_id=mt.follow_up.test_id,
                    operator=mt.operator,
                    source_outcome=src,
                    follow_up_outcome=fol,
                )
            )
    return MetamorphicReport(
        pairs=pairs,
        violations=tuple(violations),
        violation_rate_by_operator={
            op: per_op_bad[op] / n for op, n in sorted(per_op_total.items())
        },
    )
