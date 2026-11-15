"""DuckDB analysis over stored Parquet results (adapter: keeps DuckDB out of the domain)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ai_reliability.errors import UnknownComponent


@dataclass(frozen=True, slots=True)
class RunColumns:
    """Per-execution numeric/categorical columns used for drift analysis."""

    run_ids: tuple[str, ...]
    latency_ms: list[float]
    output_tokens: list[float]
    response_chars: list[float]
    passed: list[float]
    is_error: list[float]
    categories: list[str]
    failure_categories: list[str]


def _paths(results_dir: Path, run_ids: Sequence[str]) -> list[str]:
    paths = []
    for run_id in run_ids:
        path = results_dir / run_id / "results.parquet"
        if not path.exists():
            raise UnknownComponent(f"no results.parquet for run {run_id!r} in {results_dir}")
        paths.append(path.as_posix())
    return paths


def load_columns(results_dir: Path, run_ids: Sequence[str]) -> RunColumns:
    """Load drift-relevant columns for one or more runs with a single DuckDB scan."""
    paths = _paths(results_dir, run_ids)
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT latency_ms, output_tokens, response_chars,
                   CASE WHEN outcome = 'pass' THEN 1.0 ELSE 0.0 END AS passed,
                   CASE WHEN outcome = 'error' THEN 1.0 ELSE 0.0 END AS is_error,
                   category, coalesce(failure_category, 'none') AS failure_category
            FROM read_parquet(?)
            """,
            [paths],
        ).fetchall()
    finally:
        con.close()
    return RunColumns(
        run_ids=tuple(run_ids),
        latency_ms=[float(r[0]) for r in rows if r[0] is not None],
        output_tokens=[float(r[1]) for r in rows if r[1] is not None],
        response_chars=[float(r[2]) for r in rows if r[2] is not None],
        passed=[float(r[3]) for r in rows],
        is_error=[float(r[4]) for r in rows],
        categories=[str(r[5]) for r in rows],
        failure_categories=[str(r[6]) for r in rows],
    )


def category_pass_rates(
    results_dir: Path, run_ids: Sequence[str]
) -> list[tuple[str, str, float, int]]:
    """(run_id, category, pass_rate, n) across runs — e.g. for trend plots."""
    paths = _paths(results_dir, run_ids)
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT run_id, category, avg(CASE WHEN outcome = 'pass' THEN 1.0 ELSE 0.0 END), count(*)
            FROM read_parquet(?)
            GROUP BY run_id, category ORDER BY run_id, category
            """,
            [paths],
        ).fetchall()
    finally:
        con.close()
    return [(str(r[0]), str(r[1]), float(r[2]), int(r[3])) for r in rows]
