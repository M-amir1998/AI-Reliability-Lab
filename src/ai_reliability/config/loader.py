"""YAML loading with safe parsing and typed validation."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from ai_reliability.domain.base import canonical_dumps, sha256_hex
from ai_reliability.errors import ConfigurationError

M = TypeVar("M", bound=BaseModel)


def read_yaml(path: Path) -> object:
    """Parse YAML with ``safe_load`` (never constructs arbitrary Python objects)."""
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}") from exc


def load_model(path: Path, model: type[M]) -> M:
    """Load ``path`` and validate it as ``model``."""
    data = read_yaml(path)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"{path}: {exc}") from exc


def dump_yaml(model: BaseModel) -> str:
    """Serialize a model to YAML (JSON-compatible types only)."""
    return yaml.safe_dump(
        model.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True
    )


def config_hash(model: BaseModel) -> str:
    """Stable hash of a configuration model."""
    return sha256_hex(canonical_dumps(model.model_dump(mode="json")))[:16]
