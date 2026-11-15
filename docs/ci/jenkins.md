# Jenkins CI

Jenkins is the project's CI system; there are no GitHub Actions workflows
([ADR-007](../adr/ADR-007-jenkins-instead-of-github-actions.md)). The pipeline is defined in the
repository's `Jenkinsfile` (declarative). The default build is **offline and CPU-only**: every
evaluation uses deterministic mock or scripted systems, and no GPU, model API, paid judge, cloud
service or external database is needed.

## 1. Agent prerequisites (Linux)

| Requirement | Why |
|---|---|
| Linux agent with `sh`, `git`, `xargs`, `rm` | every step is an `sh` step |
| [uv](https://docs.astral.sh/uv/) on the `PATH` of the Jenkins user | all Python commands run through `uv` |
| Python 3.11+ (or let uv download one) | `requires-python >= 3.11`; `.python-version` pins 3.12 |
| Docker (optional) | `Docker Build` stage runs only if `docker info` succeeds |
| ~8 GB RAM, a few GB free disk | checked (as warnings) by `aireliability doctor` |

Install Jenkins (LTS) and a supported Java runtime following the official instructions for your
distribution at <https://www.jenkins.io/doc/book/installing/linux/>.

Install uv for the user that runs builds (the `jenkins` user on a package install):

```bash
sudo -u jenkins -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# installs to ~jenkins/.local/bin; make it visible to builds, e.g.:
sudo ln -s /var/lib/jenkins/.local/bin/uv /usr/local/bin/uv     # adjust to the jenkins home
sudo -u jenkins -H uv --version
```

Alternatively add the directory to `PATH` in *Manage Jenkins -> Nodes -> (node) -> Environment
variables*.

### Plugins

| Plugin | Used by |
|---|---|
| Pipeline (workflow-aggregator) | declarative `pipeline {}` |
| Git | `checkout scm` |
| GitHub Branch Source (multibranch) or GitHub (single job) | repository source and webhook trigger |
| Credentials Binding | `withCredentials([string(...)])` in the external stage |
| JUnit | `junit 'build/junit-*.xml'` |
| Timestamper | `options { timestamps() }` |

## 2. Job setup

**Multibranch Pipeline (recommended).** *New Item -> Multibranch Pipeline*; Branch Sources ->
GitHub; repository URL; a GitHub credential (GitHub App or a fine-grained token with read access)
if the repository is private; Build Configuration -> *by Jenkinsfile*, Script Path `Jenkinsfile`.
Each branch and pull request gets its own job.

**Single Pipeline job.** *New Item -> Pipeline*; Definition *Pipeline script from SCM*; SCM Git;
branch `*/main`; Script Path `Jenkinsfile`; enable *GitHub hook trigger for GITScm polling*.

Declarative `parameters` are registered by the first build; run the job once before using
*Build with Parameters*.

### GitHub webhook

Repository *Settings -> Webhooks -> Add webhook*:

* Payload URL: `https://<your-jenkins-host>/github-webhook/`
* Content type: `application/json`
* Events: *push* and *pull requests* (multibranch) or *push* (single job)

GitHub must be able to reach the URL. If Jenkins runs on a laptop behind NAT, either use a
webhook relay you trust or skip the webhook and configure periodic scanning (multibranch:
*Scan Multibranch Pipeline Triggers*) or SCM polling.

## 3. Default pipeline

Options: `disableConcurrentBuilds()`, `timeout(45 minutes)`, `timestamps()`,
`buildDiscarder(logRotator(numToKeepStr: '20', artifactNumToKeepStr: '10'))`.

Environment: `UV_NO_PROGRESS=1`, `PYTHONDONTWRITEBYTECODE=1`,
`PYTEST_ADDOPTS=-p no:cacheprovider`, and `OPENAI_API_KEY=''`, `ANTHROPIC_API_KEY=''` to
guarantee offline behaviour.

| # | Stage | Commands |
|---|---|---|
| 1 | Checkout | `checkout scm`; `git log -1 --format="%h %an %s"` |
| 2 | Environment Validation | `uv --version`; `uv sync --frozen --all-extras`; `uv run aireliability doctor` |
| 3 | Dependency Lock Check | `uv lock --check` |
| 4 | Formatting | `uv run ruff format --check src tests` |
| 5 | Ruff | `uv run ruff check src tests` |
| 6 | Strict mypy | `uv run mypy` |
| 7 | Unit Tests | `uv run pytest tests/unit -q --junitxml=build/junit-unit.xml --cov --cov-report=xml:build/coverage.xml` |
| 8 | Property Tests | `uv run pytest tests/property -q --junitxml=build/junit-property.xml` |
| 9 | Integration Tests | `uv run pytest tests/integration -q --junitxml=build/junit-integration.xml` |
| 10 | Security Evaluation Tests | `uv run pytest tests/security -q --junitxml=build/junit-security.xml` |
| 11 | Mock Model E2E | `uv run aireliability run configs/runs/ci-core.yaml --root . --fail-on-gate`; `uv run pytest tests/e2e -q -k "cli or mock" --junitxml=build/junit-e2e-mock.xml` |
| 12 | Regression Gate E2E | `uv run pytest tests/e2e -q -k release_gate --junitxml=build/junit-e2e-gate.xml` — the demo candidate must be rejected |
| 13 | Incident-to-Eval E2E | `uv run pytest tests/e2e -q -k incident --junitxml=build/junit-e2e-incident.xml` |
| 14 | Secret Scan | `git ls-files -z \| xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline` |
| 15 | Package Build | `rm -rf dist && uv build`; `ls -l dist` |
| 16 | Docker Build | only if `docker info` succeeds: `docker build -t ai-reliability-lab:$BUILD_NUMBER .`; `docker run --rm ai-reliability-lab:$BUILD_NUMBER --version` |
| 17 | External Evaluation (optional) | only if `RUN_EXTERNAL` is true — see below |

Stage 11 runs `synthetic/good` on `ci_core` with `configs/gates/ci.yaml` (pass rate 1.0,
prompt-injection pass rate 1.0, system error rate 0, no critical failures); `--fail-on-gate`
makes a FAIL decision exit with code 2 and fail the build.

The Secret Scan stage requires a committed, audited `.secrets.baseline` (create it with
`uv run detect-secrets scan > .secrets.baseline` and review it with `detect-secrets audit`). The
stage fails if the file is missing.

## 4. Optional external evaluation

Parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `RUN_EXTERNAL` | `false` | enable stage 17 |
| `SYSTEM_CONFIG` | `configs/systems/openai-compatible-example.yaml` | system to evaluate |
| `SUITE` | `ci_core` | suite to run |
| `BASELINE_RUN` | empty | run id or `baseline:<name>` to compare against |
| `EXTERNAL_CREDENTIALS_ID` | `llm-provider-api-key` | Jenkins credential id (kind *Secret text*) |
| `EXTERNAL_API_KEY_ENV` | `OPENAI_API_KEY` | variable name the system config reads (`api_key_env`) |

Setup:

1. *Manage Jenkins -> Credentials -> (store) -> Add Credentials*, kind **Secret text**, ID
   `llm-provider-api-key` (or your own id passed as `EXTERNAL_CREDENTIALS_ID`).
2. Edit the system config: real `model`, `revision`, `endpoint`; `api_key_env` must equal
   `EXTERNAL_API_KEY_ENV`. Never put key values in configs.
3. *Build with Parameters* with `RUN_EXTERNAL` checked.

The stage binds the secret with `withCredentials`, runs with `set +x`, exports it under
`EXTERNAL_API_KEY_ENV`, writes `build/external-run.yaml` (`system`, `suite`, `repetitions: 3`,
`gates: configs/gates/release.yaml`, `output_dir: results`, labels with the build number, plus
`baseline` if given) and runs `aireliability run build/external-run.yaml --root . --fail-on-gate`.
The `cleanup` post-step deletes the generated config.

Notes:

* Without `BASELINE_RUN`, the release policy's `regression` gate cannot be evaluated and yields
  a WARNING (default `on_missing: warn`); the build fails only on a FAIL decision.
* The default `SUITE=ci_core` contains no `jailbreak` tests, so the release policy's
  `jailbreak` gate is also reported as missing (WARNING). Use `release_regression` to cover
  every gated category.
* Parameters are interpolated into a shell script. Restrict *Build with Parameters* to trusted
  users.
* The Jenkins workspace is not cleaned between builds, so `results/` (including
  `results/registry.sqlite` and its baseline declarations) persists on the agent. Wiping the
  workspace removes local baselines.
* Real-model results produced this way are not part of the repository; their status in the docs
  remains **Pending** until published deliberately.

## 5. Artifacts and retention

`post { always { ... } }`:

* `junit allowEmptyResults: true, testResults: 'build/junit-*.xml'`
* `archiveArtifacts` (fingerprinted): `results/*/report.md`, `results/*/release-report.md`,
  `results/*/summary.json`, `results/*/gates.json`, `results/*/manifest.json`,
  `results/*/plots/*.svg`, `dist/*`

Not archived, deliberately: `results.parquet` / `failures.parquet` / `checkpoint.jsonl` (may
contain captured model response text), `regressions.parquet`, `comparison.json`, `report.html`,
`config.yaml`, `checksums.json`, `registry.sqlite`, and anything under `incidents/`, `traces/`
or `reviews/`. Archived reports and summaries carry metrics, gate reasons and failure reasons,
not raw prompts or traces. Because `checksums.json` is not archived, archived files cannot be
verified with `aireliability runs verify`. As the workspace persists, each build archives every
run directory still present under `results/`.

Retention: logs of the last 20 builds, artifacts of the last 10.

## 6. Why `disableConcurrentBuilds`

The reference CI host is a 16 GB laptop. One build creates a virtualenv, runs strict mypy, the
test suites and several CLI runs, and may build a Docker image; two concurrent builds compete for
RAM and CPU and make timing-sensitive checks noisy. Concurrent builds would also share the
persistent `results/` directory and its SQLite index. Builds therefore queue instead of running
in parallel; the 45-minute timeout bounds a stuck build.

## 7. Windows agents

The Python package runs on Windows, but the `Jenkinsfile` uses `sh` steps with POSIX features
(`xargs -0`, heredocs, `command -v`, `rm -rf`, `set +x`). Use a Linux agent, or a Linux agent
inside WSL2 on a Windows host. A native Windows agent would need a POSIX `sh` (e.g. Git Bash)
configured for Jenkins and is not supported by this pipeline; the Docker stage is skipped
automatically when no Docker daemon is reachable.

## 8. Reproducing CI locally

```bash
uv sync --frozen --all-extras
uv lock --check
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy
uv run pytest tests/unit tests/property tests/integration tests/security
uv run aireliability run configs/runs/ci-core.yaml --root . --fail-on-gate
uv run pytest tests/e2e
```
