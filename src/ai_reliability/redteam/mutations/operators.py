"""Deterministic, meaning-preserving input mutations for robustness and metamorphic testing.

Each operator maps (TestInput, seed) -> TestInput and is pure: the same input
and seed always give the same output. Operators are intended to preserve the
correct answer; where that is only approximately true (typos), it is noted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from ai_reliability.determinism import seeded_rng
from ai_reliability.domain.content import RetrievedDocument
from ai_reliability.domain.test_case import TestInput

MutationFn = Callable[[TestInput, int], TestInput]


@dataclass(frozen=True, slots=True)
class MutationOperator:
    name: str
    description: str
    apply: MutationFn
    preserves_answer: bool = True


def _with_prompt(test: TestInput, prompt: str) -> TestInput:
    return test.model_copy(update={"prompt": prompt})


def whitespace(test: TestInput, seed: int) -> TestInput:
    rng = seeded_rng("ws", seed, test.prompt)
    words = test.prompt.split(" ")
    out = [w + (" " if rng.random() < 0.3 else "") for w in words]
    return _with_prompt(test, "  " + " ".join(out) + "\n\n")


def capitalization(test: TestInput, seed: int) -> TestInput:
    # Lower-cases the prompt except quoted spans and tokens containing digits or symbols
    # (identifiers, chemical symbols) so the expected answer is unaffected.
    def lower(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if re.search(r"[\d_'\"]", token) or token.isupper() else token.lower()

    return _with_prompt(test, re.sub(r"\S+", lower, test.prompt))


def typos(test: TestInput, seed: int) -> TestInput:
    """Swap two inner letters in up to two long words (approximately answer-preserving)."""
    rng = seeded_rng("typo", seed, test.prompt)
    words = test.prompt.split(" ")
    candidates = [i for i, w in enumerate(words) if w.isalpha() and len(w) >= 6]
    for i in rng.sample(candidates, k=min(2, len(candidates))):
        w = words[i]
        j = rng.randrange(1, len(w) - 2)
        words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2 :]
    return _with_prompt(test, " ".join(words))


def instruction_reordering(test: TestInput, seed: int) -> TestInput:
    """Move a trailing format instruction ('Answer with ...') to the front."""
    parts = re.split(
        r"(?<=[.?!])\s+(?=(?:Answer|Reply|Return|Output|Respond)\b)", test.prompt, maxsplit=1
    )
    if len(parts) != 2:
        return test
    return _with_prompt(test, f"{parts[1].strip()} {parts[0].strip()}")


def question_formatting(test: TestInput, seed: int) -> TestInput:
    return _with_prompt(test, f"Question: {test.prompt}\nAnswer:")


def irrelevant_context(test: TestInput, seed: int) -> TestInput:
    fillers = (
        "Context: the office kitchen was renovated last spring.",
        "Background: the weather this week has been mild and dry.",
        "FYI: the team offsite is planned for the third week of the month.",
    )
    rng = seeded_rng("ctx", seed, test.prompt)
    return _with_prompt(test, f"{rng.choice(fillers)} {test.prompt}")


def longer_context(test: TestInput, seed: int) -> TestInput:
    padding = " ".join(
        ["This sentence is unrelated padding included to lengthen the context."] * 12
    )
    return _with_prompt(test, f"{padding}\n\n{test.prompt}")


def distractor_document(test: TestInput, seed: int) -> TestInput:
    distractor = RetrievedDocument(
        doc_id="distractor-1",
        text="The cafeteria serves soup on Fridays. Parking permits renew every January.",
        source="fixture://distractor",
    )
    return test.model_copy(update={"documents": (*test.documents, distractor)})


def json_formatting(test: TestInput, seed: int) -> TestInput:
    """Re-indent any JSON object embedded in the prompt (semantics unchanged)."""

    def reformat(match: re.Match[str]) -> str:
        try:
            return json.dumps(json.loads(match.group(0)), indent=2, sort_keys=True)
        except ValueError:
            return match.group(0)

    return _with_prompt(test, re.sub(r"\{[^{}]*\}", reformat, test.prompt))


def unicode_variants(test: TestInput, seed: int) -> TestInput:
    """Replace spaces with no-break spaces and straight quotes with typographic quotes."""
    text = test.prompt.replace(" ", " ", 3).replace("'", "’")
    return _with_prompt(test, text)


_PARAPHRASES = (
    (r"\bWhat is\b", "Tell me"),
    (r"\bAnswer with\b", "Respond with"),
    (r"\bHow many\b", "What number of"),
    (r"\bReturn JSON\b", "Output JSON"),
)


def paraphrase_fixture(test: TestInput, seed: int) -> TestInput:
    """Table-driven paraphrase (deterministic; no model involved)."""
    text = test.prompt
    for pattern, replacement in _PARAPHRASES:
        text = re.sub(pattern, replacement, text, count=1)
    return _with_prompt(test, text)


OPERATORS: dict[str, MutationOperator] = {
    op.name: op
    for op in (
        MutationOperator("whitespace", "extra/irregular whitespace", whitespace),
        MutationOperator("capitalization", "lower-case non-identifier words", capitalization),
        MutationOperator("typos", "swap inner letters in long words", typos, preserves_answer=True),
        MutationOperator(
            "instruction_reordering", "move format instruction to the front", instruction_reordering
        ),
        MutationOperator(
            "question_formatting", "wrap as 'Question: ... Answer:'", question_formatting
        ),
        MutationOperator(
            "irrelevant_context", "prepend an irrelevant sentence", irrelevant_context
        ),
        MutationOperator("longer_context", "prepend unrelated padding", longer_context),
        MutationOperator(
            "distractor_document", "add an irrelevant retrieved document", distractor_document
        ),
        MutationOperator("json_formatting", "re-indent embedded JSON", json_formatting),
        MutationOperator("unicode", "no-break spaces and typographic quotes", unicode_variants),
        MutationOperator("paraphrase", "table-driven paraphrase", paraphrase_fixture),
    )
}
