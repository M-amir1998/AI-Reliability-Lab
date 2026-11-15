"""Regression tests for issues found during documentation review."""

from __future__ import annotations

from pathlib import Path

from ai_reliability.domain.enums import RunStatus, TestStatus, TracePrivacy
from ai_reliability.domain.incident import Incident, ObservedBehavior, SanitizationRecord
from ai_reliability.domain.test_case import (
    ExpectedBehavior,
    SafetyExpectations,
    TestCase,
    TestInput,
)
from ai_reliability.incidents.store import IncidentStore
from ai_reliability.privacy.redaction import Redactor
from ai_reliability.redteam.mutations.metamorphic import mutate_test
from ai_reliability.storage.registry_db import RunIndex, RunRecord
from ai_reliability.testsuites.registry import TestSuiteRegistry
from ai_reliability.traces.model import _RawTrace, to_record
from tests.conftest import make_case


def test_redeclaring_an_older_baseline_makes_it_current_again(tmp_path: Path) -> None:
    index = RunIndex(tmp_path / "r.sqlite")
    for run_id in ("a", "b"):
        index.upsert_run(
            RunRecord(
                run_id=run_id,
                created_at="t",
                completed_at="t",
                status=RunStatus.COMPLETED,
                system_id="s",
                suite_id="x",
                suite_version="1",
                config_hash="c",
                git_sha=None,
                pass_rate=None,
                decision=None,
            )
        )
    for run_id in ("a", "b", "a"):
        index.declare_baseline("main", run_id)
    assert index.current_baseline("main") == "a"
    assert [b.run_id for b in index.baseline_history("main")] == ["a", "b", "a"]


def test_raw_incident_claiming_to_be_sanitized_is_still_redacted(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    email = "victim" + "@example.com"
    store.add_raw(
        Incident(
            incident_id="INC-X",
            system_id="s",
            category="correctness",
            description=f"reported by {email}",
            input=TestInput(prompt="q"),
            observed=ObservedBehavior(response_text="a"),
            expected=ExpectedBehavior(exact="b"),
            suspected_root_cause=f"maybe {email}'s config",
            sanitization=SanitizationRecord(sanitized=True),
        )
    )
    clean = store.sanitize("INC-X")
    assert email not in clean.model_dump_json()
    assert clean.sanitization.redactions.get("email") == 2


def test_mutation_test_ids_survive_validation_roundtrip() -> None:
    mutated = mutate_test(make_case(ExpectedBehavior(exact="x"), test_id="core.q"), "whitespace")
    assert TestCase.model_validate_json(mutated.model_dump_json()).test_id == "core.q~whitespace"


def test_release_full_includes_active_incident_tests_when_registered(tmp_path: Path) -> None:
    registry = TestSuiteRegistry()
    base = len(registry.get("release_full"))
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    import yaml

    case = make_case(
        ExpectedBehavior(safety=SafetyExpectations(canary="CANARY_X")),
        ("canary_leak",),
        test_id="incident.INC-9",
    )
    (tests_dir / "incident.INC-9.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json")), "utf-8"
    )
    registry.register_incident_tests(tests_dir)
    full = registry.get("release_full")
    assert len(full) == base + 1
    assert full.select().tests[-1].metadata.status is TestStatus.ACTIVE


def test_trace_error_text_follows_capture_policy() -> None:
    raw = _RawTrace(trace_id="t", input="x", error="db password leak at host-7")
    for policy in (TracePrivacy.METADATA_ONLY, TracePrivacy.HASH_ONLY):
        record = to_record(raw, policy, Redactor())
        assert record.error is None or record.error.startswith("sha256:")
    assert (
        to_record(raw, TracePrivacy.REDACTED_TEXT, Redactor()).error == "db password leak at host-7"
    )


def test_category_regression_gate_does_not_silently_pass_without_comparable_tests() -> None:
    from ai_reliability.domain.enums import Outcome, Severity
    from ai_reliability.domain.gate import GateOutcome, GatePolicy, GateSpec
    from ai_reliability.domain.result import CaseResult
    from ai_reliability.gates.engine import evaluate_gates
    from ai_reliability.metrics.aggregate import summarize
    from ai_reliability.regression.compare import compare_runs

    def rows(test_hash: str) -> list[CaseResult]:
        return [
            CaseResult(
                execution_id=f"e{i}",
                run_id="r",
                test_id=f"pi{i}",
                test_hash=test_hash,
                category="prompt_injection",
                severity=Severity.LOW,
                outcome=Outcome.PASS,
            )
            for i in range(3)
        ]

    base, cand = rows("old"), rows("redefined")  # every test changed: nothing comparable
    comparison = compare_runs("b", base, "c", cand)
    spec = GateSpec(
        gate_id="pi",
        kind="category",
        category="prompt_injection",
        max_regressions=0,
        on_missing="fail",
    )
    decision = evaluate_gates(GatePolicy(gates=(spec,)), summarize("c", cand), comparison)
    assert decision.gates[0].outcome is GateOutcome.FAIL
    assert "no comparable tests" in decision.gates[0].reason
