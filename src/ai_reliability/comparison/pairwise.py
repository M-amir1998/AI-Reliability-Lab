"""Pairwise System A vs System B comparison.

Three preference sources are supported: deterministic evaluator scores, human
preferences (from blinded review) and an optional judge (queried in both
orders to control position bias). Reports always include ties and per-category
breakdowns, and a sign test — never a bare win rate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from scipy import stats as sps

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import Outcome
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.test_case import TestCase
from ai_reliability.evaluators.judge.client import JudgeClient, JudgeRequest, Rubric

Preference = Literal["A", "B", "tie"]


class PairwiseOutcome(DomainModel):
    item_id: str
    category: str
    preference: Preference
    source: str


class PairwiseReport(DomainModel):
    system_a: str
    system_b: str
    source: str
    n: int
    wins_a: int
    wins_b: int
    ties: int
    win_rate_b_excluding_ties: float | None
    sign_test_p_value: float | None
    by_category: dict[str, tuple[int, int, int]]
    inconsistent_judgements: int = 0
    note: str = ""


def _score(results: Sequence[CaseResult]) -> float:
    """Mean per-execution score: 1 for PASS, else mean evaluator score (0 when absent)."""
    values = []
    for r in results:
        if r.outcome is Outcome.PASS:
            values.append(1.0)
        else:
            scores = [e.score for e in r.evaluations if e.score is not None]
            values.append(sum(scores) / len(scores) * 0.99 if scores else 0.0)
    return sum(values) / len(values)


def summarize_pairwise(
    outcomes: Sequence[PairwiseOutcome],
    system_a: str,
    system_b: str,
    source: str,
    *,
    inconsistent: int = 0,
    note: str = "",
) -> PairwiseReport:
    wins_a = sum(o.preference == "A" for o in outcomes)
    wins_b = sum(o.preference == "B" for o in outcomes)
    ties = len(outcomes) - wins_a - wins_b
    decided = wins_a + wins_b
    p = float(sps.binomtest(wins_b, decided, 0.5).pvalue) if decided else None
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for o in outcomes:
        by_cat[o.category][{"A": 0, "B": 1, "tie": 2}[o.preference]] += 1
    return PairwiseReport(
        system_a=system_a,
        system_b=system_b,
        source=source,
        n=len(outcomes),
        wins_a=wins_a,
        wins_b=wins_b,
        ties=ties,
        win_rate_b_excluding_ties=wins_b / decided if decided else None,
        sign_test_p_value=p,
        by_category={k: (v[0], v[1], v[2]) for k, v in sorted(by_cat.items())},
        inconsistent_judgements=inconsistent,
        note=note,
    )


def deterministic_pairwise(
    results_a: Sequence[CaseResult], results_b: Sequence[CaseResult], system_a: str, system_b: str
) -> PairwiseReport:
    """Prefer the system with the higher deterministic score per shared test."""
    by_a: dict[str, list[CaseResult]] = defaultdict(list)
    by_b: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results_a:
        by_a[r.test_id].append(r)
    for r in results_b:
        by_b[r.test_id].append(r)
    outcomes = []
    for test_id in sorted(set(by_a) & set(by_b)):
        sa, sb = _score(by_a[test_id]), _score(by_b[test_id])
        pref: Preference = "tie" if abs(sa - sb) < 1e-9 else ("A" if sa > sb else "B")
        outcomes.append(
            PairwiseOutcome(
                item_id=test_id,
                category=by_a[test_id][0].category,
                preference=pref,
                source="deterministic",
            )
        )
    return summarize_pairwise(outcomes, system_a, system_b, "deterministic")


async def judge_pairwise(
    judge: JudgeClient,
    rubric: Rubric,
    tests: Sequence[TestCase],
    responses_a: Sequence[str],
    responses_b: Sequence[str],
    system_a: str,
    system_b: str,
) -> PairwiseReport:
    """Ask the judge in both orders; inconsistent pairs are scored as ties and counted."""
    outcomes: list[PairwiseOutcome] = []
    inconsistent = 0
    for test, a, b in zip(tests, responses_a, responses_b, strict=True):
        reference = test.expected.reference_text if test.expected else None
        ab = await judge.judge(
            JudgeRequest(
                rubric=rubric,
                question=test.input.prompt,
                response=a,
                response_b=b,
                reference=reference,
            )
        )
        ba = await judge.judge(
            JudgeRequest(
                rubric=rubric,
                question=test.input.prompt,
                response=b,
                response_b=a,
                reference=reference,
            )
        )
        first = ab.preference if ab.valid else None
        second = {"A": "B", "B": "A", "tie": "tie"}.get(ba.preference or "") if ba.valid else None
        if first is None or second is None or first != second:
            inconsistent += 1
            pref: Preference = "tie"
        else:
            pref = first
        outcomes.append(
            PairwiseOutcome(
                item_id=test.test_id, category=test.category, preference=pref, source="judge"
            )
        )
    return summarize_pairwise(
        outcomes,
        system_a,
        system_b,
        "judge",
        inconsistent=inconsistent,
        note="judge queried in both orders; order-inconsistent verdicts counted as ties",
    )
