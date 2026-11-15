from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_reliability.clock import FakeClock
from ai_reliability.config.models import Pricing, RunnerSettings
from ai_reliability.domain.enums import (
    CapturePolicy,
    ErrorCode,
    FailureCategory,
    Outcome,
    SystemKind,
)
from ai_reliability.domain.result import EvaluationResult
from ai_reliability.domain.system import SystemDescriptor, SystemResponse, TokenUsage
from ai_reliability.domain.test_case import ExpectedBehavior, TestCase, TestInput
from ai_reliability.errors import ResultCorruption
from ai_reliability.evaluators.registry import EvaluatorRegistry, default_evaluator_registry
from ai_reliability.runner.checkpoint import CheckpointJournal, execution_id
from ai_reliability.runner.executor import EvaluationRunner
from ai_reliability.runner.policies import RateLimiter, RetryPolicy
from tests.conftest import make_case, run

TESTS = [
    make_case(ExpectedBehavior(exact=f"a{i}"), test_id=f"t{i}", prompt=f"q{i}") for i in range(6)
]


class CountingSystem:
    """Answers correctly from the prompt; optional failure script per call number."""

    def __init__(
        self, failures: dict[int, SystemResponse] | None = None, *, wrong: bool = False
    ) -> None:
        self.calls = 0
        self.failures = failures or {}
        self.wrong = wrong
        self.descriptor = SystemDescriptor(system_id="counting", kind=SystemKind.MOCK)

    async def execute(self, test: TestInput) -> SystemResponse:
        self.calls += 1
        if self.calls in self.failures:
            return self.failures[self.calls]
        answer = "nope" if self.wrong else "a" + test.prompt[1:]
        return SystemResponse(text=answer, usage=TokenUsage(input_tokens=1000, output_tokens=500))


def runner(system: object, clock: FakeClock, **kw: object) -> EvaluationRunner:
    settings = kw.pop(
        "settings", RunnerSettings(concurrency=2, max_attempts=3, backoff_initial_s=1.0)
    )
    return EvaluationRunner(
        system, default_evaluator_registry(), settings=settings, clock=clock, **kw
    )  # type: ignore[arg-type]


def test_all_pass_and_ids_deterministic(clock: FakeClock) -> None:
    out = run(runner(CountingSystem(), clock).run("r1", TESTS))
    assert [r.outcome for r in out.results] == [Outcome.PASS] * 6 and not out.interrupted
    assert out.results[0].execution_id == execution_id("r1", "t0", TESTS[0].definition_hash(), 0)


def test_transient_errors_are_retried_with_backoff(clock: FakeClock) -> None:
    transient = SystemResponse.failure(ErrorCode.HTTP_ERROR, "503", retryable=True, status_code=503)
    system = CountingSystem({1: transient, 2: transient})
    out = run(
        runner(system, clock, settings=RunnerSettings(concurrency=1, backoff_initial_s=1.0)).run(
            "r", TESTS[:1]
        )
    )
    assert out.results[0].outcome is Outcome.PASS and out.results[0].attempts == 3
    assert (
        len(clock.sleeps) == 2 and clock.sleeps[1] > clock.sleeps[0] * 0.9
    )  # exponential, jittered


def test_non_retryable_errors_and_wrong_answers_are_not_retried(clock: FakeClock) -> None:
    fatal = SystemResponse.failure(ErrorCode.HTTP_ERROR, "500", retryable=False, status_code=500)
    system = CountingSystem({1: fatal})
    out = run(runner(system, clock, settings=RunnerSettings(concurrency=1)).run("r", TESTS[:1]))
    assert out.results[0].outcome is Outcome.ERROR and system.calls == 1
    assert out.results[0].failure_category is FailureCategory.SYSTEM_ERROR
    wrong = CountingSystem(wrong=True)
    out = run(runner(wrong, clock).run("r", TESTS[:2]))
    assert wrong.calls == 2 and all(r.outcome is Outcome.FAIL for r in out.results)


def test_retry_gives_up_after_max_attempts(clock: FakeClock) -> None:
    transient = SystemResponse.failure(ErrorCode.RATE_LIMITED, "429", retryable=True)
    system = CountingSystem({1: transient, 2: transient, 3: transient})
    out = run(
        runner(system, clock, settings=RunnerSettings(concurrency=1, max_attempts=3)).run(
            "r", TESTS[:1]
        )
    )
    assert (
        out.results[0].outcome is Outcome.ERROR
        and out.results[0].attempts == 3
        and system.calls == 3
    )


def test_timeout_becomes_error(clock: FakeClock) -> None:
    class Hanging:
        descriptor = SystemDescriptor(system_id="hang", kind=SystemKind.MOCK)

        async def execute(self, test: TestInput) -> SystemResponse:
            await asyncio.Event().wait()  # never completes
            raise AssertionError

    settings = RunnerSettings(concurrency=1, timeout_s=0.01, max_attempts=1)
    out = run(runner(Hanging(), clock, settings=settings).run("r", TESTS[:1]))
    assert out.results[0].outcome is Outcome.ERROR
    assert (
        out.results[0].error_code is ErrorCode.TIMEOUT
        and out.results[0].failure_category is FailureCategory.TIMEOUT
    )


