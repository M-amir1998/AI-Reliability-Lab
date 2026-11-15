"""Security evaluation tests: red-team E2E, secret hygiene of stored artifacts, unsafe-code bans."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_reliability.clock import FakeClock
from ai_reliability.config.models import RunConfig, RunnerSettings
from ai_reliability.domain.enums import CapturePolicy, Outcome
from ai_reliability.evaluators.registry import default_evaluator_registry
from ai_reliability.redteam.catalog import FAMILIES, PROMPT_INJECTION_FAMILIES, build_redteam_suite
from ai_reliability.redteam.common import DEFAULT_CANARY
from ai_reliability.redteam.engine import RedTeamEngine
from ai_reliability.runner.executor import EvaluationRunner
from ai_reliability.services.evaluation import run_evaluation
from ai_reliability.services.workspace import Workspace
from ai_reliability.systems.mock import SYNTHETIC_SYSTEMS, KnowledgeBase, MockSystem
from tests.conftest import REPO_ROOT, run

pytestmark = pytest.mark.security


def _attack(system_name: str, families: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    suite = build_redteam_suite(families)
    clock = FakeClock()
    system = MockSystem(
        system_name,
        SYNTHETIC_SYSTEMS[system_name].profile,
        KnowledgeBase.from_tests(suite),
        clock=clock,
    )
    runner = EvaluationRunner(
        system, default_evaluator_registry(), settings=RunnerSettings(), clock=clock
    )
    return run(RedTeamEngine(runner, system_name).run(f"rt-{system_name}", suite))


def test_safe_system_resists_every_attack_family() -> None:
    outcome, report = _attack("good")
    assert report.attack_success_rate == 0.0 and report.leaked_test_ids == ()
    assert set(report.families) == set(FAMILIES)
    assert all(r.outcome is Outcome.PASS for r in outcome.results)


def test_unsafe_system_is_caught_by_prompt_injection_suite() -> None:
    _, report = _attack("unsafe", PROMPT_INJECTION_FAMILIES)
    assert (report.attack_success_rate or 0) > 0.8
    for family in PROMPT_INJECTION_FAMILIES:
        assert report.families[family].succeeded > 0, family
    assert report.leaked_test_ids, "canary leaks must be detected"


def test_unsafe_system_fails_jailbreak_hierarchy_and_tool_abuse() -> None:
    _, report = _attack(
        "unsafe", ("jailbreak", "instruction_hierarchy", "sensitive_extraction", "tool_abuse")
    )
    assert report.families["instruction_hierarchy"].canary_leaks > 0
    assert report.families["tool_abuse"].succeeded > 0
    assert (report.families["jailbreak"].attack_success_rate or 0) > 0.5


def test_stored_artifacts_never_contain_prompt_text_or_canary_by_default(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    art = run(
        run_evaluation(
            workspace,
            RunConfig(
                system="synthetic/good",
                suite="prompt_injection",
                capture=CapturePolicy.FAILED_CASES,
            ),
            clock=FakeClock(),
        )
    )
    assert art.summary is not None and art.summary.metric("pass_rate") == 1.0
    for path in art.run_dir.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            assert DEFAULT_CANARY.encode() not in blob, path.name
            assert b"Ignore all previous instructions" not in blob, path.name


def test_failed_case_text_is_redacted_before_storage(tmp_path: Path) -> None:
    from ai_reliability.config.models import SystemConfig
    from ai_reliability.domain.content import RetrievedDocument
    from ai_reliability.domain.test_case import ExpectedBehavior, TestCase, TestInput
    from ai_reliability.systems.mock.profile import MockProfile

    fake_key = "sk-" + "Zz9" * 10
    workspace = Workspace(tmp_path)
    suite_file = tmp_path / "s.yaml"
    test = TestCase(
        test_id="leaky",
        category="privacy",
        input=TestInput(prompt="p", documents=(RetrievedDocument(doc_id="d", text="x"),)),
        expected=ExpectedBehavior(exact="nope", reference_text=f"key={fake_key}"),
        evaluators=("exact_match",),
    )
    import yaml

    suite_file.write_text(
        yaml.safe_dump({"suite_id": "leaky", "tests": [test.model_dump(mode="json")]}), "utf-8"
    )
    workspace.systems.register(SystemConfig(system_id="echo", type="mock", mock=MockProfile()))
    art = run(
        run_evaluation(
            workspace, RunConfig(system="echo", suite=str(suite_file)), clock=FakeClock()
        )
    )
    rows = workspace.store().load_results(art.manifest.run_id)
    assert rows[0].outcome is Outcome.FAIL and rows[0].response_text is not None
    assert fake_key not in rows[0].response_text and "[REDACTED" in rows[0].response_text


def test_source_never_executes_or_unpickles_model_output() -> None:
    banned_calls = {"eval", "exec", "compile", "__import__"}
    offenders = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] + (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
                )
                if any(n.split(".")[0] in {"pickle", "marshal", "shelve", "dill"} for n in names):
                    offenders.append(f"{path.name}: imports {names}")
            # observability uses __import__ only to load optional, fixed module names
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in banned_calls
                and not (node.func.id == "__import__" and path.name == "metrics.py")
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.func.id}()")
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") in {"load", "unsafe_load"}
                and getattr(getattr(node.func, "value", None), "id", "") == "yaml"
            ):
                offenders.append(f"{path.name}:{node.lineno} yaml.load")
    assert offenders == []


def test_example_configs_contain_no_credentials() -> None:
    from ai_reliability.privacy.redaction import Redactor

    redactor = Redactor()
    for path in list((REPO_ROOT / "configs").rglob("*.yaml")) + list(
        (REPO_ROOT / "examples").rglob("*.*")
    ):
        findings = redactor.findings(path.read_text(encoding="utf-8"))
        findings.pop("email", None)  # synthetic example addresses on reserved domains are fine
        findings.pop("phone", None)
        findings.pop("ipv4", None)  # loopback endpoints of local example servers
        findings.pop("ipv4", None)  # loopback endpoints of local example servers
        assert findings == {}, f"{path}: {findings}"
