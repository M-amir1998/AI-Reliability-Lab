"""Test case, expected behaviour and test suite contracts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import date
from typing import Literal

from pydantic import Field, JsonValue, field_serializer, field_validator, model_validator

from ai_reliability.domain.base import DomainModel, JsonObject, canonical_dumps, sha256_hex
from ai_reliability.domain.content import (
    Citation,
    PromptRef,
    RetrievedDocument,
    ToolCall,
    ToolOutputFixture,
    ToolSpec,
)
from ai_reliability.domain.enums import Severity, TestSource, TestStatus, ToolDecision
from ai_reliability.errors import InvalidTestCase

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:~\-]{0,159}$")


class TestInput(DomainModel):
    """Everything the system under test receives. Never contains expectations."""

    __test__ = False

    prompt: str
    system_prompt: str | None = None
    developer_prompt: str | None = None
    documents: tuple[RetrievedDocument, ...] = ()
    tool_outputs: tuple[ToolOutputFixture, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    variables: dict[str, str] = Field(default_factory=dict)
    prompt_ref: PromptRef | None = None
    response_format: Literal["text", "json"] = "text"


AssertionOp = Literal[
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "matches",
    "gte",
    "lte",
    "len_between",
    "type_is",
    "in_set",
    "exists",
    "not_exists",
]


class Assertion(DomainModel):
    """A declarative assertion against the response.

    ``path`` is ``$text`` for the raw response text, or a JSON pointer such as
    ``/items/0/name`` into the parsed JSON response. No response content is ever
    executed: assertions are evaluated purely as data comparisons.
    """

    name: str
    path: str = "$text"
    op: AssertionOp
    value: JsonValue = None


class ExpectedToolCall(DomainModel):
    """A tool call that must appear, optionally with required argument values."""

    name: str
    required_arguments: JsonObject = Field(default_factory=dict)
    forbidden_arguments: tuple[str, ...] = ()


class ToolExpectations(DomainModel):
    """Expected tool-use behaviour for agent tests."""

    allowed_tools: tuple[str, ...] | None = None
    forbidden_tools: tuple[str, ...] = ()
    required_calls: tuple[ExpectedToolCall, ...] = ()
    max_calls: int | None = Field(default=None, ge=0)
    max_repeated_calls: int | None = Field(default=None, ge=1)
    approval_required_for: tuple[str, ...] = ()
    expected_decisions: dict[str, ToolDecision] = Field(default_factory=dict)
    argument_types: dict[str, dict[str, Literal["string", "number", "integer", "boolean"]]] = Field(
        default_factory=dict
    )


class CitationExpectations(DomainModel):
    """Expected citation behaviour for RAG tests."""

    required: bool = True
    min_citations: int = Field(default=1, ge=0)
    must_cite: tuple[str, ...] = ()
    claim_support: tuple[Citation, ...] = ()


class RagExpectations(DomainModel):
    """Expected retrieval and grounding behaviour."""

    relevant_doc_ids: tuple[str, ...] = ()
    required_facts: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    min_grounding: float = Field(default=0.6, ge=0.0, le=1.0)


class SafetyExpectations(DomainModel):
    """Expected safety behaviour (refusal, canary protection, injection resistance)."""

    canary: str | None = None
    must_refuse: bool | None = None
    injection_markers: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()
    max_output_chars: int | None = Field(default=None, ge=1)


class ExpectedBehavior(DomainModel):
    """What a correct response looks like. Each evaluator reads only its own fields."""

    exact: str | None = None
    case_sensitive: bool = False
    normalize_whitespace: bool = True
    contains_all: tuple[str, ...] = ()
    contains_any: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    regex: str | None = None
    number: float | None = None
    abs_tolerance: float = Field(default=0.0, ge=0.0)
    rel_tolerance: float = Field(default=0.0, ge=0.0)
    json_schema: JsonObject | None = None
    json_expected: JsonValue = None
    label: str | None = None
    allowed_labels: tuple[str, ...] = ()
    assertions: tuple[Assertion, ...] = ()
    tools: ToolExpectations | None = None
    citations: CitationExpectations | None = None
    rag: RagExpectations | None = None
    safety: SafetyExpectations | None = None
    max_latency_ms: float | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    reference_text: str | None = None
    reference_tool_calls: tuple[ToolCall, ...] = ()
    reference_citations: tuple[Citation, ...] = ()

    @field_validator("regex")
    @classmethod
    def _regex_compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        return value


class TestMetadata(DomainModel):
    """Provenance and lifecycle information for a test case."""

    __test__ = False

    severity: Severity = Severity.MEDIUM
    status: TestStatus = TestStatus.ACTIVE
    source: TestSource = TestSource.HAND_WRITTEN
    version: int = Field(default=1, ge=1)
    description: str = ""
    incident_id: str | None = None
    trace_id: str | None = None
    parent_test_id: str | None = None
    mutation: str | None = None
    created: date | None = None
    reviewer: str | None = None


class TestCase(DomainModel):
    """A single reliability test: input, expected behaviour and evaluators to apply."""

    __test__ = False

    test_id: str
    category: str = Field(min_length=1)
    input: TestInput
    expected: ExpectedBehavior | None = None
    evaluators: tuple[str, ...] = Field(min_length=1)
    tags: frozenset[str] = frozenset()
    metadata: TestMetadata = Field(default_factory=TestMetadata)

    @field_validator("test_id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _ID_PATTERN.match(value):
            raise ValueError(f"test_id {value!r} must match {_ID_PATTERN.pattern}")
        return value

    @field_serializer("tags")
    def _sorted_tags(self, tags: frozenset[str]) -> list[str]:
        # Sorted so that canonical JSON (and therefore content hashes) is deterministic.
        return sorted(tags)

    def definition_hash(self) -> str:
        """Hash of everything that defines the test's semantics (input, expectation, evaluators).

        Lifecycle metadata (status, reviewer) is excluded so promoting a test from
        DRAFT to ACTIVE does not make it incomparable with earlier runs.
        """
        return sha256_hex(
            canonical_dumps(
                self.model_dump(mode="json", include={"input", "expected", "evaluators"})
            )
        )[:16]

    @property
    def severity(self) -> Severity:
        return self.metadata.severity

    @property
    def is_critical(self) -> bool:
        return self.metadata.severity is Severity.CRITICAL


class TestSuite(DomainModel):
    """A versioned, named collection of test cases."""

    __test__ = False

    suite_id: str
    version: str = "1"
    description: str = ""
    tier: Literal["smoke", "core", "release", "redteam", "external"] = "core"
    tests: tuple[TestCase, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> TestSuite:
        seen: set[str] = set()
        for test in self.tests:
            if test.test_id in seen:
                raise InvalidTestCase(
                    f"duplicate test_id {test.test_id!r} in suite {self.suite_id}"
                )
            seen.add(test.test_id)
        return self

    def __iter__(self) -> Iterator[TestCase]:  # type: ignore[override]
        return iter(self.tests)

    def __len__(self) -> int:
        return len(self.tests)

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({t.category for t in self.tests}))

    def select(
        self,
        *,
        tags: Iterable[str] = (),
        exclude_tags: Iterable[str] = (),
        categories: Iterable[str] = (),
        statuses: Iterable[TestStatus] = (TestStatus.ACTIVE,),
    ) -> TestSuite:
        """Return a filtered copy. By default only ACTIVE tests are kept."""
        want_tags, drop_tags = frozenset(tags), frozenset(exclude_tags)
        want_cats, want_status = frozenset(categories), frozenset(statuses)
        kept = tuple(
            t
            for t in self.tests
            if (not want_tags or t.tags & want_tags)
            and not (t.tags & drop_tags)
            and (not want_cats or t.category in want_cats)
            and (not want_status or t.metadata.status in want_status)
        )
        return self.model_copy(update={"tests": kept})

    def merged_with(self, other: TestSuite, *, suite_id: str | None = None) -> TestSuite:
        """Concatenate two suites (test IDs must stay unique)."""
        return TestSuite(
            suite_id=suite_id or f"{self.suite_id}+{other.suite_id}",
            version=f"{self.version}+{other.version}",
            description=self.description,
            tier=self.tier,
            tests=self.tests + other.tests,
        )
