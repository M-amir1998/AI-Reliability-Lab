"""Append-only checkpoint journal enabling resumable runs."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from ai_reliability.domain.base import sha256_hex
from ai_reliability.domain.result import CaseResult
from ai_reliability.errors import ResultCorruption


def execution_id(run_id: str, test_id: str, test_hash: str, repetition: int) -> str:
    """Deterministic ID of one execution: identical inputs always map to the same ID."""
    return sha256_hex(f"{run_id}|{test_id}|{test_hash}|{repetition}")[:24]


class CheckpointJournal:
    """JSONL journal of completed executions.

    Each completed execution is appended and flushed immediately, so an
    interrupted run can resume without repeating successful tests. A torn
    final line (crash mid-write) is tolerated; corruption elsewhere is not.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, CaseResult]:
        if not self.path.exists():
            return {}
        lines = self.path.read_text(encoding="utf-8").splitlines()
        results: dict[str, CaseResult] = {}
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                result = CaseResult.model_validate(json.loads(line))
            except (ValueError, ValidationError) as exc:
                if index == len(lines) - 1:
                    break  # torn trailing write from an interrupted run
                raise ResultCorruption(f"{self.path}: corrupt checkpoint line {index + 1}") from exc
            results[result.execution_id] = result
        return results

    def append(self, result: CaseResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(result.model_dump_json() + "\n")
            fh.flush()
