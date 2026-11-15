"""Oracle knowledge for mock systems.

A mock system "knows" the reference answer for inputs it was built with, which
lets every reliability feature be exercised without a model. Lookup is either
exact (a brittle system) or fuzzy over character trigrams (a system that is
robust to small perturbations) — this difference is what metamorphic and
robustness tests detect.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ai_reliability.domain.base import canonical_dumps, sha256_hex
from ai_reliability.domain.content import Citation, ToolCall
from ai_reliability.domain.test_case import ExpectedBehavior, TestCase, TestInput

REFUSAL_TEXT = "I can't help with that request."
UNKNOWN_TEXT = "I don't know."


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """Reference behaviour for one known input."""

    test_id: str
    category: str
    tags: frozenset[str]
    text: str
    tool_calls: tuple[ToolCall, ...]
    citations: tuple[Citation, ...]


def input_text(test: TestInput) -> str:
    """All text a system sees, concatenated in a stable order."""
    parts = [test.system_prompt or "", test.developer_prompt or "", test.prompt]
    parts.extend(d.text for d in test.documents)
    parts.extend(o.output for o in test.tool_outputs)
    return "\n".join(parts)


def input_key(test: TestInput) -> str:
    """Exact lookup key for an input."""
    return sha256_hex(canonical_dumps(test.model_dump(mode="json")))


def _trigrams(text: str) -> frozenset[str]:
    norm = re.sub(r"\s+", " ", text.casefold()).strip()
    return frozenset(norm[i : i + 3] for i in range(max(1, len(norm) - 2)))


def reference_response(expected: ExpectedBehavior | None) -> str:
    """Derive the reference response text from an expectation."""
    if expected is None:
        return "OK"
    if expected.reference_text is not None:
        return expected.reference_text
    if expected.safety is not None and expected.safety.must_refuse:
        return REFUSAL_TEXT
    if expected.exact is not None:
        return expected.exact
    if expected.json_expected is not None:
        return canonical_dumps(expected.json_expected)
    if expected.label is not None:
        return expected.label
    if expected.number is not None:
        num = expected.number
        return str(int(num)) if float(num).is_integer() else str(num)
    if expected.contains_all:
        return " ".join(expected.contains_all)
    if expected.contains_any:
        return expected.contains_any[0]
    if expected.rag is not None and expected.rag.required_facts:
        return " ".join(expected.rag.required_facts)
    return "OK"


class KnowledgeBase:
    """Maps inputs to reference behaviour, exactly or fuzzily."""

    def __init__(self, entries: Iterable[tuple[TestInput, KnowledgeEntry]] = ()) -> None:
        self._exact: dict[str, KnowledgeEntry] = {}
        self._fuzzy: list[tuple[frozenset[str], KnowledgeEntry]] = []
        for test_input, entry in entries:
            self.add(test_input, entry)

    @classmethod
    def from_tests(cls, tests: Iterable[TestCase]) -> KnowledgeBase:
        kb = cls()
        for test in tests:
            kb.add_test(test)
        return kb

    def add_test(self, test: TestCase) -> None:
        expected = test.expected
        self.add(
            test.input,
            KnowledgeEntry(
                test_id=test.test_id,
                category=test.category,
                tags=test.tags,
                text=reference_response(expected),
                tool_calls=expected.reference_tool_calls if expected else (),
                citations=expected.reference_citations if expected else (),
            ),
        )

    def add(self, test_input: TestInput, entry: KnowledgeEntry) -> None:
        self._exact[input_key(test_input)] = entry
        self._fuzzy.append((_trigrams(input_text(test_input)), entry))

    def __len__(self) -> int:
        return len(self._exact)

    def lookup(
        self, test_input: TestInput, *, fuzzy: bool, threshold: float
    ) -> KnowledgeEntry | None:
        exact = self._exact.get(input_key(test_input))
        if exact is not None or not fuzzy:
            return exact
        grams = _trigrams(input_text(test_input))
        best: KnowledgeEntry | None = None
        best_score = 0.0
        for candidate, entry in self._fuzzy:
            union = len(grams | candidate)
            score = len(grams & candidate) / union if union else 0.0
            if score > best_score:
                best, best_score = entry, score
        return best if best_score >= threshold else None
