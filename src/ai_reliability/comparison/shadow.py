"""Offline shadow evaluation: replay captured, sanitized traces (or synthetic workloads)
against a baseline and a candidate, and compare behaviour without reference answers.

Nothing here sends live production traffic. Only traces captured with
REDACTED_TEXT or FULL_TEXT policy are replayable.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.system import SystemResponse
from ai_reliability.domain.test_case import TestInput
from ai_reliability.evaluators.base import parse_json_response
from ai_reliability.evaluators.deterministic.operational import looks_like_refusal
from ai_reliability.evaluators.safety.evaluators import canary_leak_forms
from ai_reliability.stats import percentile
from ai_reliability.systems.base import SystemUnderTest
from ai_reliability.traces.model import TraceRecord


class ShadowSideStats(DomainModel):
    errors: int
    refusals: int
    invalid_json: int
    canary_leaks: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None


class ShadowReport(DomainModel):
    traces: int
    replayed: int
    skipped_not_replayable: int
    agreement_rate: float | None
    baseline: ShadowSideStats
    candidate: ShadowSideStats
    disagreements: tuple[str, ...]
    note: str = "Reference-free comparison; agreement is normalized-text equality, not correctness."


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def _stats(
    responses: Sequence[SystemResponse], *, expect_json: Sequence[bool], canary: str | None
) -> ShadowSideStats:
    latencies = [r.timing.e2e_ms for r in responses if r.timing.e2e_ms is not None]
    return ShadowSideStats(
        errors=sum(r.error is not None for r in responses),
        refusals=sum(looks_like_refusal(r.text) for r in responses if r.ok),
        invalid_json=sum(
            1
            for r, j in zip(responses, expect_json, strict=True)
            if j and r.ok and not parse_json_response(r.text).ok
        ),
        canary_leaks=sum(bool(canary and canary_leak_forms(r.text, canary)) for r in responses),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
    )


async def run_shadow(
    traces: Sequence[TraceRecord],
    baseline: SystemUnderTest,
    candidate: SystemUnderTest,
    *,
    canary: str | None = None,
) -> ShadowReport:
    replayable = [t for t in traces if t.replayable]
    base_responses: list[SystemResponse] = []
    cand_responses: list[SystemResponse] = []
    expect_json: list[bool] = []
    disagreements: list[str] = []
    for trace in replayable:
        test_input = TestInput(
            prompt=trace.input_text or "",
            response_format="json" if trace.metadata.get("response_format") == "json" else "text",
        )
        b = await baseline.execute(test_input)
        c = await candidate.execute(test_input)
        base_responses.append(b)
        cand_responses.append(c)
        expect_json.append(test_input.response_format == "json")
        if _norm(b.text) != _norm(c.text) or (b.error is None) != (c.error is None):
            disagreements.append(trace.trace_id)
    n = len(replayable)
    return ShadowReport(
        traces=len(traces),
        replayed=n,
        skipped_not_replayable=len(traces) - n,
        agreement_rate=(n - len(disagreements)) / n if n else None,
        baseline=_stats(base_responses, expect_json=expect_json, canary=canary),
        candidate=_stats(cand_responses, expect_json=expect_json, canary=canary),
        disagreements=tuple(disagreements[:50]),
    )
