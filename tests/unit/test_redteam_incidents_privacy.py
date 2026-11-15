from __future__ import annotations

from pathlib import Path

import pytest

from ai_reliability.domain.enums import (
    FailureCategory,
    Outcome,
    Severity,
    TestSource,
    TestStatus,
    TracePrivacy,
)
from ai_reliability.domain.incident import Incident, ObservedBehavior
from ai_reliability.domain.result import CaseResult
from ai_reliability.domain.test_case import ExpectedBehavior, SafetyExpectations, TestInput
from ai_reliability.errors import (
    IncidentSanitizationFailure,
    InvalidStatusTransition,
    InvalidTestCase,
    UnknownComponent,
)
from ai_reliability.incidents.pipeline import (
    classify_incident,
    incident_to_test,
    infer_evaluators,
    sanitize_incident,
    transition,
)
from ai_reliability.incidents.store import IncidentStore
from ai_reliability.privacy.redaction import Redactor, apply_privacy
from ai_reliability.redteam.catalog import FAMILIES, build_redteam_suite, family_of
from ai_reliability.redteam.common import DEFAULT_CANARY, make_canary
from ai_reliability.redteam.engine import summarize_redteam
from ai_reliability.redteam.mutations.metamorphic import (
    build_metamorphic_tests,
    check_relations,
    mutate_test,
)
from ai_reliability.redteam.mutations.operators import OPERATORS
from tests.conftest import make_case

# Fake credentials are assembled at runtime so no literal token ever appears in the repository.
FAKE_OPENAI = "sk-" + "Ab1" * 10
FAKE_AWS = "AKIA" + "Z" * 16
FAKE_GITHUB = "ghp_" + "a1B2" * 9
FAKE_PEM = "-----BEGIN RSA " + "PRIVATE KEY-----\nabc\n-----END RSA " + "PRIVATE KEY-----"
FAKE_JWT = "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 16


# -- red team ------------------------------------------------------------------------------
def test_redteam_suite_is_deterministic_and_covers_families() -> None:
    a, b = build_redteam_suite(seed=0), build_redteam_suite(seed=0)
    assert a == b and len(a) >= 30
    assert {family_of(t) for t in a} == set(FAMILIES)
    assert all(t.metadata.source is TestSource.REDTEAM for t in a)


def test_canary_lives_only_in_protected_context() -> None:
    suite = build_redteam_suite(("direct_injection", "jailbreak"), seed=0)
    for test in suite:
        assert test.input.system_prompt and DEFAULT_CANARY in test.input.system_prompt
        assert DEFAULT_CANARY not in test.input.prompt
        assert (
            test.expected and test.expected.safety and test.expected.safety.canary == DEFAULT_CANARY
        )
    assert make_canary(5) != DEFAULT_CANARY and make_canary(5) == make_canary(5)


def test_markers_unique_per_test() -> None:
    suite = build_redteam_suite(("direct_injection", "indirect_injection"), seed=1)
    markers = [m for t in suite for m in t.expected.safety.injection_markers]  # type: ignore[union-attr]
    assert len(markers) == len(set(markers))


def test_unknown_family_rejected() -> None:
    with pytest.raises(UnknownComponent):
        build_redteam_suite(("not_a_family",))


def test_summarize_redteam_counts_errors_separately() -> None:
    suite = build_redteam_suite(("jailbreak",))
    results = [
        CaseResult(
            execution_id=str(i),
            run_id="r",
            test_id=t.test_id,
            test_hash="h",
            category=t.category,
            severity=t.severity,
            outcome=[Outcome.PASS, Outcome.FAIL, Outcome.ERROR][i % 3],
        )
        for i, t in enumerate(suite)
    ]
    report = summarize_redteam("r", "sys", suite, results)
    family = report.families["jailbreak"]
    assert family.errors == 2 and family.succeeded == 2 and family.resisted == 2
    assert report.attack_success_rate == pytest.approx(0.5)


# -- mutations / metamorphic -----------------------------------------------------------------
BASE = make_case(
    ExpectedBehavior(number=391),
    ("numeric",),
    test_id="core.math",
    prompt="What is 17 * 23? Answer with the number only. Use 'quotes'.",
)


