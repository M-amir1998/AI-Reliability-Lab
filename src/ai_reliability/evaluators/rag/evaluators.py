"""RAG evaluators: retrieval relevance, citations, groundedness, utilization, hallucination types.

Grounding here is *lexical*: a sentence counts as supported when most of its
content words occur in a single retrieved document. This is deterministic and
cheap but is not semantic entailment — paraphrased support can be missed and
lexically-similar contradictions can pass. Use an NLI/judge evaluator for that.
"""

from __future__ import annotations

import re

from ai_reliability.domain.content import RetrievedDocument
from ai_reliability.domain.enums import FailureCategory
from ai_reliability.domain.result import DetailValue, EvaluationResult
from ai_reliability.domain.system import SystemResponse
from ai_reliability.domain.test_case import RagExpectations, TestCase
from ai_reliability.evaluators.base import BaseEvaluator, expected_of

_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "which",
        "who",
        "whom",
        "what",
        "when",
        "where",
        "why",
        "how",
        "than",
        "then",
        "there",
        "their",
        "they",
        "them",
        "he",
        "she",
        "his",
        "her",
        "we",
        "our",
        "you",
        "your",
        "i",
        "me",
        "my",
        "not",
        "no",
        "yes",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "can",
        "could",
        "will",
        "would",
        "should",
        "may",
        "might",
        "must",
        "also",
        "into",
        "about",
        "over",
        "under",
        "after",
        "before",
        "between",
        "per",
        "via",
    ]
)
_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CITATION_MARK = re.compile(r"\[[^\]]{1,64}\]")
UNCERTAINTY = re.compile(
    r"\b(?:don't know|do not know|not (?:enough|sufficient) information|cannot (?:find|determine|answer)|"
    r"can't (?:find|determine|answer)|not (?:mention(?:ed)?|state[ds]?|provided?|cover(?:ed)?)|no information|unclear|"
    r"unable to (?:find|determine))\b",
    re.IGNORECASE,
)


def content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.casefold()) if t not in _STOP and len(t) > 2}


def sentences(text: str) -> list[str]:
    clean = _CITATION_MARK.sub("", text)
    return [s.strip() for s in _SENTENCE.split(clean) if content_tokens(s)]


def available_documents(
    test_case: TestCase, response: SystemResponse
) -> tuple[RetrievedDocument, ...]:
    """Documents the system reports as retrieved, else the test's fixture documents."""
    return response.retrieved_documents or test_case.input.documents


def sentence_support(sentence: str, docs: tuple[RetrievedDocument, ...]) -> float:
    """Best fraction of the sentence's content tokens found in any single document."""
    tokens = content_tokens(sentence)
    if not tokens:
        return 1.0
    return max((len(tokens & content_tokens(d.text)) / len(tokens) for d in docs), default=0.0)


def grounding(
    answer: str, docs: tuple[RetrievedDocument, ...], threshold: float = 0.6
) -> tuple[int, int]:
    """(supported sentences, total sentences) under lexical support ``threshold``."""
    sents = sentences(answer)
    supported = sum(1 for s in sents if sentence_support(s, docs) >= threshold)
    return supported, len(sents)


def _rag(test_case: TestCase) -> RagExpectations:
    return expected_of(test_case).rag or RagExpectations()


class RetrievalRelevanceEvaluator(BaseEvaluator):
    """Retrieved documents cover all ``relevant_doc_ids`` (recall), with precision reported."""

    name = "retrieval_relevance"
    version = "1"
    failure_category = FailureCategory.RETRIEVAL_FAILURE

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, bool(_rag(test_case).relevant_doc_ids), "rag.relevant_doc_ids")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        relevant = set(_rag(test_case).relevant_doc_ids)
        retrieved = {d.doc_id for d in available_documents(test_case, response)}
        hits = relevant & retrieved
        recall = len(hits) / len(relevant)
        precision = len(hits) / len(retrieved) if retrieved else 0.0
        r, p = round(recall, 4), round(precision, 4)
        if recall == 1.0:
            return self.passed(
                "all relevant documents retrieved", score=recall, recall=r, precision=p
            )
        return self.failed(
            f"retrieved {len(hits)}/{len(relevant)} relevant documents",
            score=recall,
            recall=r,
            precision=p,
        )


