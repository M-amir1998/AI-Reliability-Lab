"""Judge calibration against deterministic, human or fixture labels."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ai_reliability.domain.base import DomainModel
from ai_reliability.evaluators.judge.client import JudgeClient, JudgeRequest, Rubric
from ai_reliability.stats import cohen_kappa, wilson_interval

LabelSource = Literal["deterministic", "human", "fixture"]


class CalibrationItem(DomainModel):
    """One graded item with a trusted label."""

    item_id: str
    question: str
    response: str
    reference: str | None = None
    label: bool
    label_source: LabelSource = "fixture"


class ConfusionMatrix(DomainModel):
    """Judge (prediction) vs label (truth); positive class = PASS."""

    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn


class CalibrationReport(DomainModel):
    """Agreement metrics. ``precision``/``recall`` refer to the PASS class.

    A high false-positive count is the dangerous direction for release gating:
    the judge passes answers that are actually wrong.
    """

    judge_model: str
    rubric_id: str
    rubric_version: str
    label_source: str
    n: int
    invalid_verdicts: int
    confusion: ConfusionMatrix
    accuracy: float | None
    accuracy_ci: tuple[float, float] | None
    precision: float | None
    recall: float | None
    f1: float | None
    false_pass_rate: float | None
    cohen_kappa: float | None


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def calibration_from_labels(
    predictions: Sequence[bool], labels: Sequence[bool]
) -> tuple[ConfusionMatrix, dict[str, float | None]]:
    """Compute the confusion matrix and metrics for aligned binary predictions and labels."""
    if len(predictions) != len(labels):
        raise ValueError("predictions and labels must align")
    cm = ConfusionMatrix(
        tp=sum(p and t for p, t in zip(predictions, labels, strict=True)),
        fp=sum(p and not t for p, t in zip(predictions, labels, strict=True)),
        tn=sum((not p) and (not t) for p, t in zip(predictions, labels, strict=True)),
        fn=sum((not p) and t for p, t in zip(predictions, labels, strict=True)),
    )
    precision = _ratio(cm.tp, cm.tp + cm.fp)
    recall = _ratio(cm.tp, cm.tp + cm.fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return cm, {
        "accuracy": _ratio(cm.tp + cm.tn, cm.n),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_pass_rate": _ratio(cm.fp, cm.fp + cm.tn),
        "kappa": cohen_kappa(list(predictions), list(labels)) if predictions else None,
    }


async def calibrate_judge(
    client: JudgeClient, rubric: Rubric, items: Sequence[CalibrationItem]
) -> CalibrationReport:
    """Run the judge over labelled items and report agreement with the labels."""
    predictions: list[bool] = []
    labels: list[bool] = []
    invalid = 0
    for item in items:
        verdict = await client.judge(
            JudgeRequest(
                rubric=rubric,
                question=item.question,
                response=item.response,
                reference=item.reference,
            )
        )
        if not verdict.valid or verdict.passed is None:
            invalid += 1
            continue
        predictions.append(verdict.passed)
        labels.append(item.label)
    cm, metrics = calibration_from_labels(predictions, labels)
    prov = client.provenance(rubric)
    sources = sorted({i.label_source for i in items})
    return CalibrationReport(
        judge_model=prov.judge_model,
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.version,
        label_source=",".join(sources),
        n=cm.n,
        invalid_verdicts=invalid,
        confusion=cm,
        accuracy=metrics["accuracy"],
        accuracy_ci=wilson_interval(cm.tp + cm.tn, cm.n) if cm.n else None,
        precision=metrics["precision"],
        recall=metrics["recall"],
        f1=metrics["f1"],
        false_pass_rate=metrics["false_pass_rate"],
        cohen_kappa=metrics["kappa"],
    )
