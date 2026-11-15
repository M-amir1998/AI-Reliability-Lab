"""Lightweight failure clustering (no embeddings): group by category, failure type, evaluator, error."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import Outcome
from ai_reliability.domain.result import CaseResult

_VOLATILE = re.compile(r"\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"")


def reason_signature(reason: str) -> str:
    """Normalize a failure reason so failures differing only in numbers/quoted values cluster."""
    return _VOLATILE.sub("#", reason.split(":", 1)[-1]).strip()[:120]


class FailureCluster(DomainModel):
    """A group of similar failures. Describes the observed failure only, not its cause."""

    cluster_id: str
    category: str
    failure_category: str
    evaluator: str
    error_code: str | None
    signature: str
    count: int
    test_ids: tuple[str, ...]
    top_tags: tuple[str, ...]


def cluster_failures(results: Sequence[CaseResult]) -> list[FailureCluster]:
    groups: dict[tuple[str, str, str, str | None, str], list[CaseResult]] = defaultdict(list)
    for r in results:
        if r.outcome is Outcome.PASS:
            continue
        evaluator = r.failure_reason.split(":", 1)[0] if r.failure_reason else "system"
        key = (
            r.category,
            r.failure_category.value if r.failure_category else "unknown",
            evaluator,
            r.error_code.value if r.error_code else None,
            reason_signature(r.failure_reason),
        )
        groups[key].append(r)
    clusters = []
    for index, (key, rs) in enumerate(
        sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][:3]))
    ):
        tags = Counter(tag for r in rs for tag in r.tags if not tag.startswith("family:"))
        clusters.append(
            FailureCluster(
                cluster_id=f"c{index + 1:03d}",
                category=key[0],
                failure_category=key[1],
                evaluator=key[2],
                error_code=key[3],
                signature=key[4],
                count=len(rs),
                test_ids=tuple(sorted({r.test_id for r in rs})),
                top_tags=tuple(t for t, _ in tags.most_common(3)),
            )
        )
    return clusters