class CitationEvaluator(BaseEvaluator):
    """Citations exist, refer to retrieved sources, quote them faithfully and cover required docs."""

    name = "citation"
    version = "1"
    failure_category = FailureCategory.CITATION_FAILURE

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, expected_of(test_case).citations is not None, "expected.citations")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        exp = expected_of(test_case).citations
        assert exp is not None
        docs = {d.doc_id: d for d in available_documents(test_case, response)}
        cited = [c.doc_id for c in response.citations]
        problems: list[str] = []
        fabricated = sorted({c for c in cited if c not in docs})
        if fabricated:
            problems.append(f"{len(fabricated)} citation(s) to non-retrieved sources")
        if exp.required and len(cited) < exp.min_citations:
            problems.append(f"{len(cited)} citation(s) < required {exp.min_citations}")
        missing = [d for d in exp.must_cite if d not in cited]
        if missing:
            problems.append(f"missing required citation(s): {', '.join(missing)}")
        misquoted = 0
        for citation in response.citations:
            doc = docs.get(citation.doc_id)
            if (
                doc is not None
                and citation.quote
                and citation.quote.casefold() not in doc.text.casefold()
            ):
                misquoted += 1
        if misquoted:
            problems.append(f"{misquoted} quote(s) not found in cited source")
        unsupported = 0
        answer = response.text.casefold()
        for support in exp.claim_support:
            if support.claim and support.claim.casefold() in answer and support.doc_id not in cited:
                unsupported += 1
        if unsupported:
            problems.append(f"{unsupported} fixture claim(s) stated without citing their source")
        details: dict[str, DetailValue] = {
            "citations": len(cited),
            "fabricated": len(fabricated),
            "misquoted": misquoted,
        }
        if problems:
            return self.failed("; ".join(problems), extra=details)
        return self.passed("citations valid", extra=details)


class GroundednessEvaluator(BaseEvaluator):
    """Fraction of answer sentences lexically supported by retrieved documents >= ``min_grounding``.

    Also reports ``unsupported_claim_rate`` (1 - groundedness).
    """

    name = "groundedness"
    version = "1"
    failure_category = FailureCategory.HALLUCINATION

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        docs = available_documents(test_case, response)
        if not docs:
            return self.skipped("no documents available to ground against")
        supported, total = grounding(response.text, docs)
        if total == 0:
            return self.skipped("answer has no content sentences")
        score = supported / total
        details: dict[str, DetailValue] = {
            "supported_sentences": supported,
            "sentences": total,
            "unsupported_claim_rate": round(1 - score, 4),
        }
        if score >= _rag(test_case).min_grounding:
            return self.passed("answer grounded in retrieved context", score=score, extra=details)
        return self.failed("answer contains unsupported sentences", score=score, extra=details)


class ContextUtilizationEvaluator(BaseEvaluator):
    """Share of relevant documents actually used (cited, or sharing >=3 content words with answer)."""

    name = "context_utilization"
    version = "1"
    failure_category = FailureCategory.RETRIEVAL_FAILURE

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        docs = available_documents(test_case, response)
        relevant_ids = set(_rag(test_case).relevant_doc_ids) or {d.doc_id for d in docs}
        relevant = [d for d in docs if d.doc_id in relevant_ids]
        if not relevant:
            return self.skipped("no relevant documents available")
        cited = {c.doc_id for c in response.citations}
        answer_tokens = content_tokens(response.text)
        used = sum(
            1
            for d in relevant
            if d.doc_id in cited or len(content_tokens(d.text) & answer_tokens) >= 3
        )
        score = used / len(relevant)
        if score >= 0.5:
            return self.passed(f"{used}/{len(relevant)} relevant documents used", score=score)
        return self.failed(f"only {used}/{len(relevant)} relevant documents used", score=score)


class HallucinationEvaluator(BaseEvaluator):
    """Distinguishes hallucination *types* instead of producing one vague score.

    * ``fabricated_citation`` — cites a source that was not retrieved.
    * ``wrong_answer`` — a required fact is missing or a forbidden claim is made.
    * ``unsupported_answer`` — sentences not lexically supported by context.
    * ``overconfident`` — context has no relevant document, yet no uncertainty is expressed.
    """

    name = "hallucination"
    version = "1"
    failure_category = FailureCategory.HALLUCINATION

    def validate(self, test_case: TestCase) -> None:
        self.require(test_case, expected_of(test_case).rag is not None, "expected.rag")

    def check(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        rag = _rag(test_case)
        docs = available_documents(test_case, response)
        doc_ids = {d.doc_id for d in docs}
        answer = response.text.casefold()
        types: list[str] = []
        if any(c.doc_id not in doc_ids for c in response.citations):
            types.append("fabricated_citation")
        missing = [f for f in rag.required_facts if f.casefold() not in answer]
        forbidden = [c for c in rag.forbidden_claims if c.casefold() in answer]
        if missing or forbidden:
            types.append("wrong_answer")
        answerable = bool(rag.relevant_doc_ids)
        if not answerable and not UNCERTAINTY.search(response.text):
            types.append("overconfident")
        if answerable and docs:
            supported, total = grounding(response.text, docs)
            if total and supported / total < rag.min_grounding:
                types.append("unsupported_answer")
        if not types:
            return self.passed("no hallucination detected by deterministic checks")
        category = {
            "fabricated_citation": FailureCategory.CITATION_FAILURE,
            "wrong_answer": FailureCategory.WRONG_ANSWER,
        }.get(types[0], FailureCategory.HALLUCINATION)
        return self.failed(
            "hallucination: " + ", ".join(types),
            category=category,
            hallucination_types=",".join(types),
            missing_facts=len(missing),
            forbidden_claims=len(forbidden),
        )
