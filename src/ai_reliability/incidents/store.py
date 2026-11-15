"""File-backed incident store. The default location (``incidents/``) is git-ignored.

Layout::

    incidents/
    ├── raw/<incident_id>.yaml        # as reported — never commit
    ├── sanitized/<incident_id>.yaml  # redacted, still requires manual review
    └── tests/<test_id>.yaml          # DRAFT / REVIEWED / ACTIVE regression tests
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from ai_reliability.config.loader import read_yaml
from ai_reliability.domain.enums import TestStatus
from ai_reliability.domain.incident import Incident
from ai_reliability.domain.test_case import TestCase
from ai_reliability.errors import ConfigurationError, UnknownComponent
from ai_reliability.incidents.pipeline import (
    incident_to_test,
    sanitize_incident,
    transition,
)


def _dump(model: BaseModel) -> str:
    return yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


class IncidentStore:
    """Reads and writes incidents and incident-derived tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw_dir = root / "raw"
        self.sanitized_dir = root / "sanitized"
        self.tests_dir = root / "tests"

    def _load_incident(self, path: Path) -> Incident:
        try:
            return Incident.model_validate(read_yaml(path))
        except ValidationError as exc:
            raise ConfigurationError(f"{path}: {exc}") from exc

    def add_raw(self, incident: Incident) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / f"{incident.incident_id}.yaml"
        path.write_text(_dump(incident), encoding="utf-8")
        return path

    def list_incidents(self) -> list[tuple[str, str]]:
        """(incident_id, stage) for every known incident; the most processed stage wins."""
        found: dict[str, str] = {}
        for stage, directory in (("raw", self.raw_dir), ("sanitized", self.sanitized_dir)):
            if directory.is_dir():
                for path in sorted(directory.glob("*.yaml")):
                    found[path.stem] = stage
        return sorted(found.items())

    def load(self, incident_id: str) -> Incident:
        for directory in (self.sanitized_dir, self.raw_dir):
            path = directory / f"{incident_id}.yaml"
            if path.exists():
                return self._load_incident(path)
        raise UnknownComponent(f"incident {incident_id!r} not found under {self.root}")

    def sanitize(self, incident_id: str, *, extra_literals: tuple[str, ...] = ()) -> Incident:
        sanitized_path = self.sanitized_dir / f"{incident_id}.yaml"
        if sanitized_path.exists():
            return self._load_incident(sanitized_path)
        # A raw file is never trusted, even if it claims to be sanitized already.
        incident = self.load(incident_id)
        incident = incident.model_copy(
            update={"sanitization": incident.sanitization.model_copy(update={"sanitized": False})}
        )
        incident = sanitize_incident(incident, extra_literals=extra_literals)
        self.sanitized_dir.mkdir(parents=True, exist_ok=True)
        sanitized_path.write_text(_dump(incident), encoding="utf-8")
        return incident

    def convert(self, incident_id: str) -> tuple[TestCase, Path]:
        """Sanitize (if needed) and write a DRAFT candidate regression test. Never auto-commits."""
        test = incident_to_test(self.sanitize(incident_id))
        self.tests_dir.mkdir(parents=True, exist_ok=True)
        path = self.tests_dir / f"{test.test_id}.yaml"
        if path.exists():
            existing = self.load_test(test.test_id)
            if existing.metadata.status is not TestStatus.DRAFT:
                raise ConfigurationError(
                    f"{test.test_id} is already {existing.metadata.status.value}; refusing to overwrite"
                )
        path.write_text(_dump(test), encoding="utf-8")
        return test, path

    def load_test(self, test_id: str) -> TestCase:
        path = self.tests_dir / f"{test_id}.yaml"
        if not path.exists():
            raise UnknownComponent(f"incident test {test_id!r} not found")
        return TestCase.model_validate(read_yaml(path))

    def set_status(
        self, test_id: str, status: TestStatus, *, reviewer: str | None = None
    ) -> TestCase:
        test = transition(self.load_test(test_id), status, reviewer=reviewer)
        (self.tests_dir / f"{test_id}.yaml").write_text(_dump(test), encoding="utf-8")
        return test

    def list_tests(self) -> list[TestCase]:
        if not self.tests_dir.is_dir():
            return []
        return [
            TestCase.model_validate(read_yaml(p)) for p in sorted(self.tests_dir.glob("*.yaml"))
        ]
