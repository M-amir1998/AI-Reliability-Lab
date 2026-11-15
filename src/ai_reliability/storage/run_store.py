"""Filesystem layout for immutable evaluation runs.

::

    results/<run-id>/
    ├── manifest.json        # what/which/versions/seed; status COMPLETED written last
    ├── config.yaml          # exact run configuration
    ├── checkpoint.jsonl     # resume journal (removed after finalization)
    ├── results.parquet      # one row per execution (no prompt text; response text per policy)
    ├── failures.parquet     # non-passing executions only
    ├── summary.json         # metrics, category summaries, failure taxonomy counts
    ├── regressions.parquet  # per-test comparison vs baseline (if a baseline was given)
    ├── comparison.json      # category/metric deltas and statistics (if baseline)
    ├── gates.json           # release decision (if a gate policy was given)
    ├── checksums.json       # sha256 of every artifact for integrity checks
    └── plots/               # SVG plots
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ai_reliability.domain.enums import RunStatus
from ai_reliability.domain.gate import ReleaseDecision
from ai_reliability.domain.regression import ComparisonReport
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.run import RunManifest, RunSummary
from ai_reliability.errors import ImmutableRunError, ResultCorruption, UnknownComponent
from ai_reliability.runner.checkpoint import CheckpointJournal
from ai_reliability.storage.parquet import read_results, write_comparisons, write_results

MANIFEST = "manifest.json"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
CHECKSUMS = "checksums.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, model: BaseModel) -> None:
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


class RunStore:
    """Creates, finalizes and reads run directories. Completed runs are immutable."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def run_dir(self, run_id: str) -> Path:
        # Strict allow-list: blocks path traversal and Windows drive-relative ids like "C:x".
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError(f"invalid run id {run_id!r}; allowed: {_RUN_ID.pattern}")
        return self.root / run_id

    def exists(self, run_id: str) -> bool:
        return (self.run_dir(run_id) / MANIFEST).exists()

    def create(self, manifest: RunManifest, config_yaml: str) -> Path:
        directory = self.run_dir(manifest.run_id)
        if (directory / MANIFEST).exists():
            existing = self.load_manifest(manifest.run_id)
            if existing.status is RunStatus.COMPLETED:
                raise ImmutableRunError(f"run {manifest.run_id} already completed")
            return directory  # resuming an interrupted run
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / MANIFEST, manifest)
        (directory / "config.yaml").write_text(config_yaml, encoding="utf-8")
        return directory

    def journal(self, run_id: str) -> CheckpointJournal:
        return CheckpointJournal(self.run_dir(run_id) / "checkpoint.jsonl")

    def mark_interrupted(self, manifest: RunManifest) -> None:
        _write_json(
            self.run_dir(manifest.run_id) / MANIFEST,
            manifest.model_copy(update={"status": RunStatus.INTERRUPTED}),
        )

    def finalize(
        self,
        manifest: RunManifest,
        results: Sequence[CaseResult],
        summary: RunSummary,
        *,
        comparison: ComparisonReport | None = None,
        decision: ReleaseDecision | None = None,
        plots: dict[str, str] | None = None,
        documents: dict[str, str] | None = None,
    ) -> Path:
        """Write all artifacts, then the COMPLETED manifest and checksums (in that order)."""
        directory = self.run_dir(manifest.run_id)
        if (
            self.exists(manifest.run_id)
            and self.load_manifest(manifest.run_id).status is RunStatus.COMPLETED
        ):
            raise ImmutableRunError(f"run {manifest.run_id} already completed")
        write_results(directory / "results.parquet", results)
        write_results(directory / "failures.parquet", [r for r in results if not r.passed])
        _write_json(directory / "summary.json", summary)
        if comparison is not None:
            write_comparisons(directory / "regressions.parquet", comparison.cases)
            _write_json(directory / "comparison.json", comparison)
        if decision is not None:
            _write_json(directory / "gates.json", decision)
        for name, text in (documents or {}).items():
            if "/" in name or "\\" in name:
                raise ValueError(f"document name must be a plain file name: {name!r}")
            (directory / name).write_text(text, encoding="utf-8")
        if plots:
            plot_dir = directory / "plots"
            plot_dir.mkdir(exist_ok=True)
            for name, svg in plots.items():
                (plot_dir / name).write_text(svg, encoding="utf-8")
        _write_json(
            directory / MANIFEST, manifest.model_copy(update={"status": RunStatus.COMPLETED})
        )
        checksums = {
            str(p.relative_to(directory)).replace("\\", "/"): _sha256_file(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p.name not in {CHECKSUMS, "checkpoint.jsonl"}
        }
        (directory / CHECKSUMS).write_text(json.dumps(checksums, indent=2), encoding="utf-8")
        journal = directory / "checkpoint.jsonl"
        if journal.exists():
            journal.unlink()
        return directory

    # -- reading -------------------------------------------------------------
    def _require(self, run_id: str) -> Path:
        directory = self.run_dir(run_id)
        if not (directory / MANIFEST).exists():
            raise UnknownComponent(f"run {run_id!r} not found under {self.root}")
        return directory

    def load_manifest(self, run_id: str) -> RunManifest:
        path = self._require(run_id) / MANIFEST
        try:
            return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise ResultCorruption(f"{path}: {exc}") from exc

    def load_results(self, run_id: str) -> list[CaseResult]:
        return read_results(self._require(run_id) / "results.parquet")

    def load_summary(self, run_id: str) -> RunSummary:
        path = self._require(run_id) / "summary.json"
        if not path.exists():
            raise ResultCorruption(f"run {run_id} has no summary (not completed?)")
        return RunSummary.model_validate_json(path.read_text(encoding="utf-8"))

    def load_decision(self, run_id: str) -> ReleaseDecision | None:
        path = self._require(run_id) / "gates.json"
        return (
            ReleaseDecision.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def load_comparison(self, run_id: str) -> ComparisonReport | None:
        path = self._require(run_id) / "comparison.json"
        return (
            ComparisonReport.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def verify(self, run_id: str) -> None:
        """Raise :class:`ResultCorruption` if any artifact is missing or modified."""
        directory = self._require(run_id)
        path = directory / CHECKSUMS
        if not path.exists():
            raise ResultCorruption(f"run {run_id} has no checksums (not completed?)")
        expected: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        for rel, digest in expected.items():
            artifact = directory / rel
            if not artifact.exists():
                raise ResultCorruption(f"run {run_id}: missing artifact {rel}")
            if _sha256_file(artifact) != digest:
                raise ResultCorruption(f"run {run_id}: artifact {rel} was modified")

    def list_run_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.parent.name for p in self.root.glob(f"*/{MANIFEST}"))
