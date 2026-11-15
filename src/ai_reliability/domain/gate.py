"""Quality gate specifications and release decisions."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from ai_reliability.domain.base import DomainModel
from ai_reliability.domain.enums import GateDecision


class GateOutcome(StrEnum):
    """Outcome of a single gate."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


class GateSpec(DomainModel):
    """A single typed quality gate.

    * ``metric`` gates bound a run-level metric (``min`` / ``max``). Metrics prefixed
      with ``regression_`` or ``new_failure`` come from the baseline comparison.
    * ``category`` gates bound a category's pass rate and, given a baseline, its
      regressions — this is what lets a safety category veto an overall improvement.
    * ``critical_failures`` gates bound the number of failed CRITICAL-severity tests.
    """

    gate_id: str
    kind: Literal["metric", "category", "critical_failures"]
    description: str = ""
    metric: str | None = None
    category: str | None = None
    min: float | None = None
    max: float | None = None
    pass_rate_min: float | None = Field(default=None, ge=0.0, le=1.0)
    max_regressions: int | None = Field(default=None, ge=0)
    max_pass_rate_drop: float | None = Field(default=None, ge=0.0, le=1.0)
    on_violation: Literal["fail", "warn"] = "fail"
    on_missing: Literal["fail", "warn", "skip"] = "warn"

    @model_validator(mode="after")
    def _consistent(self) -> GateSpec:
        if self.kind == "metric":
            if not self.metric:
                raise ValueError(f"gate {self.gate_id}: metric gate needs 'metric'")
            if self.min is None and self.max is None:
                raise ValueError(f"gate {self.gate_id}: metric gate needs 'min' or 'max'")
        elif self.kind == "category":
            if not self.category:
                raise ValueError(f"gate {self.gate_id}: category gate needs 'category'")
            if (
                self.pass_rate_min is None
                and self.max_regressions is None
                and self.max_pass_rate_drop is None
            ):
                raise ValueError(
                    f"gate {self.gate_id}: category gate needs pass_rate_min, "
                    "max_regressions or max_pass_rate_drop"
                )
        elif self.max is None:
            raise ValueError(f"gate {self.gate_id}: critical_failures gate needs 'max'")
        return self


class GatePolicy(DomainModel):
    """A named set of gates plus global policy switches."""

    policy_id: str = "default"
    version: str = "1"
    gates: tuple[GateSpec, ...]
    block_on_critical_failure: bool = True


class GateResult(DomainModel):
    """Outcome of evaluating one gate, with the observed value and an explicit reason."""

    gate_id: str
    outcome: GateOutcome
    observed: float | None = None
    threshold: str = ""
    reason: str


class ReleaseDecision(DomainModel):
    """The release verdict. Every FAIL/WARNING is backed by explicit reasons."""

    run_id: str
    baseline_run_id: str | None = None
    policy_id: str
    decision: GateDecision
    gates: tuple[GateResult, ...]
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    critical_failures: tuple[str, ...] = ()
