"""Sanitized trace records and their import (capture policy applied at the boundary)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_reliability.domain.base import DomainModel, sha256_hex
from ai_reliability.domain.enums import TracePrivacy
from ai_reliability.errors import ConfigurationError
from ai_reliability.privacy.redaction import Redactor, apply_privacy


class TraceRecord(DomainModel):
    """Canonical trace. Text fields are present only under REDACTED_TEXT / FULL_TEXT policies."""

    trace_id: str
    input_text: str | None = None
    input_sha256: str
    response_text: str | None = None
    response_sha256: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: tuple[str, ...] = ()
    error: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    privacy: TracePrivacy = TracePrivacy.METADATA_ONLY

    @property
    def replayable(self) -> bool:
        return self.input_text is not None and self.privacy in {
            TracePrivacy.REDACTED_TEXT,
            TracePrivacy.FULL_TEXT,
        }


class _RawTrace(BaseModel):
    """Input format for JSONL trace exports (one object per line; unknown keys ignored)."""

    model_config = ConfigDict(extra="ignore")
    trace_id: str
    input: str = ""
    response: str = ""
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def to_record(raw: _RawTrace, privacy: TracePrivacy, redactor: Redactor) -> TraceRecord:
    return TraceRecord(
        trace_id=raw.trace_id,
        input_text=apply_privacy(raw.input, privacy, redactor),
        input_sha256=sha256_hex(raw.input),
        response_text=apply_privacy(raw.response, privacy, redactor),
        response_sha256=sha256_hex(raw.response) if raw.response else None,
        latency_ms=raw.latency_ms,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        tool_calls=tuple(raw.tool_calls),
        error=apply_privacy(raw.error, TracePrivacy.REDACTED_TEXT, redactor) if raw.error else None,
        metadata={k: v for k, v in raw.metadata.items() if not redactor.findings(f"{k}={v}")},
        privacy=privacy,
    )


def import_jsonl(
    path: Path,
    *,
    privacy: TracePrivacy = TracePrivacy.METADATA_ONLY,
    redactor: Redactor | None = None,
) -> Iterator[TraceRecord]:
    """Stream trace records from JSONL, applying ``privacy`` before anything is retained.

    Default policy is METADATA_ONLY: raw prompt/response text is never kept.
    """
    red = redactor or Redactor()
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                raw = _RawTrace.model_validate(json.loads(line))
            except (ValueError, ValidationError) as exc:
                raise ConfigurationError(f"{path}:{number}: invalid trace record") from exc
            yield to_record(raw, privacy, red)
