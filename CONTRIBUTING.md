# Contributing

Thanks for helping improve AI Reliability Lab. This guide covers the development setup, the
checks CI runs, and the rules for evaluators, adapters, suites and red-team families.
Architecture background: [docs/README.md](docs/README.md).

## Development setup

Requirements: Python 3.11+ (`.python-version` pins 3.12), [uv](https://docs.astral.sh/uv/), git.
No GPU, model API or network service is needed for development or tests.

```bash
uv sync --all-extras          # runtime deps, observability extra, and the dev dependency group
uv run aireliability doctor   # environment check; a missing GPU is expected
uv run aireliability demo release-gate --output demo-output   # offline smoke test
```

Use `uv sync --frozen --all-extras` to reproduce CI exactly. If you change dependencies, update
`uv.lock` (`uv lock`) and commit it; CI runs `uv lock --check`.

## Checks

```bash
uv run ruff format src tests          # CI: ruff format --check src tests
uv run ruff check src tests
uv run mypy                           # strict mode, pydantic plugin
uv run pytest tests/unit
uv run pytest tests/property          # Hypothesis
uv run pytest tests/integration       # multi-component, mock systems
uv run pytest tests/security          # red-team and privacy
uv run pytest tests/e2e               # CLI and demo flows
uv run pytest tests/benchmarks        # CPU overhead (not LLM inference)
uv run pytest tests/unit --cov        # coverage (branch)
```

CI selects tests by directory. Markers are registered in `pyproject.toml` (`property`,
`integration`, `e2e`, `security`, `benchmark`) and pytest runs with `--strict-markers`, so a
misspelled marker is an error; `-m <marker>` selects only tests that carry the marker
explicitly. Tests must be offline and deterministic: use `FakeClock`, mock/scripted systems and
`httpx.MockTransport`, never real endpoints or real sleeps.

Ruff enforces, among others, bandit-style `S` rules and a banned-API rule for `pickle`.

## Evaluators

### Contract tests (required)

Every evaluator, new or changed, needs tests covering at least:

| Case | Expectation |
|---|---|
| **Positive** | a correct response passes (and has the expected `score`) |
| **Negative** | an incorrect response fails with the intended `failure_category` and a useful `reason` |
| **Malformed** | empty text, non-JSON, truncated JSON, wrong types, unexpected tool-call shapes: the evaluator returns a verdict (FAIL or SKIPPED) and never raises |
| **Edge** | boundaries (exactly at a threshold), unicode/whitespace, empty collections, missing optional fields; and `validate()` raising `InvalidTestCase` when required expectation fields are absent |

### Versioning rule

`name` identifies an evaluator; `version` identifies its pass criteria. **Any change to what
the evaluator considers a pass — thresholds, regexes, normalization, parsing — must bump
`version`.** Run manifests record evaluator versions; without a bump, historical results become
silently incomparable. Pure refactors that provably keep every verdict keep the version.
Composite versions include component versions automatically.

### Adding an evaluator

1. Subclass `BaseEvaluator` (or implement the `Evaluator` protocol); set `name`, `version`,
   `failure_category`; implement `validate()` and `check()`.
2. Never execute response content; parse with `json.loads` / regex. Keep `details` small and
   free of secrets and full prompts.
3. Register it in `default_evaluator_registry()` (`evaluators/registry.py`). Add it to
   `EVALUATOR_GROUPS` in `metrics/aggregate.py` only if it should feed a run metric.
4. If incidents should select it, extend `infer_evaluators()` in `incidents/pipeline.py`.
5. Document it in `docs/architecture/evaluators.md`, including its limits.

LLM-judge evaluators are never registered by default (ADR-001).

## Adding a system adapter

1. Implement `SystemUnderTest` (`descriptor`, `async execute()`); add `aclose()` if you hold an
   HTTP client.
2. Never raise for transport problems: return `SystemResponse.failure(...)` with the right
   `ErrorCode` and `retryable` flag. Reuse `post_json()` (classification), `auth_headers()`
   (env-var credentials) and `validate_endpoint()` (SSRF policy) from `systems/http/`.
3. Parse responses with a lenient pydantic model into the canonical `SystemResponse`; report
   tokens only if the system reports them.
4. Wire it up: add the type to `SystemType` and the `_type_fields` validator in
   `config/models.py`, to `_KIND` and `SystemRegistry.build()` in `systems/registry.py`.
5. Add an example config under `configs/systems/` with placeholder values and `api_key_env`
   (never a key), and tests using `httpx.MockTransport`.
6. Document the request/response contract in `docs/architecture/system-adapters.md`.

## Adding a suite

* YAML: add `configs/suites/<name>.yaml` (`suite_id`, `version`, `tier`, optional `include` /
  `include_tags`, `tests`). It is registered automatically.
* Python: add tests to `testsuites/builtin.py` or a composed suite in `testsuites/registry.py`.
* Test ids must match `^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$` and be unique per suite.
* Every test needs at least one evaluator and the expectation fields that evaluator validates.
* Choose `severity` deliberately: CRITICAL failures block releases on their own.
* Bump the suite `version` when tests change. Changing a test's input, expectation or evaluators
  changes its definition hash and makes it incomparable with older runs.

## Adding a red-team family

1. Write a generator `def family(seed: int, canary: str) -> tuple[TestCase, ...]` in
   `redteam/<area>/fixtures.py` using the `case()` builder.
2. Use `protected_system_prompt(canary)` for secrets and `make_marker(family, index, seed)` for
   compliance markers; never use real secrets, real people or real external targets.
3. Tag tests `redteam` and `family:<name>`, set `source=TestSource.REDTEAM` and an explicit
   severity; ids must be stable for `seed=0`.
4. Register it in `FAMILIES` (and `PROMPT_INJECTION_FAMILIES` if it is an injection family) in
   `redteam/catalog.py`.
5. Add security tests: `synthetic/good` resists it; where applicable `synthetic/unsafe` does not.
6. Document it in `docs/methodology/prompt-injection.md`.

## Data that must never be committed

* Real incidents, traces, review labels or production prompts/responses (`incidents/`,
  `traces/`, `reviews/` are git-ignored).
* Evaluation artifacts (`results/`, `reports/`, `*.parquet`, `*.sqlite`, `demo-output/`).
* API keys, tokens, `.env` files, private keys.

Examples under `examples/` must be clearly synthetic and say so in a comment. If you commit
something sensitive by mistake, do not just delete it in a follow-up commit; report it (see
[SECURITY.md](SECURITY.md)) so the history and any exposed credential can be dealt with.

## Commit hygiene

* Stage files explicitly (`git add path/to/file`); avoid `git add -A` / `git add .`. Review
  `git diff --staged` before committing.
* Use conventional commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`,
  `build:`, `ci:`, `chore:`. One logical change per commit.
* Do not bypass hooks or checks (`--no-verify`).
* Never claim results for a real model in docs or commit messages; real-model evaluations stay
  **Pending** until they are actually run and published.

## CI

CI runs on Jenkins (`Jenkinsfile`); there are no GitHub Actions workflows. A change is ready when
the default offline pipeline passes: formatting, ruff, strict mypy, unit/property/integration/
security tests, the mock end-to-end runs and gates, secret scan and package build. See
[docs/ci/jenkins.md](docs/ci/jenkins.md).
