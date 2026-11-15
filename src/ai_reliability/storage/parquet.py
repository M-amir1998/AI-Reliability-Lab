"""CaseResult <-> Arrow/Parquet conversion (compressed, metadata-focused)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ai_reliability.domain.regression import CaseComparison
from ai_reliability.domain.result import CaseResult, EvaluationResult
from ai_reliability.errors import ResultCorruption

RESULT_SCHEMA = pa.schema(
    [
        ("execution_id", pa.string()),
        ("run_id", pa.string()),
        ("test_id", pa.string()),
        ("test_hash", pa.string()),
        ("category", pa.string()),
        ("severity", pa.string()),
        ("tags", pa.list_(pa.string())),
        ("repetition", pa.int32()),
        ("outcome", pa.string()),
        ("failure_category", pa.string()),
        ("failure_reason", pa.string()),
        ("error_code", pa.string()),
        ("latency_ms", pa.float64()),
        ("ttft_ms", pa.float64()),
        ("input_tokens", pa.int64()),
        ("output_tokens", pa.int64()),
        ("cost_usd", pa.float64()),
        ("response_chars", pa.int64()),
        ("tool_call_count", pa.int32()),
        ("attempts", pa.int32()),
        ("input_sha256", pa.string()),
        ("response_sha256", pa.string()),
        ("response_text", pa.string()),
        ("evaluations_json", pa.string()),
    ]
)


def results_to_table(results: Sequence[CaseResult]) -> pa.Table:
    rows: dict[str, list[object]] = {name: [] for name in RESULT_SCHEMA.names}
    for r in results:
        data = r.model_dump(mode="json")
        for name in RESULT_SCHEMA.names:
            if name == "evaluations_json":
                rows[name].append(json.dumps(data["evaluations"], separators=(",", ":")))
            else:
                rows[name].append(data[name])
    return pa.Table.from_pydict(rows, schema=RESULT_SCHEMA)


def table_to_results(table: pa.Table) -> list[CaseResult]:
    out: list[CaseResult] = []
    for row in table.to_pylist():
        evaluations = json.loads(row.pop("evaluations_json") or "[]")
        try:
            out.append(
                CaseResult.model_validate(
                    {
                        **row,
                        "tags": tuple(row["tags"] or ()),
                        "evaluations": [EvaluationResult.model_validate(e) for e in evaluations],
                    }
                )
            )
        except ValueError as exc:
            raise ResultCorruption(f"invalid result row {row.get('execution_id')}: {exc}") from exc
    return out


def write_results(path: Path, results: Sequence[CaseResult]) -> None:
    pq.write_table(results_to_table(results), path, compression="zstd")


def read_results(path: Path) -> list[CaseResult]:
    if not path.exists():
        raise ResultCorruption(f"missing results file {path}")
    return table_to_results(pq.read_table(path))


def write_comparisons(path: Path, cases: Sequence[CaseComparison]) -> None:
    rows = [c.model_dump(mode="json") for c in cases]
    schema = pa.schema(
        [
            ("test_id", pa.string()),
            ("category", pa.string()),
            ("severity", pa.string()),
            ("status", pa.string()),
            ("baseline_pass_rate", pa.float64()),
            ("candidate_pass_rate", pa.float64()),
            ("baseline_outcome", pa.string()),
            ("candidate_outcome", pa.string()),
            ("reason", pa.string()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="zstd")