def test_evaluator_crash_is_reported_not_fatal(clock: FakeClock) -> None:
    class Broken:
        name = "broken"
        version = "1"

        def validate(self, test_case: TestCase) -> None:
            return None

        async def evaluate(self, test_case: TestCase, response: SystemResponse) -> EvaluationResult:
            raise RuntimeError("bug")

    registry = EvaluatorRegistry()
    registry.register(Broken())
    case = make_case(ExpectedBehavior(), ("broken",))
    out = run(EvaluationRunner(CountingSystem(), registry, clock=clock).run("r", [case]))
    assert out.results[0].outcome is Outcome.ERROR
    assert "evaluator failure" in out.results[0].evaluations[0].reason


def test_resume_skips_completed_executions(tmp_path: Path, clock: FakeClock) -> None:
    journal = CheckpointJournal(tmp_path / "checkpoint.jsonl")
    first = run(runner(CountingSystem(), clock).run("r", TESTS[:4], journal=journal))
    assert len(journal.load()) == 4
    system = CountingSystem()
    out = run(runner(system, clock).run("r", TESTS, journal=journal))
    assert out.resumed == 4 and out.executed == 2 and system.calls == 2
    assert [r.test_id for r in out.results] == [t.test_id for t in TESTS]
    assert out.results[0] == first.results[0]


def test_journal_tolerates_torn_tail_but_not_corruption(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "checkpoint.jsonl"
    journal = CheckpointJournal(path)
    run(runner(CountingSystem(), clock).run("r", TESTS[:2], journal=journal))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"execution_id": "torn')
    assert len(journal.load()) == 2
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("garbage\n" + "\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ResultCorruption):
        journal.load()


def test_capture_policy_and_cost(clock: FakeClock) -> None:
    pricing = Pricing(input_per_1k_usd=0.5, output_per_1k_usd=1.0)
    failing = CountingSystem(wrong=True)
    out = run(
        runner(failing, clock, capture=CapturePolicy.METADATA_ONLY, pricing=pricing).run(
            "r", TESTS[:1]
        )
    )
    result = out.results[0]
    assert result.response_text is None and result.response_sha256
    assert result.cost_usd == pytest.approx(0.5 * 1 + 1.0 * 0.5)
    out = run(
        runner(CountingSystem(wrong=True), clock, capture=CapturePolicy.FAILED_CASES).run(
            "r", TESTS[:1]
        )
    )
    assert out.results[0].response_text == "nope"
    out = run(
        runner(CountingSystem(), clock, capture=CapturePolicy.FAILED_CASES).run("r", TESTS[:1])
    )
    assert out.results[0].response_text is None


def test_repetitions_produce_distinct_executions(clock: FakeClock) -> None:
    out = run(runner(CountingSystem(), clock).run("r", TESTS[:2], repetitions=3))
    assert len(out.results) == 6 and len({r.execution_id for r in out.results}) == 6


def test_cancellation_marks_run_interrupted(tmp_path: Path) -> None:
    clock = FakeClock()
    started = asyncio.Event()

    class Slow:
        descriptor = SystemDescriptor(system_id="slow", kind=SystemKind.MOCK)
        calls = 0

        async def execute(self, test: TestInput) -> SystemResponse:
            Slow.calls += 1
            if Slow.calls > 2:
                started.set()
                await asyncio.Event().wait()
            return SystemResponse(text="a" + test.prompt[1:])

    journal = CheckpointJournal(tmp_path / "c.jsonl")

    async def scenario() -> object:
        task = asyncio.create_task(
            runner(Slow(), clock, settings=RunnerSettings(concurrency=1)).run(
                "r", TESTS, journal=journal
            )
        )
        await started.wait()
        task.cancel()
        return await task

    out = run(scenario())
    assert out.interrupted and len(out.results) == 2  # type: ignore[attr-defined]
    assert len(journal.load()) == 2


def test_retry_policy_rules() -> None:
    policy = RetryPolicy(max_attempts=2)
    assert policy.should_retry(SystemResponse.failure(ErrorCode.NETWORK_ERROR, retryable=True), 1)
    assert not policy.should_retry(
        SystemResponse.failure(ErrorCode.NETWORK_ERROR, retryable=True), 2
    )
    assert not policy.should_retry(
        SystemResponse.failure(ErrorCode.INVALID_RESPONSE, retryable=True), 1
    )
    assert not policy.should_retry(SystemResponse(text="wrong answer"), 1)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


def test_rate_limiter_requests_per_second() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock, requests_per_second=2)

    async def burst() -> None:
        for _ in range(5):
            await limiter.acquire()

    run(burst())
    assert clock.monotonic() == pytest.approx(2.0)  # 5 requests at 2/s need two 1-second waits


def test_rate_limiter_tokens_per_minute() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock, tokens_per_minute=100)

    async def burst() -> None:
        for _ in range(3):
            await limiter.acquire(estimated_tokens=60)

    run(burst())
    assert clock.monotonic() == pytest.approx(120.0)
    assert not RateLimiter(clock).enabled
