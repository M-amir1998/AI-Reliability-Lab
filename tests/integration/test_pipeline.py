"""Suite -> mock system -> evaluators -> results -> metrics -> gates -> report, with real storage."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_reliability.clock import FakeClock
from ai_reliability.config.models import RunConfig, SystemConfig
from ai_reliability.domain.enums import CapturePolicy, GateDecision, RegressionStatus, RunStatus
from ai_reliability.errors import ConfigurationError
from ai_reliability.services.evaluation import resume_evaluation, run_evaluation
from ai_reliability.services.workspace import Workspace
from ai_reliability.storage.parquet import read_results
from ai_reliability.systems.mock.profile import BehaviorRule, MockBehavior, MockProfile
from tests.conftest import REPO_ROOT, run

pytestmark = pytest.mark.integration


def _policy(tmp_path: Path) -> Path:
    path = tmp_path / "gates.yaml"
    path.write_text(
        (REPO_ROOT / "configs" / "gates" / "release.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return path


def test_full_pipeline_produces_complete_immutable_run(
    workspace: Workspace, tmp_path: Path
) -> None:
    config = RunConfig(
        system="synthetic/good",
        suite="ci_core",
        gates=str(_policy(tmp_path)),
        capture=CapturePolicy.ALL,
    )
    art = run(run_evaluation(workspace, config, clock=FakeClock()))
    assert art.summary is not None and art.decision is not None
    assert art.summary.metric("pass_rate") == 1.0
    assert (
        art.decision.decision is GateDecision.WARNING
    )  # regression gate cannot be evaluated without baseline
    files = {p.name for p in art.run_dir.iterdir()}
    assert {
        "manifest.json",
        "config.yaml",
        "results.parquet",
        "failures.parquet",
        "summary.json",
        "gates.json",
        "report.md",
        "report.html",
        "release-report.md",
        "checksums.json",
        "plots",
    } <= files
    assert "checkpoint.jsonl" not in files
    store = workspace.store()
    store.verify(art.manifest.run_id)
    manifest = store.load_manifest(art.manifest.run_id)
    assert manifest.status is RunStatus.COMPLETED and manifest.test_count == len(
        read_results(art.run_dir / "results.parquet")
    )
    assert (
        manifest.evaluator_versions
        and manifest.config_hash
        and manifest.system.system_id == "synthetic/good"
    )
    assert workspace.index().get_run(art.manifest.run_id).status is RunStatus.COMPLETED  # type: ignore[union-attr]


def test_regression_blocks_release(workspace: Workspace, tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    base = run(
        run_evaluation(
            workspace,
            RunConfig(system="synthetic/good", suite="release_regression"),
            clock=FakeClock(),
        )
    )
    workspace.index().declare_baseline("main", base.manifest.run_id)
    workspace.systems.register(
        SystemConfig(
            system_id="regressing",
            type="mock",
            mock=MockProfile(
                rules=(
                    BehaviorRule(
                        behavior=MockBehavior.INJECTION_COMPLIANT, categories=("prompt_injection",)
                    ),
                )
            ),
        )
    )
    cand = run(
        run_evaluation(
            workspace,
            RunConfig(
                system="regressing",
                suite="release_regression",
                gates=str(policy),
                baseline="baseline:main",
            ),
            clock=FakeClock(),
        )
    )
    assert cand.comparison is not None and cand.decision is not None
    assert cand.comparison.counts[RegressionStatus.REGRESSED] > 0
    assert set(cand.comparison.new_failures) <= {
        t.test_id for t in workspace.suites.get("prompt_injection")
    }
    assert cand.decision.decision is GateDecision.FAIL
    gate = {g.gate_id: g for g in cand.decision.gates}
    assert gate["prompt_injection"].outcome.value == "fail"
    assert (cand.run_dir / "regressions.parquet").exists()


def test_interrupted_run_resumes_without_repeating(workspace: Workspace) -> None:
    calls: list[str] = []
    main: dict[str, asyncio.Task[object]] = {}
    original_build = workspace.systems.build

    def counting_build(*args, **kwargs):  # type: ignore[no-untyped-def]
        system = original_build(*args, **kwargs)
        inner = system.execute

        async def execute(test):  # type: ignore[no-untyped-def]
            calls.append(test.prompt)
            if len(calls) == 10 and "task" in main:
                main.pop("task").cancel()  # what asyncio.run does on Ctrl-C
                await asyncio.sleep(0)
            return await inner(test)

        system.execute = execute  # type: ignore[method-assign]
        return system

    workspace.systems.build = counting_build  # type: ignore[method-assign]
    config = RunConfig(
        system="synthetic/good", suite="ci_core", runner={"concurrency": 1}, run_id="resume-me"
    )  # type: ignore[arg-type]

    async def first_attempt():  # type: ignore[no-untyped-def]
        main["task"] = asyncio.current_task()  # type: ignore[assignment]
        return await run_evaluation(workspace, config, clock=FakeClock())

    first = run(first_attempt())
    assert first.summary is None and first.outcome.interrupted
    assert workspace.store().load_manifest("resume-me").status is RunStatus.INTERRUPTED
    done = len(first.outcome.results)
    assert 0 < done < len(workspace.suites.get("ci_core"))
    calls.clear()
    second = run(resume_evaluation(workspace, "resume-me", clock=FakeClock()))
    assert second.summary is not None and second.outcome.resumed == done
    assert len(calls) == len(workspace.suites.get("ci_core")) - done
    workspace.store().verify("resume-me")


def test_empty_selection_is_a_configuration_error(workspace: Workspace) -> None:
    config = RunConfig(system="synthetic/good", suite="smoke", filters={"tags": ["no-such-tag"]})  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        run(run_evaluation(workspace, config, clock=FakeClock()))


def test_flaky_system_passes_through_retries(workspace: Workspace) -> None:
    config = RunConfig(
        system="synthetic/flaky",
        suite="ci_core",
        runner={"backoff_initial_s": 0.01, "max_attempts": 3},
    )  # type: ignore[arg-type]
    art = run(run_evaluation(workspace, config, clock=FakeClock()))
    assert art.summary is not None and art.summary.metric("pass_rate") == 1.0
    assert (art.summary.metric("retry_rate") or 0) > 0


def test_slow_system_trips_latency_gate(workspace: Workspace, tmp_path: Path) -> None:
    art = run(
        run_evaluation(
            workspace,
            RunConfig(system="synthetic/slow", suite="ci_core", gates=str(_policy(tmp_path))),
            clock=FakeClock(),
        )
    )
    assert art.decision is not None
    latency = next(g for g in art.decision.gates if g.gate_id == "latency")
    assert latency.outcome.value == "warning" and (latency.observed or 0) > 3000
