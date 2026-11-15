"""Application service: execute a RunConfig end to end and persist an immutable run."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from ai_reliability import __version__
from ai_reliability.clock import Clock, SystemClock
from ai_reliability.config.loader import config_hash, dump_yaml, load_model
from ai_reliability.config.models import RunConfig
from ai_reliability.domain.base import sha256_hex
from ai_reliability.domain.enums import RunStatus
from ai_reliability.domain.gate import ReleaseDecision
from ai_reliability.domain.regression import ComparisonReport
from ai_reliability.domain.run import RunManifest, RunSummary
from ai_reliability.domain.test_case import TestSuite
from ai_reliability.errors import ConfigurationError, UnknownComponent
from ai_reliability.gates.config import load_gate_policy
from ai_reliability.gates.engine import evaluate_gates
from ai_reliability.metrics.aggregate import summarize
from ai_reliability.regression.compare import compare_runs
from ai_reliability.reporting.html import markdown_to_html
from ai_reliability.reporting.markdown import render_plots, render_release_report, render_report
from ai_reliability.runner.executor import EvaluationRunner, RunOutcome
from ai_reliability.runner.observer import RunObserver
from ai_reliability.runner.policies import RateLimiter
from ai_reliability.services.workspace import Workspace
from ai_reliability.storage.registry_db import RunRecord
from ai_reliability.systems.mock.knowledge import KnowledgeBase


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    manifest: RunManifest
    outcome: RunOutcome
    summary: RunSummary | None
    comparison: ComparisonReport | None
    decision: ReleaseDecision | None
    run_dir: Path


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def new_run_id(config: RunConfig, clock: Clock) -> str:
    # Timestamp for readability, random suffix for uniqueness (two runs may start in the
    # same second, e.g. with a fake clock or in CI). The config hash lives in the manifest.
    stamp = clock.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{_slug(config.system)}-{uuid.uuid4().hex[:8]}"


def select_suite(workspace: Workspace, config: RunConfig) -> TestSuite:
    suite = workspace.suites.resolve(config.suite)
    f = config.filters
    selected = suite.select(
        tags=f.tags, exclude_tags=f.exclude_tags, categories=f.categories, statuses=f.statuses
    )
    if not selected.tests:
        raise ConfigurationError(f"suite {config.suite!r} has no tests after filtering")
    return selected


def resolve_baseline(workspace: Workspace, ref: str, results_dir: Path) -> str:
    """A baseline ref is a run ID or ``baseline:<name>`` (latest declaration of that name)."""
    if ref.startswith("baseline:"):
        run_id = workspace.index(results_dir).current_baseline(ref.removeprefix("baseline:"))
        if run_id is None:
            raise UnknownComponent(f"no baseline declared under name {ref!r}")
        return run_id
    return ref


async def run_evaluation(
    workspace: Workspace,
    config: RunConfig,
    *,
    run_id: str | None = None,
    clock: Clock | None = None,
    observer: RunObserver | None = None,
) -> RunArtifacts:
    """Execute (or resume) a run and finalize its artifacts."""
    clock = clock or SystemClock()
    results_dir = workspace.resolve_path(config.output_dir)
    store, index = workspace.store(results_dir), workspace.index(results_dir)
    suite = select_suite(workspace, config)
    sys_config = workspace.systems.resolve(config.system)
    knowledge = (
        KnowledgeBase.from_tests(suite) if sys_config.type in {"mock", "synthetic"} else None
    )
    system = workspace.systems.build(sys_config, knowledge=knowledge, clock=clock)
    evaluators = workspace.evaluators()
    evaluators.validate_suite(suite)
    run_id = run_id or config.run_id or new_run_id(config, clock)

    used = sorted({name for t in suite for name in t.evaluators})
    created_at = store.load_manifest(run_id).created_at if store.exists(run_id) else clock.now()
    manifest = RunManifest(
        run_id=run_id,
        created_at=created_at,
        tool_version=__version__,
        git_sha=workspace.git_sha,
        system=system.descriptor,
        suite_id=suite.suite_id,
        suite_version=suite.version,
        test_count=len(suite),
        test_ids_hash=sha256_hex("\n".join(f"{t.test_id}:{t.definition_hash()}" for t in suite))[
            :16
        ],
        evaluator_versions=evaluators.versions(used),
        config_hash=config_hash(config),
        seed=config.seed,
        repetitions=config.repetitions,
        capture_policy=config.capture,
        baseline_run_id=None,
        labels=config.labels,
    )
    run_dir = store.create(manifest, dump_yaml(config.model_copy(update={"run_id": run_id})))
    record = RunRecord(
        run_id=run_id,
        created_at=manifest.created_at.isoformat(),
        completed_at=None,
        status=RunStatus.RUNNING,
        system_id=manifest.system.system_id,
        suite_id=suite.suite_id,
        suite_version=suite.version,
        config_hash=manifest.config_hash,
        git_sha=manifest.git_sha,
        pass_rate=None,
        decision=None,
    )
    index.upsert_run(record)

    rate = sys_config.rate_limit
    runner = EvaluationRunner(
        system,
        evaluators,
        settings=config.runner,
        clock=clock,
        observer=observer,
        rate_limiter=RateLimiter(
            clock,
            requests_per_second=rate.requests_per_second if rate else None,
            tokens_per_minute=rate.tokens_per_minute if rate else None,
        ),
        pricing=sys_config.pricing,
        capture=config.capture,
    )
    try:
        outcome = await runner.run(
            run_id, suite.tests, repetitions=config.repetitions, journal=store.journal(run_id)
        )
    finally:
        close = getattr(system, "aclose", None)
        if close is not None:
            await close()
    if outcome.interrupted:
        store.mark_interrupted(manifest)
        index.upsert_run(record.model_copy(update={"status": RunStatus.INTERRUPTED}))
        return RunArtifacts(manifest, outcome, None, None, None, run_dir)

    summary = summarize(run_id, outcome.results)
    comparison = None
    if config.baseline:
        baseline_id = resolve_baseline(workspace, config.baseline, results_dir)
        comparison = compare_runs(
            baseline_id,
            store.load_results(baseline_id),
            run_id,
            outcome.results,
            baseline_summary=store.load_summary(baseline_id),
            candidate_summary=summary,
            seed=config.seed,
        )
        manifest = manifest.model_copy(update={"baseline_run_id": baseline_id})
    decision = None
    if config.gates:
        decision = evaluate_gates(
            load_gate_policy(workspace.resolve_path(config.gates)), summary, comparison
        )
    manifest = manifest.model_copy(update={"completed_at": clock.now()})
    plots = render_plots(summary, outcome.results, comparison)
    report = render_report(
        manifest,
        summary,
        outcome.results,
        comparison=comparison,
        decision=decision,
        plots=sorted(plots),
    )
    documents = {"report.md": report, "report.html": markdown_to_html(report, f"Report {run_id}")}
    if decision is not None:
        documents["release-report.md"] = render_release_report(
            manifest, summary, decision, comparison
        )
    store.finalize(
        manifest,
        outcome.results,
        summary,
        comparison=comparison,
        decision=decision,
        plots=plots,
        documents=documents,
    )
    index.upsert_run(
        record.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "completed_at": manifest.completed_at.isoformat()
                if manifest.completed_at
                else None,
                "pass_rate": summary.metric("pass_rate"),
                "decision": decision.decision.value if decision else None,
            }
        )
    )
    return RunArtifacts(manifest, outcome, summary, comparison, decision, run_dir)


async def resume_evaluation(
    workspace: Workspace,
    run_id: str,
    *,
    results_dir: Path | None = None,
    clock: Clock | None = None,
    observer: RunObserver | None = None,
) -> RunArtifacts:
    """Resume an interrupted run from its stored config and checkpoint journal."""
    store = workspace.store(results_dir)
    manifest = store.load_manifest(run_id)
    if manifest.status is RunStatus.COMPLETED:
        raise ConfigurationError(f"run {run_id} is already completed")
    config = load_model(store.run_dir(run_id) / "config.yaml", RunConfig)
    if results_dir is not None:
        config = config.model_copy(update={"output_dir": str(results_dir)})
    return await run_evaluation(workspace, config, run_id=run_id, clock=clock, observer=observer)
