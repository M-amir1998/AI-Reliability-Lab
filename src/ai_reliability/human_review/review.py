"""Optional human review: queue NEEDS_REVIEW cases, collect pseudonymous labels, blind pairwise items.

Reviewer identity is an optional free-text pseudonym; no account data is stored.
Review files live outside the immutable run directory (``results/reviews/``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from ai_reliability.determinism import stable_unit
from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import Outcome
from ai_reliability.domain.result import CaseResult
from ai_reliability.errors import ConfigurationError, UnknownComponent
from ai_reliability.evaluators.judge.calibration import ConfusionMatrix, calibration_from_labels


class ReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class ReviewItem(DomainModel):
    item_id: str
    run_id: str
    test_id: str
    execution_id: str
    reason: str
    response_excerpt: str | None = None


class ReviewLabel(DomainModel):
    item_id: str
    verdict: ReviewVerdict
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    comment: str = Field(default="", max_length=2000)
    reviewer: str | None = Field(default=None, max_length=64)
    labelled_at: datetime


def build_queue(
    results: Sequence[CaseResult], *, include_failures: bool = False
) -> list[ReviewItem]:
    """Queue NEEDS_REVIEW executions (and optionally all failures) for human review."""
    wanted = {Outcome.NEEDS_REVIEW} | ({Outcome.FAIL} if include_failures else set())
    return [
        ReviewItem(
            item_id=r.execution_id,
            run_id=r.run_id,
            test_id=r.test_id,
            execution_id=r.execution_id,
            reason=r.failure_reason or r.outcome.value,
            response_excerpt=(r.response_text or "")[:1000] or None,
        )
        for r in results
        if r.outcome in wanted
    ]


class ReviewStore:
    """JSONL-backed queue and label log for one run."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.queue_path = root / f"{run_id}.queue.jsonl"
        self.labels_path = root / f"{run_id}.labels.jsonl"

    def write_queue(self, items: Sequence[ReviewItem]) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text(
            "".join(i.model_dump_json() + "\n" for i in items), encoding="utf-8"
        )

    def queue(self) -> list[ReviewItem]:
        if not self.queue_path.exists():
            return []
        return [
            ReviewItem.model_validate_json(line)
            for line in self.queue_path.read_text("utf-8").splitlines()
            if line
        ]

    def add_label(
        self,
        item_id: str,
        verdict: ReviewVerdict,
        *,
        score: float | None = None,
        comment: str = "",
        reviewer: str | None = None,
    ) -> ReviewLabel:
        if item_id not in {i.item_id for i in self.queue()}:
            raise UnknownComponent(f"review item {item_id!r} is not in the queue")
        label = ReviewLabel(
            item_id=item_id,
            verdict=verdict,
            score=score,
            comment=comment,
            reviewer=reviewer,
            labelled_at=datetime.now(UTC),
        )
        with self.labels_path.open("a", encoding="utf-8") as fh:
            fh.write(label.model_dump_json() + "\n")
        return label

    def labels(self) -> dict[str, ReviewLabel]:
        """Latest label per item."""
        if not self.labels_path.exists():
            return {}
        out: dict[str, ReviewLabel] = {}
        for line in self.labels_path.read_text("utf-8").splitlines():
            if line:
                label = ReviewLabel.model_validate_json(line)
                out[label.item_id] = label
        return out

    def pending(self) -> list[ReviewItem]:
        done = self.labels()
        return [i for i in self.queue() if i.item_id not in done]


# ---------------------------------------------------------------------------
# Blinded pairwise review
# ---------------------------------------------------------------------------


class BlindedPair(DomainModel):
    pair_id: str
    question: str
    left: str
    right: str


class BlindingKey(DomainModel):
    """Maps each pair to which system appears on the left. Keep separate from reviewers."""

    system_a: str
    system_b: str
    left_is_a: dict[str, bool]


def blind_pairs(
    items: Sequence[tuple[str, str, str, str]], system_a: str, system_b: str, *, seed: int = 0
) -> tuple[list[BlindedPair], BlindingKey]:
    """``items`` = (pair_id, question, response_a, response_b). Side assignment is seeded."""
    pairs: list[BlindedPair] = []
    key: dict[str, bool] = {}
    for pair_id, question, a, b in items:
        left_is_a = stable_unit("blind", seed, pair_id) < 0.5
        key[pair_id] = left_is_a
        pairs.append(
            BlindedPair(
                pair_id=pair_id,
                question=question,
                left=a if left_is_a else b,
                right=b if left_is_a else a,
            )
        )
    return pairs, BlindingKey(system_a=system_a, system_b=system_b, left_is_a=key)


def unblind(preferences: dict[str, str], key: BlindingKey) -> dict[str, str]:
    """Convert reviewer preferences ('left'|'right'|'tie') to 'A'|'B'|'tie'."""
    out: dict[str, str] = {}
    for pair_id, pref in preferences.items():
        if pair_id not in key.left_is_a:
            raise UnknownComponent(f"pair {pair_id!r} not in blinding key")
        if pref == "tie":
            out[pair_id] = "tie"
        elif pref in {"left", "right"}:
            left_is_a = key.left_is_a[pair_id]
            out[pair_id] = "A" if (pref == "left") == left_is_a else "B"
        else:
            raise ConfigurationError(f"invalid preference {pref!r}")
    return out


class HumanJudgeAgreement(DomainModel):
    n: int
    confusion: ConfusionMatrix
    accuracy: float | None
    cohen_kappa: float | None


def judge_vs_human(judge: dict[str, bool], human: dict[str, ReviewLabel]) -> HumanJudgeAgreement:
    """Agreement of judge PASS/FAIL decisions with human labels on shared items."""
    shared = sorted(set(judge) & set(human))
    preds = [judge[i] for i in shared]
    truth = [human[i].verdict is ReviewVerdict.PASS for i in shared]
    cm, metrics = calibration_from_labels(preds, truth)
    return HumanJudgeAgreement(
        n=len(shared), confusion=cm, accuracy=metrics["accuracy"], cohen_kappa=metrics["kappa"]
    )


def export_labels(store: ReviewStore) -> str:
    return json.dumps({k: v.model_dump(mode="json") for k, v in store.labels().items()}, indent=2)