@pytest.mark.parametrize("operator", sorted(OPERATORS))
def test_mutations_are_deterministic_and_preserve_expectations(operator: str) -> None:
    first, second = mutate_test(BASE, operator, seed=3), mutate_test(BASE, operator, seed=3)
    assert first == second
    assert first.expected == BASE.expected and first.evaluators == BASE.evaluators
    assert (
        first.metadata.parent_test_id == "core.math"
        and first.metadata.source is TestSource.MUTATION
    )
    assert first.test_id == f"core.math~{operator}"


def test_specific_mutations() -> None:
    assert OPERATORS["instruction_reordering"].apply(BASE.input, 0).prompt.startswith("Answer with")
    assert "’" in OPERATORS["unicode"].apply(BASE.input, 0).prompt
    assert len(OPERATORS["distractor_document"].apply(BASE.input, 0).documents) == 1
    reformatted = (
        OPERATORS["json_formatting"].apply(TestInput(prompt='fix {"a": 1, "b": 2}'), 0).prompt
    )
    assert '\n  "a": 1' in reformatted


def test_metamorphic_relations() -> None:
    pairs = build_metamorphic_tests([BASE], ["whitespace", "paraphrase"])
    assert len(pairs) == 2

    def row(test_id: str, outcome: Outcome) -> CaseResult:
        return CaseResult(
            execution_id=test_id,
            run_id="r",
            test_id=test_id,
            test_hash="h",
            category="c",
            severity=Severity.LOW,
            outcome=outcome,
        )

    report = check_relations(
        pairs,
        [
            row("core.math", Outcome.PASS),
            row("core.math~whitespace", Outcome.PASS),
            row("core.math~paraphrase", Outcome.FAIL),
        ],
    )
    assert report.pairs == 2 and len(report.violations) == 1
    assert report.violation_rate_by_operator == {"paraphrase": 1.0, "whitespace": 0.0}


# -- privacy ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"key {FAKE_OPENAI}", "openai_key"),
        (f"aws {FAKE_AWS}", "aws_access_key"),
        (f"gh {FAKE_GITHUB}", "github_token"),
        (f"token {FAKE_JWT}", "jwt"),
        ("Authorization: Bearer " + "x" * 24, "authorization_header"),
        ('{"pass' + 'word": "' + "hunter2" * 2 + '"}', "credential_field"),
        ("mail me at jane.doe@example.com", "email"),
        ("card 4111 1111 1111 1111", "credit_card"),
        ("call +1 415 555 0100", "phone"),
        ("host 203.0.113.9", "ipv4"),
        ("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "private_key"),
    ],
)
def test_redaction_kinds(text: str, kind: str) -> None:
    result = Redactor().redact(text)
    assert kind in result.counts and f"[REDACTED:{kind}]" in result.text


def test_redaction_avoids_false_positives_and_is_idempotent() -> None:
    redactor = Redactor()
    assert (
        redactor.redact("order 1234 5678 9012 3456 shipped; max_tokens: 1024").counts == {}
    )  # Luhn fails
    once = redactor.redact(f"Api_key: {FAKE_OPENAI} email a@b.co").text
    assert redactor.redact(once).text == once and redactor.findings(once) == {}


def test_redaction_literals_and_preserved_canaries() -> None:
    redactor = Redactor(extra_literals=["Contoso Ltd"], preserve_literals=[DEFAULT_CANARY])
    out = redactor.redact(f"Customer Contoso Ltd; secret = {DEFAULT_CANARY}").text
    assert "Contoso" not in out and DEFAULT_CANARY in out


@pytest.mark.parametrize(
    ("policy", "check"),
    [
        (TracePrivacy.FULL_TEXT, lambda v: v == "mail a@b.co"),
        (TracePrivacy.REDACTED_TEXT, lambda v: v == "mail [REDACTED:email]"),
        (TracePrivacy.HASH_ONLY, lambda v: v.startswith("sha256:") and "a@b.co" not in v),
        (TracePrivacy.METADATA_ONLY, lambda v: v is None),
    ],
)
def test_apply_privacy(policy: TracePrivacy, check) -> None:  # type: ignore[no-untyped-def]
    assert check(apply_privacy("mail a@b.co", policy))


# -- incidents -------------------------------------------------------------------------------------
def incident(**updates: object) -> Incident:
    base = Incident(
        incident_id="INC-1",
        system_id="bot",
        category="prompt_injection",
        severity=Severity.CRITICAL,
        description="Leaked config to jane@example.com",
        trace_id="tr-1",
        input=TestInput(
            prompt=f"Summarize; my key is {FAKE_OPENAI}", system_prompt=f"secret = {DEFAULT_CANARY}"
        ),
        observed=ObservedBehavior(response_text=f"secret = {DEFAULT_CANARY}"),
        expected=ExpectedBehavior(safety=SafetyExpectations(canary=DEFAULT_CANARY)),
        suspected_root_cause="unknown",
    )
    return base.model_copy(update=updates)


