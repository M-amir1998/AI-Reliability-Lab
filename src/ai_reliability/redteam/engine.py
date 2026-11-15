"""Red-team engine: generate attack suites, execute them, and report attack success by family."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import Outcome
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.test_case import TestSuite
from ai_reliability.redteam.catalog import build_redteam_suite, family_of
from ai_reliability.runner.executor import EvaluationRunner, RunOutcome


class FamilyResult(DomainModel):
    family: str
    attacks: int
    resisted: int
    succeeded: int
    errors: int
    attack_success_rate: float | None
    canary_leaks: int
    unauthorized_tool_calls: int


class RedTeamReport(DomainModel):
    """Attack success per family. "Resisted" means every evaluator passed for that attack."""

    run_id: str
    system_id: str
    suite_version: str
    families: dict[str, FamilyResult]
    attacks: int
    succeeded: int
    attack_success_rate: float | None
    leaked_test_ids: tuple[str, ...]

    @property
    def resistance_rate(self) -> float | None:
        return None if self.attack_success_rate is None else 1.0 - self.attack_success_rate


def _evaluator_failed(result: CaseResult, names: set[str]) -> bool:
    stack = list(result.evaluations)
    while stack:
        e = stack.pop()
        if e.evaluator in names and e.outcome is Outcome.FAIL:
            return True
        stack.extend(e.components)
    return False


def summarize_redteam(
    run_id: str, system_id: str, suite: TestSuite, results: Sequence[CaseResult]
) -> RedTeamReport:
    family_by_test = {t.test_id: family_of(t) or "unknown" for t in suite}
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        grouped[family_by_test.get(r.test_id, "unknown")].append(r)
    families: dict[str, FamilyResult] = {}
    leaked: set[str] = set()
    for family, rs in sorted(grouped.items()):
        errors = sum(r.outcome is Outcome.ERROR for r in rs)
        succeeded = sum(r.outcome is Outcome.FAIL for r in rs)
        valid = len(rs) - errors
        leaks = [r for r in rs if _evaluator_failed(r, {"canary_leak"})]
        leaked.update(r.test_id for r in leaks)
        families[family] = FamilyResult(
            family=family,
            attacks=len(rs),
            resisted=sum(r.outcome is Outcome.PASS for r in rs),
            succeeded=succeeded,
            errors=errors,
            attack_success_rate=succeeded / valid if valid else None,
            canary_leaks=len(leaks),
            unauthorized_tool_calls=sum(
                _evaluator_failed(r, {"tool_policy", "injection_resistance"}) for r in rs
            ),
        )
    attacks = sum(f.attacks - f.errors for f in families.values())
    succeeded = sum(f.succeeded for f in families.values())
    return RedTeamReport(
        run_id=run_id,
        system_id=system_id,
        suite_version=suite.version,
        families=families,
        attacks=attacks,
        succeeded=succeeded,
        attack_success_rate=succeeded / attacks if attacks else None,
        leaked_test_ids=tuple(sorted(leaked)),
    )


class RedTeamEngine:
    """Builds a deterministic attack suite and executes it with a configured runner.

    Errors (timeouts, transport failures) are reported separately and are not
    counted as resisted attacks.
    """

    def __init__(self, runner: EvaluationRunner, system_id: str) -> None:
        self._runner = runner
        self._system_id = system_id

    def build_suite(
        self, families: Sequence[str] = (), *, seed: int = 0, canary: str | None = None
    ) -> TestSuite:
        return build_redteam_suite(families, seed=seed, canary=canary)

    async def run(self, run_id: str, suite: TestSuite) -> tuple[RunOutcome, RedTeamReport]:
        outcome = await self._runner.run(run_id, suite.tests)
        return outcome, summarize_redteam(run_id, self._system_id, suite, outcome.results)
