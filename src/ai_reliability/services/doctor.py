"""Environment diagnostics. A missing GPU is expected and never an error."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import psutil

from ai_reliability.domain.base import DomainModel

_PROVIDER_ENV = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "INFERENCELAB_API_KEY",
    "AGENTRUNTIME_API_KEY",
)


class Check(DomainModel):
    name: str
    status: str  # ok | info | warn
    detail: str


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _docker() -> Check:
    docker = shutil.which("docker")
    if docker is None:
        return Check(
            name="docker", status="info", detail="not installed (only needed for image builds)"
        )
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Check(name="docker", status="warn", detail="CLI present, daemon not responding")
    if out.returncode != 0:
        return Check(name="docker", status="warn", detail="CLI present, daemon not running")
    return Check(name="docker", status="ok", detail=f"server {out.stdout.strip()}")


def run_checks(root: Path) -> list[Check]:
    mem = psutil.virtual_memory()
    disk = shutil.disk_usage(root)
    checks = [
        Check(
            name="python",
            status="ok" if sys.version_info >= (3, 11) else "warn",
            detail=f"{platform.python_implementation()} {platform.python_version()}",
        ),
        Check(
            name="platform",
            status="info",
            detail=f"{platform.system()} {platform.release()} ({platform.machine()})",
        ),
        Check(
            name="cpu",
            status="ok",
            detail=f"{psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads",
        ),
        Check(
            name="ram",
            status="ok" if mem.total >= 8 * 2**30 else "warn",
            detail=f"{mem.total / 2**30:.1f} GiB total, {mem.available / 2**30:.1f} GiB available",
        ),
        Check(
            name="disk",
            status="ok" if disk.free >= 5 * 2**30 else "warn",
            detail=f"{disk.free / 2**30:.1f} GiB free at {root}",
        ),
        Check(name="gpu", status="info", detail="not required - all core features run on CPU"),
        _docker(),
        Check(
            name="duckdb",
            status="ok" if _version("duckdb") else "warn",
            detail=_version("duckdb") or "missing",
        ),
        Check(
            name="pyarrow",
            status="ok" if _version("pyarrow") else "warn",
            detail=_version("pyarrow") or "missing",
        ),
    ]
    obs = [p for p in ("prometheus-client", "opentelemetry-sdk") if _version(p)]
    checks.append(
        Check(
            name="observability",
            status="info",
            detail=", ".join(obs) if obs else "optional extras not installed",
        )
    )
    configured = [name for name in _PROVIDER_ENV if os.environ.get(name)]
    checks.append(
        Check(
            name="judge/provider credentials",
            status="info",
            detail=("set: " + ", ".join(configured))
            if configured
            else "none set (offline mode; values are never printed)",
        )
    )
    return checks
