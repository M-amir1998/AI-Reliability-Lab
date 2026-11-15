"""Test-suite registry: built-in, generated, composed, YAML-defined and incident-derived suites."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from ai_reliability.config.loader import read_yaml
from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.test_case import TestCase, TestSuite
from ai_reliability.errors import ConfigurationError, UnknownComponent
from ai_reliability.redteam.catalog import FAMILIES, PROMPT_INJECTION_FAMILIES, build_redteam_suite
from ai_reliability.redteam.mutations.metamorphic import mutate_test
from ai_reliability.redteam.mutations.operators import OPERATORS
from ai_reliability.testsuites.builtin import builtin_suites

SuiteFactory = Callable[[], TestSuite]

ROBUSTNESS_OPERATORS = (
    "whitespace",
    "capitalization",
    "question_formatting",
    "irrelevant_context",
    "instruction_reordering",
    "unicode",
    "paraphrase",
)


class SuiteFile(DomainModel):
    """YAML suite definition. ``include`` pulls in other registered suites."""

    suite_id: str
    version: str = "1"
    description: str = ""
    tier: Literal["smoke", "core", "release", "redteam", "external"] = "core"
    include: tuple[str, ...] = ()
    include_tags: tuple[str, ...] = ()
    tests: tuple[TestCase, ...] = Field(default=())


class TestSuiteRegistry:
    """Resolves suite IDs to suites. Factories are lazy; no global mutable state."""

    __test__ = False

    def __init__(self) -> None:
        self._factories: dict[str, SuiteFactory] = {}
        self._descriptions: dict[str, str] = {}
        builtin = builtin_suites()
        for suite_id, suite in builtin.items():
            self.register(suite_id, lambda s=suite: s, suite.description)  # type: ignore[misc]
        self.register(
            "prompt_injection",
            lambda: build_redteam_suite(PROMPT_INJECTION_FAMILIES, suite_id="prompt_injection"),
            "Direct, indirect, tool-output and nested prompt injection with canary secrets.",
        )
        self.register(
            "redteam_full",
            lambda: build_redteam_suite(tuple(FAMILIES), suite_id="redteam_full"),
            "All red-team families (injection, hierarchy, jailbreak, extraction, tool/resource abuse).",
        )
        self.register(
            "smoke",
            lambda: self._compose(
                "smoke",
                ("core_quality", "structured_output", "agent_tool_safety"),
                tier="smoke",
                tags=("smoke",),
            ),
            "Fastest sanity subset (tests tagged 'smoke').",
        )
        self.register(
            "ci_core",
            lambda: self._compose(
                "ci_core",
                (
                    "core_quality",
                    "structured_output",
                    "rag_grounding",
                    "agent_tool_safety",
                    "prompt_injection",
                ),
            ),
            "Small, fast offline CI suite (quality + structured output + RAG + agent + injection).",
        )
        self.register(
            "release_regression",
            lambda: self._compose(
                "release_regression",
                (
                    "core_quality",
                    "structured_output",
                    "rag_grounding",
                    "agent_tool_safety",
                    "redteam_full",
                ),
                tier="release",
            ),
            "Release gate suite: all quality suites plus the full red-team catalogue.",
        )
        self.register(
            "robustness",
            lambda: self._robustness("core_quality"),
            "core_quality plus deterministic answer-preserving mutations (metamorphic follow-ups).",
        )
        self.register(
            "release_full",
            lambda: self._compose(
                "release_full",
                ("release_regression", "robustness", "incident_regressions"),
                tier="release",
            ),
            "Everything: release regression + robustness + ACTIVE incident tests (manual/real runs).",
        )

    def register(self, suite_id: str, factory: SuiteFactory, description: str = "") -> None:
        if suite_id in self._factories:
            raise ConfigurationError(f"duplicate suite id {suite_id!r}")
        self._factories[suite_id] = factory
        self._descriptions[suite_id] = description

    def _compose(
        self,
        suite_id: str,
        parts: tuple[str, ...],
        *,
        tier: str = "core",
        tags: tuple[str, ...] = (),
    ) -> TestSuite:
        tests: dict[str, TestCase] = {}
        versions = []
        for part in parts:
            if part == "incident_regressions" and part not in self._factories:
                continue  # optional: only present when a workspace registers incident tests
            suite = self.get(part)
            versions.append(f"{part}@{suite.version}")
            for test in suite:
                if not tags or test.tags & set(tags):
                    tests.setdefault(test.test_id, test)
        return TestSuite(
            suite_id=suite_id,
            version="+".join(versions),
            tier=tier,  # type: ignore[arg-type]
            description=self._descriptions.get(suite_id, ""),
            tests=tuple(tests.values()),
        )

    def _robustness(self, base: str) -> TestSuite:
        source = self.get(base)
        # Keep only follow-ups whose mutation actually changed the input.
        unique = [
            follow_up
            for test in source
            for op in ROBUSTNESS_OPERATORS
            if op in OPERATORS and (follow_up := mutate_test(test, op)).input != test.input
        ]
        return TestSuite(
            suite_id="robustness",
            version=f"{source.version}-mut1",
            tier="release",
            description=self._descriptions.get("robustness", ""),
            tests=source.tests + tuple(unique),
        )

    def get(self, suite_id: str) -> TestSuite:
        factory = self._factories.get(suite_id)
        if factory is None:
            raise UnknownComponent(f"unknown suite {suite_id!r}; see `aireliability suites list`")
        return factory()

    def resolve(self, ref: str) -> TestSuite:
        """Resolve a registered suite ID or a path to a YAML suite file."""
        if ref in self._factories:
            return self.get(ref)
        path = Path(ref)
        if path.suffix in {".yaml", ".yml"} and path.is_file():
            return self.load_file(path)
        raise UnknownComponent(f"unknown suite {ref!r}")

    def entries(self) -> list[tuple[str, str]]:
        return [(k, self._descriptions[k]) for k in sorted(self._factories)]

    def load_file(self, path: Path) -> TestSuite:
        try:
            spec = SuiteFile.model_validate(read_yaml(path))
        except ValidationError as exc:
            raise ConfigurationError(f"{path}: {exc}") from exc
        tests: dict[str, TestCase] = {}
        for included in spec.include:
            for test in self.get(included):
                if not spec.include_tags or test.tags & set(spec.include_tags):
                    tests.setdefault(test.test_id, test)
        for test in spec.tests:
            tests[test.test_id] = test
        return TestSuite(
            suite_id=spec.suite_id,
            version=spec.version,
            tier=spec.tier,
            description=spec.description,
            tests=tuple(tests.values()),
        )

    def register_directory(self, directory: Path) -> None:
        """Register every ``*.yaml`` suite file in ``directory`` under its ``suite_id``."""
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.yaml")):
            suite_id = SuiteFile.model_validate(read_yaml(path)).suite_id
            self.register(suite_id, lambda p=path: self.load_file(p), f"YAML suite ({path.name})")  # type: ignore[misc]

    def register_incident_tests(
        self, directory: Path, suite_id: str = "incident_regressions"
    ) -> None:
        """Register incident-derived tests (all statuses; runs filter to ACTIVE by default)."""

        def factory() -> TestSuite:
            tests = []
            if directory.is_dir():
                for path in sorted(directory.glob("*.yaml")):
                    try:
                        tests.append(TestCase.model_validate(read_yaml(path)))
                    except ValidationError as exc:
                        raise ConfigurationError(f"{path}: {exc}") from exc
            return TestSuite(
                suite_id=suite_id,
                version=str(len(tests)),
                tier="release",
                description="Regression tests derived from production incidents.",
                tests=tuple(tests),
            )

        self.register(
            suite_id, factory, "Incident-derived regression tests (ACTIVE only by default)."
        )
