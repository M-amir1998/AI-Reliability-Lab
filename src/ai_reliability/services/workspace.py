"""Workspace: resolves configuration directories, registries and storage for a project root."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ai_reliability.evaluators.registry import EvaluatorRegistry, default_evaluator_registry
from ai_reliability.incidents.store import IncidentStore
from ai_reliability.storage.registry_db import RunIndex
from ai_reliability.storage.run_store import RunStore
from ai_reliability.systems.prompts import PromptRegistry
from ai_reliability.systems.registry import SystemRegistry
from ai_reliability.testsuites.registry import TestSuiteRegistry


def git_sha(root: Path) -> str | None:
    """Current commit SHA if ``root`` is inside a git checkout (read-only call)."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [git, "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) == 40 else None


@dataclass
class Workspace:
    """Everything rooted at a project directory. Construct once per CLI invocation."""

    root: Path
    results_dir: Path = field(init=False)
    prompts: PromptRegistry = field(init=False)
    systems: SystemRegistry = field(init=False)
    suites: TestSuiteRegistry = field(init=False)
    incidents: IncidentStore = field(init=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        configs = self.root / "configs"
        self.results_dir = self.root / "results"
        self.prompts = PromptRegistry.from_directory(configs / "prompts")
        self.systems = SystemRegistry.from_directory(configs / "systems", self.prompts)
        self.suites = TestSuiteRegistry()
        self.suites.register_directory(configs / "suites")
        self.incidents = IncidentStore(self.root / "incidents")
        self.suites.register_incident_tests(self.incidents.tests_dir)

    def store(self, results_dir: Path | None = None) -> RunStore:
        return RunStore(results_dir or self.results_dir)

    def index(self, results_dir: Path | None = None) -> RunIndex:
        return RunIndex((results_dir or self.results_dir) / "registry.sqlite")

    def evaluators(self) -> EvaluatorRegistry:
        return default_evaluator_registry()

    def resolve_path(self, ref: str) -> Path:
        path = Path(ref)
        return path if path.is_absolute() else self.root / path

    @property
    def git_sha(self) -> str | None:
        return git_sha(self.root)
