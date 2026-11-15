"""Optional LLM-as-a-judge evaluator. Never the default; always records provenance."""

from __future__ import annotations

import re

from ai_reliability.domain.enums import FailureCategory, Outcome
from ai_reliability.domain.result import EvaluationResult
from ai_reliability.domain.system import SystemResponse
from ai_reliability.domain.test_case import TestCase
from ai_reliability.evaluators.base import expected_of
from ai_reliability.evaluators.judge.client import JudgeClient, JudgeRequest, Rubric

# Text in a response that tries to address the grader is a judge-manipulation signal.
_JUDGE_ATTACK = re.compile(
    r"\b(?:as the (?:judge|grader|evaluator)|dear (?:judge|grader)|(?:grade|score|rate) this "
    r"(?:answer|response) as|ignore (?:the|your) rubric|output\s+\"?verdict\"?\s*:\s*\"?pass)",
    re.IGNORECASE,
)


class LLMJudgeEvaluator:
    """Grades responses with a judge model against a versioned rubric.

    Records judge model, revision, prompt template hash, temperature, rubric
    version and run ID in ``details``. Invalid judge output or a suspected
    judge-manipulation attempt yields ``NEEDS_REVIEW`` (never a silent PASS).
    """

    def __init__(
        self, client: JudgeClient, rubric: Rubric, *, run_id: str = "", name: str = "llm_judge"
    ):
        self.name = name
        self._client = client
        self._rubric = rubric
        self._run_id = run_id
        prov = client.provenance(rubric)
        self.version = (
            f"{rubric.rubric_id}@{rubric.version}/{prov.judge_model}/{prov.prompt_sha256[:8]}"
        )

    def validate(self, test_case: TestCase) -> None:
        return None

    async def evaluate(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
        prov = self._client.provenance(self._rubric)
        details: dict[str, str | int | float | bool | None] = {
            "judge_model": prov.judge_model,
            "judge_model_revision": prov.model_revision,
            "judge_prompt_template": prov.prompt_template_id,
            "judge_prompt_sha256": prov.prompt_sha256[:16],
            "judge_temperature": prov.temperature,
            "rubric_id": prov.rubric_id,
            "rubric_version": prov.rubric_version,
            "run_id": self._run_id,
        }
        if _JUDGE_ATTACK.search(response.text):
            return EvaluationResult(
                evaluator=self.name,
                evaluator_version=self.version,
                outcome=Outcome.NEEDS_REVIEW,
                reason="response appears to address the grader; routed to human review",
                failure_category=FailureCategory.SAFETY_FAILURE,
                details=details,
            )
        verdict = await self._client.judge(
            JudgeRequest(
                rubric=self._rubric,
                question=test_case.input.prompt,
                response=response.text,
                reference=expected_of(test_case).reference_text,
            )
        )
        if not verdict.valid or verdict.passed is None:
            return EvaluationResult(
                evaluator=self.name,
                evaluator_version=self.version,
                outcome=Outcome.NEEDS_REVIEW,
                reason=f"invalid judge output: {verdict.rationale}",
                details=details,
            )
        return EvaluationResult(
            evaluator=self.name,
            evaluator_version=self.version,
            outcome=Outcome.PASS if verdict.passed else Outcome.FAIL,
            score=verdict.score,
            reason=verdict.rationale or ("judge: pass" if verdict.passed else "judge: fail"),
            failure_category=None if verdict.passed else FailureCategory.WRONG_ANSWER,
            details=details,
        )
