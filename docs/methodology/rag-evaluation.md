# RAG evaluation

RAG evaluators (`evaluators/rag/evaluators.py`) are deterministic and need no vector database or
model. They evaluate the documents a pipeline *reports* as retrieved (`SystemResponse.retrieved_documents`,
from the canonical RAG contract), falling back to the test's fixture documents
(`TestInput.documents`) when the system reports none.

Expectations live in `ExpectedBehavior.rag` (`RagExpectations`) and `ExpectedBehavior.citations`
(`CitationExpectations`):

```yaml
# illustrative test fragment
expected:
  rag:
    relevant_doc_ids: [kb-refunds-v3]
    required_facts: ["30 days"]
    forbidden_claims: ["60 days"]
    min_grounding: 0.6          # default
  citations:
    required: true              # default
    min_citations: 1            # default
    must_cite: [kb-refunds-v3]
    claim_support: [{doc_id: kb-refunds-v3, claim: "30 days"}]
evaluators: [retrieval_relevance, citation, groundedness, hallucination]
```

## Retrieval relevance

`retrieval_relevance`: recall = |relevant ∩ retrieved| / |relevant|; precision =
|relevant ∩ retrieved| / |retrieved| (reported in `details`). The score is recall and the test
passes only when every relevant document id was retrieved. Relevance is by document id only;
ranking and scores are not evaluated.

`context_utilization`: of the relevant documents available (all available documents if no
`relevant_doc_ids`), the share that is either cited or shares at least 3 content tokens with the
answer. Passes at >= 0.5 (fixed threshold). SKIPPED when no relevant document is available.

## Citations

`citation` fails on any of:

* a citation to a document id that is not among the available documents (fabricated source);
* fewer than `min_citations` citations when `required`;
* a `must_cite` document not cited;
* a citation `quote` that does not occur (case-insensitive substring) in the cited document;
* a `claim_support` claim that appears in the answer while its document is not cited.

`details` reports `citations`, `fabricated`, `misquoted`.

## Groundedness (lexical)

1. The answer is split into sentences on `.`, `!`, `?` followed by whitespace, after removing
   bracketed citation marks like `[doc-1]`.
2. Content tokens: lower-cased alphanumeric tokens longer than 2 characters, minus an English
   stop-word list.
3. A sentence's support = the best fraction of its content tokens found in **any single**
   document. A sentence is supported when support >= 0.6 (fixed per-sentence threshold).
4. `groundedness` score = supported sentences / sentences; the test passes when the score
   >= `rag.min_grounding` (default 0.6). `details` include `unsupported_claim_rate = 1 − score`.
   SKIPPED when there are no documents or no content sentences.

Two different 0.6 values are involved: the per-sentence lexical threshold (hard-coded) and the
answer-level `min_grounding` (configurable).

### Limits of lexical groundedness

* It is word overlap, not entailment. Paraphrased but faithful answers can be marked
  unsupported; answers that reuse the document's words while contradicting it (negation,
  swapped numbers, wrong entity relations) can be marked supported.
* Support must come from a single document; a sentence synthesizing two documents can fail.
* Tokenization and the stop-word list are English-oriented.
* Very short answers produce few sentences, so the rate is coarse.

Use it as a cheap regression signal. For semantic faithfulness add an NLI or calibrated judge
evaluator ([judge-calibration.md](judge-calibration.md)).

## Hallucination types

`hallucination` reports *types* instead of one vague score (`details.hallucination_types`):

| Type | Detected when | Failure category |
|---|---|---|
| `fabricated_citation` | a citation points at a document that is not available | `citation_failure` |
| `wrong_answer` | a `required_facts` string is missing from the answer, or a `forbidden_claims` string is present (case-insensitive substring) | `wrong_answer` |
| `unsupported_answer` | test is answerable (`relevant_doc_ids` non-empty), documents exist, and lexical groundedness < `min_grounding` | `hallucination` |
| `overconfident` | test is unanswerable (`relevant_doc_ids` empty) and the answer contains no uncertainty phrase ("don't know", "not enough information", "cannot determine", "not mentioned", "unable to find", ...) | `hallucination` |

The failure category is taken from the first detected type in the order listed. Note that
"unanswerable" is defined by the test author leaving `relevant_doc_ids` empty, not by inspecting
the retrieved documents, and uncertainty detection is a regex.

## Composite

`rag_grounded` = `citation` + `groundedness` + `hallucination` (`all` mode). Its score is the
mean of component scores; the outcome comes from the components' outcomes.

## Metrics

Run-level: `citation_validity` (pass rate of `citation` evaluations) and `groundedness_rate`
(pass rate of `groundedness` evaluations, skipped evaluations excluded). See
[../reliability/metrics.md](../reliability/metrics.md).

## Mock behaviour

`synthetic/buggy` applies `hallucinated` to 50% of `rag` tests: it appends "This was confirmed
by an independent 2019 audit." and a citation to `doc-fabricated-404`, which `citation` and
`hallucination` (`fabricated_citation`) detect. These are mock results only.
