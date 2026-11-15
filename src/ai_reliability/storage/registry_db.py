"""SQLite index of runs and baseline declarations (metadata only, append-only baselines)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import RunStatus
from ai_reliability.errors import ImmutableRunError, UnknownComponent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    system_id TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    git_sha TEXT,
    pass_rate REAL,
    decision TEXT
);
CREATE TABLE IF NOT EXISTS baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    declared_at TEXT NOT NULL,
    note TEXT,
    UNIQUE(name, run_id)
);
"""


class RunRecord(DomainModel):
    run_id: str
    created_at: str
    completed_at: str | None
    status: RunStatus
    system_id: str
    suite_id: str
    suite_version: str
    config_hash: str
    git_sha: str | None
    pass_rate: float | None
    decision: str | None


class BaselineRecord(DomainModel):
    name: str
    run_id: str
    declared_at: str
    note: str | None


class RunIndex:
    """Metadata index. Results themselves live in Parquet files, not in SQLite."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_run(self, record: RunRecord) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (record.run_id,)
            ).fetchone()
            if existing is not None and existing["status"] == RunStatus.COMPLETED.value:
                raise ImmutableRunError(f"run {record.run_id} is completed and immutable")
            conn.execute(
                """INSERT OR REPLACE INTO runs VALUES
                   (:run_id, :created_at, :completed_at, :status, :system_id, :suite_id,
                    :suite_version, :config_hash, :git_sha, :pass_rate, :decision)""",
                record.model_dump(mode="json"),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else RunRecord.model_validate(dict(row))

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [RunRecord.model_validate(dict(r)) for r in rows]

    def declare_baseline(self, name: str, run_id: str, note: str | None = None) -> BaselineRecord:
        """Append a baseline declaration. History is never overwritten."""
        run = self.get_run(run_id)
        if run is None:
            raise UnknownComponent(f"run {run_id} not found")
        if run.status is not RunStatus.COMPLETED:
            raise ImmutableRunError(
                f"run {run_id} is {run.status}; only completed runs can be baselines"
            )
        record = BaselineRecord(
            name=name, run_id=run_id, declared_at=datetime.now(UTC).isoformat(), note=note
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO baselines (name, run_id, declared_at, note) VALUES (?, ?, ?, ?)",
                (record.name, record.run_id, record.declared_at, record.note),
            )
        return record

    def current_baseline(self, name: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id FROM baselines WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)
            ).fetchone()
        return None if row is None else str(row["run_id"])

    def baseline_history(self, name: str) -> list[BaselineRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, run_id, declared_at, note FROM baselines WHERE name = ? ORDER BY id",
                (name,),
            ).fetchall()
        return [BaselineRecord.model_validate(dict(r)) for r in rows]
