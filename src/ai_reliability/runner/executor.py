"""Bounded asynchronous evaluation runner with timeouts, retries, rate limits and resume."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from ai_reliability.clock import Clock, SystemClock
from ai_reliability.config.models import Pricing, RunnerSettings
from ai_reliability.domain.base import canonical_dumps, sha256_hex
from ai_reliability.domain.enums import CapturePolicy, ErrorCode, FailureCategory, Outcome
from ai_reliability.domain.result import CaseResult, EvaluationResult
from ai_reliability.domain.system import SystemResponse, Timing
from ai_reliability.domain.test_case import TestCase
from ai_reliability.evaluators.composite import combine_outcomes
from ai_reliability.evaluators.registry import EvaluatorRegistry
from ai_reliability.privacy.redaction import Redactor
from ai_reliability.runner.checkpoint import CheckpointJournal, execution_id
from ai_reliability.runner.observer import NullObserver, RunObserver
from ai_reliability.runner.policies import RateLimiter, RetryPolicy
from ai_reliability.systems.base import SystemUnderTest
from ai_reliability.systems.mock.knowledge import input_text

FAILURE_PRIORITY = (
    FailureCategory.SAFETY_FAILURE,
    FailureCategory.TOOL_FAILURE,
    FailureCategory.CITATION_FAILURE,
    FailureCategory.HALLUCINATION,
    FailureCategory.RETRIEVAL_FAILURE,
    FailureCategory.INSTRUCTION_FAILURE,
    FailureCategory.FORMAT_FAILURE,
    FailureCategory.WRONG_ANSWER,
    FailureCategory.PERFORMANCE,
)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """All case results of a run (including resumed ones) and whether it was interrupted."""

    results: tuple[CaseResult, ...]
    resumed: int
    executed: int
    interrupted: bool
    duration_s: float


@dataclass(frozen=True, slots=True)
class _Execution:
    test: TestCase
    repetition: int
    execution_id: str
    test_hash: str


def primary_failure(evaluations: Sequence[EvaluationResult]) -> tuple[FailureCategory | None, str]:
    """Pick the most severe failure category among failing evaluations, with its reason."""
    failing = [e for e in evaluations if e.outcome in {Outcome.FAIL, Outcome.ERROR}]
    if not failing:
        return None, ""

    def rank(e: EvaluationResult) -> int:
        cat = e.failure_category
        return FAILURE_PRIORITY.index(cat) if cat in FAILURE_PRIORITY else len(FAILURE_PRIORITY)

    worst = min(failing, key=rank)
    return worst.failure_category, f"{worst.evaluator}: {worst.reason}"[:500]


class EvaluationRunner:
    """Executes test cases against a system and evaluates the responses.

    * bounded concurrency (semaphore),
    * per-call timeout, transport-only retries with backoff,
    * optional requests/sec and tokens/min limits,
    * deterministic execution IDs + checkpoint journal for resume,
    * cancellation-safe: completed executions are journaled before return.
    """

    def __init__(
        self,
        system: SystemUnderTest,
        evaluators: EvaluatorRegistry,
        *,
        settings: RunnerSettings | None = None,
        clock: Clock | None = None,
        observer: RunObserver | None = None,
        rate_limiter: RateLimiter | None = None,
        pricing: Pricing | None = None,
        capture: CapturePolicy = CapturePolicy.FAILED_CASES,
        redactor: Redactor | None = None,
    ) -> None:
        self._system = system
        self._evaluators = evaluators
        self._settings = settings or RunnerSettings()
        self._clock = clock or SystemClock()
        self._observer = observer or NullObserver()
        self._limiter = rate_limiter or RateLimiter(self._clock)
        self._pricing = pricing
        self._capture = capture
        self._redactor = redactor or Redactor()
        self._retry = RetryPolicy(
            max_attempts=self._settings.max_attempts,
            initial_s=self._settings.backoff_initial_s,
            max_s=self._settings.backoff_max_s,
        )
        self._in_flight = 0

    async def run(
        self,
        run_id: str,
        tests: Sequence[TestCase],
        *,
        repetitions: int = 1,
        journal: CheckpointJournal | None = None,
    ) -> RunOutcome:
        for test in tests:
            self._evaluators.validate_test(test)
        plan = [
            _Execution(
                t,
                rep,
                execution_id(run_id, t.test_id, t.definition_hash(), rep),
                t.definition_hash(),
            )
            for t in tests
            for rep in range(repetitions)
        ]
        done = journal.load() if journal else {}
        planned_ids = {e.execution_id for e in plan}
        resumed = {k: v for k, v in done.items() if k in planned_ids}
        pending = [e for e in plan if e.execution_id not in resumed]
        self._observer.on_run_started(run_id, len(plan), len(resumed))
        start = self._clock.monotonic()
        results: dict[str, CaseResult] = dict(resumed)
        semaphore = asyncio.Semaphore(self._settings.concurrency)
        interrupted = False

        async def worker(execution: _Execution) -> None:
            async with semaphore:
                self._in_flight += 1
                self._observer.on_case_started(run_id, execution.test.test_id, self._in_flight)
                try:
                    result = await self._execute(run_id, execution)
                finally:
                    self._in_flight -= 1
                results[result.execution_id] = result
                if journal is not None:
                    journal.append(result)
                self._observer.on_case_finished(run_id, result, self._in_flight)

        try:
            async with asyncio.TaskGroup() as group:
                for execution in pending:
                    group.create_task(worker(execution))
        except asyncio.CancelledError:
            interrupted = True
        duration = self._clock.monotonic() - start
        self._observer.on_run_finished(run_id, duration, interrupted)
        ordered = tuple(results[e.execution_id] for e in plan if e.execution_id in results)
        return RunOutcome(
            results=ordered,
            resumed=len(resumed),
            executed=len(ordered) - len(resumed),
            interrupted=interrupted,
            duration_s=duration,
        )

    async def _call_system(self, test: TestCase) -> SystemResponse:
        attempt = 0
        key = f"{test.test_id}:{test.definition_hash()}"
        while True:
            attempt += 1
            await self._limiter.acquire(estimated_tokens=len(input_text(test.input)) // 4)
            start = self._clock.monotonic()
            try:
                response = await asyncio.wait_for(
                    self._system.execute(test.input), timeout=self._settings.timeout_s
                )
            except TimeoutError:
                response = SystemResponse.failure(
                    ErrorCode.TIMEOUT,
                    f"no response within {self._settings.timeout_s}s",
                    retryable=True,
                )
            except Exception as exc:  # noqa: BLE001 - adapter bugs become system errors
                response = SystemResponse.failure(ErrorCode.SYSTEM_ERROR, type(exc).__name__)
            if response.timing.e2e_ms is None:
                elapsed = (self._clock.monotonic() - start) * 1000.0
                response = response.model_copy(
                    update={"timing": response.timing.model_copy(update={"e2e_ms": elapsed})}
                )
            if not self._retry.should_retry(response, attempt):
                return response.model_copy(update={"attempts": attempt})
            await self._clock.sleep(self._retry.backoff(attempt, key))

    async def _evaluate(self, test: TestCase, response: SystemResponse) -> list[EvaluationResult]:
        results: list[EvaluationResult] = []
        for name in test.evaluators:
            evaluator = self._evaluators.get(name)
            try:
                result = await asyncio.wait_for(
                    evaluator.evaluate(test, response), timeout=self._settings.evaluator_timeout_s
                )
            except Exception as exc:  # noqa: BLE001 - evaluator crash is reported, not fatal
                result = EvaluationResult(
                    evaluator=evaluator.name,
                    evaluator_version=evaluator.version,
                    outcome=Outcome.ERROR,
                    reason=f"evaluator failure: {type(exc).__name__}",
                    failure_category=FailureCategory.SYSTEM_ERROR,
                )
            results.append(result)
        return results

    async def _execute(self, run_id: str, execution: _Execution) -> CaseResult:
        test = execution.test
        response = await self._call_system(test)
        if self._pricing is not None and response.usage is not None:
            response = response.model_copy(
                update={
                    "cost_usd": self._pricing.cost(
                        response.usage.input_tokens, response.usage.output_tokens
                    )
                }
            )
        evaluations: list[EvaluationResult] = []
        error_code = response.error.code if response.error else None
        category: FailureCategory | None
        if response.error is not None:
            outcome = Outcome.ERROR
            category = (
                FailureCategory.TIMEOUT
                if response.error.code is ErrorCode.TIMEOUT
                else FailureCategory.SYSTEM_ERROR
            )
            reason = f"{response.error.code}: {response.error.message}"[:500]
        else:
            evaluations = await self._evaluate(test, response)
            # If every evaluator skipped, nothing was verified: the case stays SKIPPED.
            outcome = combine_outcomes([e.outcome for e in evaluations], "all")
            category, reason = primary_failure(evaluations)
        keep_text = self._capture is CapturePolicy.ALL or (
            self._capture is CapturePolicy.FAILED_CASES and outcome is not Outcome.PASS
        )
        timing: Timing = response.timing
        return CaseResult(
            execution_id=execution.execution_id,
            run_id=run_id,
            test_id=test.test_id,
            test_hash=execution.test_hash,
            category=test.category,
            severity=test.severity,
            tags=tuple(sorted(test.tags)),
            repetition=execution.repetition,
            outcome=outcome,
            evaluations=tuple(evaluations),
            failure_category=category,
            failure_reason=reason,
            error_code=error_code,
            latency_ms=timing.e2e_ms,
            ttft_ms=timing.ttft_ms,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            cost_usd=response.cost_usd,
            response_chars=len(response.text),
            tool_call_count=len(response.tool_calls),
            attempts=response.attempts,
            input_sha256=sha256_hex(canonical_dumps(test.input.model_dump(mode="json")))[:32],
            response_sha256=sha256_hex(response.text)[:32],
            response_text=self._redactor.redact(response.text).text[:4000] if keep_text else None,
        )
