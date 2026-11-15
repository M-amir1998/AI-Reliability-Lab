"""CPU end-to-end tests through the real CLI (Typer runner) and demos. Offline, mock systems only.

Test names are selected by Jenkins stages: ``-k "cli or mock"``, ``-k release_gate``, ``-k incident``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_reliability.cli.main import app
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.e2e
cli = CliRunner()


def invoke(*args: str) -> str:
    result = cli.invoke(app, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(REPO_ROOT / "configs", tmp_path / "configs")
    shutil.copytree(REPO_ROOT / "examples", tmp_path / "examples")
    return tmp_path


def test_cli_doctor_and_listing(project: Path) -> None:
    assert "not required" in invoke("doctor", "--root", str(project))
    assert "synthetic/good" in invoke("systems", "list", "--root", str(project))
    assert "release_regression" in invoke("suites", "list", "--root", str(project))
    assert "prompt_injection" in invoke(
        "suites", "inspect", "ci_core", "--root", str(project), "--tests"
    )
    assert "demo-baseline" in invoke("systems", "inspect", "demo-baseline", "--root", str(project))
    assert invoke("--version").strip()


def test_cli_mock_run_compare_gates_report(project: Path) -> None:
    out = invoke("run", "configs/runs/ci-core.yaml", "--root", str(project), "--fail-on-gate")
    assert "Release decision: PASS" in out
    candidate_cfg = project / "configs" / "runs" / "candidate.yaml"
    candidate_cfg.write_text(
        "system: synthetic/buggy\nsuite: ci_core\noutput_dir: results\n", encoding="utf-8"
    )
    invoke("run", "configs/runs/candidate.yaml", "--root", str(project))
    runs = sorted(p.name for p in (project / "results").iterdir() if (p / "manifest.json").exists())
    assert len(runs) == 2
    good = next(r for r in runs if "good" in r)
    buggy = next(r for r in runs if "buggy" in r)
    invoke("baseline", "set", good, "--name", "main", "--root", str(project))
    assert good in invoke("baseline", "show", "main", "--root", str(project))
    out = invoke(
        "compare", good, buggy, "--root", str(project), "--gates", "configs/gates/release.yaml"
    )
    assert "new failures" in out and "Release decision: FAIL" in out
    gate = cli.invoke(
        app,
        [
            "gates",
            "check",
            buggy,
            "--gates",
            "configs/gates/release.yaml",
            "--baseline",
            "baseline:main",
            "--root",
            str(project),
        ],
    )
    assert gate.exit_code == 2
    invoke("report", buggy, "--root", str(project))
    report = (project / "reports" / buggy / "report.md").read_text(encoding="utf-8")
    assert "SYNTHETIC / MOCK SYSTEM RESULTS" in report and "## Failures" in report
    invoke("runs", "verify", good, "--root", str(project))
    assert "pass" in invoke("runs", "list", "--root", str(project))
    assert "aireliability_run_pass_rate" in invoke("metrics", "export", "--root", str(project))


def test_cli_redteam_and_review_and_traces(project: Path, tmp_path: Path) -> None:
    out = invoke("redteam", "run", "configs/redteam/good-synthetic.yaml", "--root", str(project))
    assert "overall attack success rate: 0.0%" in out
    unsafe = cli.invoke(
        app, ["redteam", "run", "configs/redteam/unsafe-synthetic.yaml", "--root", str(project)]
    )
    assert unsafe.exit_code == 2 and "overall attack success rate: 0.0%" not in unsafe.output
    out = invoke(
        "traces", "import", "examples/traces/sample_traces.jsonl", str(tmp_path / "t.jsonl")
    )
    assert "metadata_only" in out
    stored = (tmp_path / "t.jsonl").read_text(encoding="utf-8")
    assert "Acme Sync" not in stored and "someone@example.com" not in stored
    assert "retrieval" in invoke("traces", "analyze-otel", "examples/traces/otel_export.json")
    assert "safety_pass_rate" in invoke(
        "canary",
        "run",
        "synthetic/good",
        "synthetic/unsafe",
        "--suite",
        "prompt_injection",
        "--root",
        str(project),
    )


def test_cli_incident_workflow(project: Path) -> None:
    invoke("incidents", "add", "examples/incidents/INC-EXAMPLE-0002.yaml", "--root", str(project))
    out = invoke("incidents", "convert", "INC-EXAMPLE-0002", "--root", str(project))
    assert "DRAFT test incident.INC-EXAMPLE-0002" in out
    sanitized = (project / "incidents" / "sanitized" / "INC-EXAMPLE-0002.yaml").read_text(
        encoding="utf-8"
    )
    assert "ops@example.com" not in sanitized
    skipped = cli.invoke(
        app, ["incidents", "activate", "incident.INC-EXAMPLE-0002", "--root", str(project)]
    )
    assert skipped.exit_code == 1  # DRAFT -> ACTIVE is not allowed
    invoke(
        "incidents",
        "review",
        "incident.INC-EXAMPLE-0002",
        "--reviewer",
        "rev-1",
        "--root",
        str(project),
    )
    invoke("incidents", "activate", "incident.INC-EXAMPLE-0002", "--root", str(project))
    assert "active" in invoke("incidents", "list", "--root", str(project))
    assert "1" in invoke("suites", "inspect", "incident_regressions", "--root", str(project))


def test_release_gate_demo_rejects_candidate(tmp_path: Path) -> None:
    out = invoke("demo", "release-gate", "--output", str(tmp_path))
    assert "SYNTHETIC / MOCK SYSTEM RESULTS" in out and "Release decision: FAIL" in out
    results = tmp_path / "results"
    decisions = [json.loads(p.read_text(encoding="utf-8")) for p in results.glob("*/gates.json")]
    assert len(decisions) == 1 and decisions[0]["decision"] == "fail"
    summaries = {
        p.parent.name: json.loads(p.read_text(encoding="utf-8"))
        for p in results.glob("*/summary.json")
    }
    base = next(v for k, v in summaries.items() if "baseline" in k)
    cand = next(v for k, v in summaries.items() if "candidate" in k)
    # Aggregate quality improves...
    assert cand["metrics"]["pass_rate"]["value"] > base["metrics"]["pass_rate"]["value"]
    assert (
        cand["categories"]["correctness"]["pass_rate"]
        > base["categories"]["correctness"]["pass_rate"]
    )
    # ...but prompt-injection safety regresses, and the gate blocks on it.
    assert (
        cand["categories"]["prompt_injection"]["pass_rate"]
        < base["categories"]["prompt_injection"]["pass_rate"]
    )
    gates = {g["gate_id"]: g["outcome"] for g in decisions[0]["gates"]}
    assert (
        gates["prompt_injection"] == "fail"
        and gates["critical"] == "fail"
        and gates["overall_accuracy"] == "pass"
    )


def test_incident_demo_baseline_fails_fix_passes(tmp_path: Path) -> None:
    out = invoke("demo", "incident-to-eval", "--output", str(tmp_path))
    assert "draft -> reviewed -> active" in out
    assert "buggy system v1: FAIL | fixed system v2: PASS" in out
    assert "openai_key" in out  # fake key was redacted before conversion
    invoke("demo", "incident-to-eval", "--output", str(tmp_path))  # re-runnable


def test_drift_demo_detects_shifts(tmp_path: Path) -> None:
    out = invoke("demo", "drift", "--output", str(tmp_path))
    assert "overall: drift" in out


def test_judge_calibration_demo(tmp_path: Path) -> None:
    out = invoke("demo", "judge-calibration", "--output", str(tmp_path))
    assert "SYNTHETIC / SCRIPTED JUDGE RESULTS" in out and "order-inconsistent" in out
