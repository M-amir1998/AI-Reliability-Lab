"""Judge bias probes: verbosity, position/order, format sensitivity, self-preference.

Each probe applies a controlled, meaning-preserving perturbation and measures
how often the judge's decision changes. A non-zero flip rate is evidence of
sensitivity to that factor on *these* items; a zero flip rate is not proof of
absence of bias. These probes do not claim complete bias detection.
"""

from __future__ import annotations

from collections.abc import Sequence

from ai_reliability.domain.base import DomainModel
from ai_reliability.evaluators.judge.calibration import CalibrationItem
from ai_reliability.evaluators.judge.client import JudgeClient, JudgeRequest, Rubric

VERBOSE_PADDING = (
    " In summary, after careful and thorough consideration of all relevant aspects, "
    "this response provides a complete, nuanced and well-reasoned account of the question."
)


class BiasProbeResult(DomainModel):
    """Outcome of one bias probe."""

    probe: str
    n: int
    flips: int
    flip_rate: float | None
    direction: str
    note: str


async def _grade(
    client: JudgeClient, rubric: Rubric, item: CalibrationItem, text: str
) -> bool | None:
    verdict = await client.judge(
        JudgeRequest(rubric=rubric, question=item.question, response=text, reference=item.reference)
    )
    return verdict.passed if verdict.valid else None


async def verbosity_probe(
    client: JudgeClient, rubric: Rubric, items: Sequence[CalibrationItem]
) -> BiasProbeResult:
    """Pad *incorrect* answers with content-free text; count fail -> pass flips."""
    wrong = [i for i in items if not i.label]
    flips = n = 0
    for item in wrong:
        before = await _grade(client, rubric, item, item.response)
        after = await _grade(client, rubric, item, item.response + VERBOSE_PADDING)
        if before is None or after is None:
            continue
        n += 1
        flips += int(before is False and after is True)
    return BiasProbeResult(
        probe="verbosity",
        n=n,
        flips=flips,
        flip_rate=flips / n if n else None,
        direction="fail->pass after adding content-free padding to wrong answers",
        note="padding adds no information; any flip indicates verbosity preference",
    )


async def format_probe(
    client: JudgeClient, rubric: Rubric, items: Sequence[CalibrationItem]
) -> BiasProbeResult:
    """Re-render answers as a markdown bullet; count decision changes in either direction."""
    flips = n = 0
    for item in items:
        before = await _grade(client, rubric, item, item.response)
        after = await _grade(client, rubric, item, f"- {item.response}")
        if before is None or after is None:
            continue
        n += 1
        flips += int(before != after)
    return BiasProbeResult(
        probe="format",
        n=n,
        flips=flips,
        flip_rate=flips / n if n else None,
        direction="any change after markdown re-formatting",
        note="formatting is meaning-preserving here",
    )


async def position_probe(
    client: JudgeClient,
    rubric: Rubric,
    pairs: Sequence[tuple[CalibrationItem, CalibrationItem]],
) -> BiasProbeResult:
    """Ask for a pairwise preference in both orders; inconsistent pairs reveal order bias."""
    inconsistent = n = 0
    first_wins = 0
    for a, b in pairs:
        ab = await client.judge(
            JudgeRequest(
                rubric=rubric,
                question=a.question,
                response=a.response,
                response_b=b.response,
                reference=a.reference,
            )
        )
        ba = await client.judge(
            JudgeRequest(
                rubric=rubric,
                question=a.question,
                response=b.response,
                response_b=a.response,
                reference=a.reference,
            )
        )
        if not (ab.valid and ba.valid) or ab.preference is None or ba.preference is None:
            continue
        n += 1
        swapped = {"A": "B", "B": "A", "tie": "tie"}[ba.preference]
        if ab.preference != swapped:
            inconsistent += 1
        first_wins += int(ab.preference == "A") + int(ba.preference == "A")
    return BiasProbeResult(
        probe="position",
        n=n,
        flips=inconsistent,
        flip_rate=inconsistent / n if n else None,
        direction=f"first-position preferred in {first_wins}/{2 * n} orderings",
        note="a consistent judge's preference follows the answer, not the slot",
    )


def self_preference(
    passed: Sequence[bool], labels: Sequence[bool], authored_by_judge_family: Sequence[bool]
) -> BiasProbeResult:
    """Compare false-pass rates on wrong answers authored by the judge's own model family vs others.

    Requires authorship metadata; with few wrong answers per group the estimate is noisy.
    """
    own = [
        p for p, t, o in zip(passed, labels, authored_by_judge_family, strict=True) if o and not t
    ]
    other = [
        p
        for p, t, o in zip(passed, labels, authored_by_judge_family, strict=True)
        if not o and not t
    ]
    own_rate = sum(own) / len(own) if own else None
    other_rate = sum(other) / len(other) if other else None
    delta = None if own_rate is None or other_rate is None else own_rate - other_rate
    return BiasProbeResult(
        probe="self_preference",
        n=len(own) + len(other),
        flips=sum(own),
        flip_rate=delta,
        direction="false-pass rate (own family) minus false-pass rate (others)",
        note=f"own={own_rate}, other={other_rate}; positive values suggest self-preference",
    )
