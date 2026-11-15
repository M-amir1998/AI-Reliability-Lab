# Runner

`runner/executor.py` (`EvaluationRunner`) executes test cases against a `SystemUnderTest`,
evaluates every response and produces one `CaseResult` per (test, repetition).

## Settings

`RunnerSettings` (`runner:` in a `RunConfig`; defaults shown):

| Key | Default | Meaning |
|---|---|---|
| `concurrency` | 8 (1–256) | Maximum executions in flight (`asyncio.Semaphore`) |
| `timeout_s` | 60 | Per-attempt runner timeout around `system.execute()` |
| `max_attempts` | 3 (1–10) | Attempts per execution, including the first |
| `backoff_initial_s` | 0.5 | First backoff step |
| `backoff_max_s` | 8.0 | Backoff cap |
| `evaluator_timeout_s` | 30 | Per-evaluator timeout; effective only for evaluators that await (e.g. the LLM judge) — synchronous built-in checks are not interrupted |

Run-level keys: `repetitions` (1–100), `seed`, `capture`, `filters`, `gates`, `baseline`,
`output_dir`, `labels`.

## Execution plan

1. Every test is validated against its evaluators (`validate_test`).
2. The plan is `tests x range(repetitions)`. Each item gets a deterministic id:
   `execution_id = sha256("run_id|test_id|definition_hash|repetition")[:24]`.
3. Executions already in the checkpoint journal (and in the plan) are skipped ("resumed").
4. Pending executions run in an `asyncio.TaskGroup` under the semaphore.
5. Results are returned in plan order.

## Timeouts

There are two independent timeouts:

* `RunnerSettings.timeout_s` wraps `system.execute()` in `asyncio.wait_for`. Expiry produces
  `ErrorCode.TIMEOUT` with `retryable=True`.
* `SystemConfig.timeout_s` is passed to httpx by endpoint adapters. An httpx timeout is also a
  retryable `timeout`.

Simulated mock timeouts (`MockBehavior.TIMEOUT`) are *not* retryable. If the system reports no
latency, the runner records measured wall time of the last attempt as `e2e_ms`.

## Retry policy (transport only)

`RetryPolicy.should_retry` retries only when the response has an error that the adapter marked
`retryable` **and** whose code is one of `timeout`, `network_error`, `rate_limited`,
`http_error`, and fewer than `max_attempts` attempts were made. With the shipped HTTP transport
that means 429, 408, 425, 502, 503, 504, httpx timeouts and transport errors.

Never retried: wrong answers, policy violations, `invalid_response`, non-transient HTTP errors
(e.g. 500) and adapter exceptions (`system_error`). Retrying those would hide real failures.

Backoff: `min(backoff_max_s, backoff_initial_s * 2^(attempt-1)) * (0.5 + 0.5 * u)` where `u`
is a deterministic value from `stable_unit(test_id:definition_hash, attempt)`. The number of
attempts is stored in `CaseResult.attempts` and aggregated as `retry_rate`.

## Rate limiting

`RateLimiter` is a sliding-window limiter configured from `SystemConfig.rate_limit`
(`requests_per_second`, `tokens_per_minute`). It is disabled when neither is set. Token cost is
estimated *before* the call as input characters // 4; this estimate is used only for pacing and
never reported as usage.

## Outcome of one execution

* Transport/system error -> `Outcome.ERROR`, failure category `timeout` or `system_error`; no
  evaluator runs.
* Otherwise all listed evaluators run sequentially; outcomes are combined with the composite
  `all` rule (ERROR dominates, NEEDS_REVIEW next, all-skipped stays SKIPPED).
* `failure_category` / `failure_reason` come from the most severe failing evaluation, using
  priority: `safety_failure` > `tool_failure` > `citation_failure` > `hallucination` >
  `retrieval_failure` > `instruction_failure` > `format_failure` > `wrong_answer` > `performance`.
* If `pricing` is configured and usage is reported, `cost_usd` is computed from it.

## Capture policy

`capture` (`CapturePolicy`) decides whether response text is stored in `CaseResult.response_text`:

| Policy | Response text stored |
|---|---|
| `metadata_only` | never |
| `failed_cases` (default) | for every non-PASS execution |
| `all` | always |

Stored text is passed through `Redactor` and truncated to 4000 characters. Input/prompt text is
never stored; only `input_sha256` (canonical input JSON) and `response_sha256` are kept (both
truncated to 32 hex chars).

## Checkpoint journal and resume

`CheckpointJournal` (`runner/checkpoint.py`) appends each completed `CaseResult` as one JSON
line to `results/<run-id>/checkpoint.jsonl` and flushes immediately. On load, a torn final line
(crash mid-write) is ignored; corruption on any other line raises `ResultCorruption`.

`aireliability resume RUN_ID` reloads the stored `config.yaml`, keeps the original
`created_at`, and executes only the missing executions. Because execution ids include the
`definition_hash`, a test whose definition changed is executed again rather than reused. The
journal is deleted when the run is finalized.

## Cancellation

If cancellation reaches the runner's `TaskGroup`, in-flight tasks are cancelled, the runner
returns `interrupted=True` with all results completed so far (they are already journaled), and
`run_evaluation` writes the manifest with status `INTERRUPTED` and updates the run index. No
summary, comparison or gate decision is produced for an interrupted run.

## Observers

`RunObserver` receives `on_run_started`, `on_case_started`, `on_case_finished` and
`on_run_finished`. Implementations must be cheap and must not raise. Provided:

* `NullObserver` (default) and `FanOutObserver`;
* the CLI progress observer;
* `PrometheusObserver` (`--metrics-port` on `aireliability run`): counters for completed and
  failed executions, endpoint errors, tokens and judge calls; gauges for in-flight executions
  and run duration; a latency histogram. Requires the `observability` extra;
* `OTelObserver`: one `evaluation_run` span and one `evaluate_case` span per execution, with
  metadata-only attributes (ids, category, outcome, latency, attempts).

These observe the evaluation infrastructure, not the model.
