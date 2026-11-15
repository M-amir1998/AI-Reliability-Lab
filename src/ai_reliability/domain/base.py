"""Shared base model and hashing helpers for domain objects."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, JsonValue

JsonObject = dict[str, JsonValue]


class DomainModel(BaseModel):
    """Immutable, strictly validated base model for all domain objects."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    def canonical_json(self) -> str:
        """Deterministic JSON representation used for hashing (sorted keys, no whitespace)."""
        payload = self.model_dump(mode="json", exclude_none=False)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON representation (first 16 hex chars)."""
        return sha256_hex(self.canonical_json())[:16]


def sha256_hex(text: str) -> str:
    """Full hex SHA-256 digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_dumps(value: JsonValue) -> str:
    """Deterministic JSON serialization for arbitrary JSON values."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