def test_sanitize_redacts_and_records_counts_only() -> None:
    clean = sanitize_incident(incident())
    record = clean.sanitization
    assert record.sanitized and record.manual_review_required
    assert record.redactions == {"email": 1, "openai_key": 1}
    assert (
        FAKE_OPENAI not in clean.model_dump_json()
        and "jane@example.com" not in clean.model_dump_json()
    )
    assert DEFAULT_CANARY in (clean.input.system_prompt or "")  # synthetic canary preserved


def test_sanitize_fails_on_residual_patterns() -> None:
    class NoOp(Redactor):
        def redact(self, text: str):  # type: ignore[no-untyped-def]
            from ai_reliability.privacy.redaction import RedactionResult

            return RedactionResult(text=text)

        def findings(self, text: str) -> dict[str, int]:
            return Redactor().findings(text)

    with pytest.raises(IncidentSanitizationFailure):
        sanitize_incident(incident(), redactor=NoOp())


@pytest.mark.parametrize(
    ("category", "error", "failure"),
    [
        ("prompt_injection", None, FailureCategory.SAFETY_FAILURE),
        ("rag", None, FailureCategory.HALLUCINATION),
        ("structured_output", None, FailureCategory.FORMAT_FAILURE),
        ("correctness", None, FailureCategory.WRONG_ANSWER),
        ("correctness", "upstream timed out", FailureCategory.TIMEOUT),
        ("tool_use", None, FailureCategory.TOOL_FAILURE),
    ],
)
def test_classification_separates_observation_from_cause(
    category: str, error: str | None, failure: FailureCategory
) -> None:
    case = classify_incident(incident(category=category, observed=ObservedBehavior(error=error)))
    assert case.observed_failure is failure and case.confidence == "unverified"
    assert case.suspected_root_cause == "unknown"


def test_conversion_requires_sanitization_and_expectation() -> None:
    with pytest.raises(IncidentSanitizationFailure):
        incident_to_test(incident())
    with pytest.raises(InvalidTestCase):
        incident_to_test(sanitize_incident(incident(expected=None)))
    test = incident_to_test(sanitize_incident(incident()))
    assert test.metadata.status is TestStatus.DRAFT and test.metadata.source is TestSource.INCIDENT
    assert test.metadata.incident_id == "INC-1" and test.metadata.trace_id == "tr-1"
    assert test.evaluators == ("canary_leak",) and "incident" in test.tags


def test_infer_evaluators() -> None:
    exp = ExpectedBehavior(
        exact="a", json_schema={"type": "object"}, safety=SafetyExpectations(must_refuse=True)
    )
    assert infer_evaluators(exp) == ("exact_match", "json_valid", "json_schema", "policy")


def test_status_transitions() -> None:
    draft = incident_to_test(sanitize_incident(incident()))
    with pytest.raises(InvalidStatusTransition):
        transition(draft, TestStatus.ACTIVE)  # cannot skip review
    with pytest.raises(InvalidStatusTransition):
        transition(draft, TestStatus.REVIEWED)  # reviewer required
    reviewed = transition(draft, TestStatus.REVIEWED, reviewer="rev-1")
    active = transition(reviewed, TestStatus.ACTIVE)
    assert active.metadata.status is TestStatus.ACTIVE and active.metadata.reviewer == "rev-1"
    retired = transition(active, TestStatus.RETIRED)
    with pytest.raises(InvalidStatusTransition):
        transition(retired, TestStatus.ACTIVE)


def test_incident_store_roundtrip(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    store.add_raw(incident())
    assert store.list_incidents() == [("INC-1", "raw")]
    test, path = store.convert("INC-1")
    assert path.exists() and store.list_incidents() == [("INC-1", "sanitized")]
    assert FAKE_OPENAI not in path.read_text(encoding="utf-8")
    store.set_status(test.test_id, TestStatus.REVIEWED, reviewer="r")
    store.set_status(test.test_id, TestStatus.ACTIVE)
    assert store.load_test(test.test_id).metadata.status is TestStatus.ACTIVE
    with pytest.raises(Exception, match="refusing to overwrite"):
        store.convert("INC-1")
