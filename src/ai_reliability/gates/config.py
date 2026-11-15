"""Load gate policies from YAML (mapping or list form)."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ai_reliability.config.loader import read_yaml
from ai_reliability.domain.gate import GatePolicy, GateSpec
from ai_reliability.errors import ConfigurationError


def _infer_kind(body: dict[str, object]) -> str:
    if "kind" in body:
        return str(body["kind"])
    if "category" in body:
        return "category"
    if body.get("metric") == "critical_failures":
        return "critical_failures"
    return "metric"


def parse_gate_policy(data: object, *, source: str = "<memory>") -> GatePolicy:
    """Parse a policy. ``gates`` may be a mapping ``{gate_id: spec}`` or a list of specs."""
    if not isinstance(data, dict) or "gates" not in data:
        raise ConfigurationError(f"{source}: gate policy needs a top-level 'gates' key")
    raw = data["gates"]
    specs: list[GateSpec] = []
    items: list[tuple[str, object]]
    if isinstance(raw, dict):
        items = [(str(k), v) for k, v in raw.items()]
    elif isinstance(raw, list):
        items = [
            (str(v.get("gate_id", f"gate_{i}")) if isinstance(v, dict) else f"gate_{i}", v)
            for i, v in enumerate(raw)
        ]
    else:
        raise ConfigurationError(f"{source}: 'gates' must be a mapping or a list")
    try:
        for gate_id, body in items:
            if not isinstance(body, dict):
                raise ConfigurationError(f"{source}: gate {gate_id} must be a mapping")
            spec_body = {k: v for k, v in body.items() if k != "gate_id"}
            spec_body["kind"] = _infer_kind(body)
            if spec_body["kind"] == "critical_failures":
                spec_body.pop("metric", None)
            specs.append(GateSpec.model_validate({"gate_id": gate_id, **spec_body}))
        return GatePolicy(
            policy_id=str(data.get("policy_id", Path(source).stem)),
            version=str(data.get("version", "1")),
            gates=tuple(specs),
            block_on_critical_failure=bool(data.get("block_on_critical_failure", True)),
        )
    except ValidationError as exc:
        raise ConfigurationError(f"{source}: {exc}") from exc


def load_gate_policy(path: Path) -> GatePolicy:
    return parse_gate_policy(read_yaml(path), source=str(path))
