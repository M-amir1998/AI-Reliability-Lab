"""Pattern-based secret and PII redaction.

Redaction is best-effort: it removes common credential formats and PII
patterns, but it cannot guarantee that free text is free of sensitive data
(e.g. secrets in unusual formats, names, addresses). Anything derived from
production data still requires manual review before being committed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ai_reliability.domain.base import sha256_hex
from ai_reliability.domain.enums import TracePrivacy

REDACTOR_VERSION = "1"


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True, slots=True)
class RedactionRule:
    """A named pattern; ``validator`` can reject false-positive matches (e.g. Luhn)."""

    kind: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    group: int = 0


def _rule(kind: str, pattern: str, flags: int = 0, **kw: object) -> RedactionRule:
    return RedactionRule(kind, re.compile(pattern, flags), **kw)  # type: ignore[arg-type]


DEFAULT_RULES: tuple[RedactionRule, ...] = (
    _rule(
        "private_key",
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    _rule(
        "authorization_header", r"(?i)\bauthorization\s*:\s*(?:bearer|basic|token)\s+[^\s\"',;]+"
    ),
    _rule("bearer_token", r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{16,}=*"),
    _rule("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    _rule("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    _rule("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    _rule("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    _rule("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    _rule("slack_token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    _rule("google_api_key", r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    _rule(
        "credential_field",
        r"(?i)\b(?:password|passwd|pwd|secret|client_secret|api[_-]?key|access[_-]?token|token)"
        r"\s*[\"']?\s*[:=]\s*[\"']?(?!\[(?:REDACTED|PRESERVED))(?P<value>[^\s\"',;}{]{4,})",
        group=1,
    ),
    _rule("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    _rule(
        "credit_card",
        r"\b(?:\d[ -]?){12,18}\d\b",
        validator=lambda s: _luhn_ok(re.sub(r"\D", "", s)),
    ),
    _rule("phone", r"(?<![\w+])\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}\b"),
    _rule("us_phone", r"\b\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
    _rule("ipv4", r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Redacted text plus counts per redaction kind (never the original values)."""

    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class Redactor:
    """Applies redaction rules in order, replacing matches with ``[REDACTED:<kind>]``."""

    def __init__(
        self,
        rules: Iterable[RedactionRule] = DEFAULT_RULES,
        *,
        extra_literals: Iterable[str] = (),
        preserve_literals: Iterable[str] = (),
    ) -> None:
        self._rules = tuple(rules)
        # Known-synthetic values (e.g. declared canary secrets) that must survive redaction.
        self._preserve = tuple(sorted({s for s in preserve_literals if s}, key=len, reverse=True))
        literals = sorted({s for s in extra_literals if s}, key=len, reverse=True)
        self._literals = re.compile("|".join(re.escape(s) for s in literals)) if literals else None
        self.version = REDACTOR_VERSION

    def redact(self, text: str) -> RedactionResult:
        counts: Counter[str] = Counter()
        for i, literal in enumerate(self._preserve):
            text = text.replace(literal, f"[PRESERVED-{i}]")
        if self._literals is not None:
            text, n = self._literals.subn("[REDACTED:literal]", text)
            if n:
                counts["literal"] += n
        for rule in self._rules:
            text = self._apply(rule, text, counts)
        for i, literal in enumerate(self._preserve):
            text = text.replace(f"[PRESERVED-{i}]", literal)
        return RedactionResult(text=text, counts=dict(counts))

    @staticmethod
    def _apply(rule: RedactionRule, text: str, counts: Counter[str]) -> str:
        def replace(match: re.Match[str]) -> str:
            value = match.group(rule.group)
            if rule.validator is not None and not rule.validator(value):
                return match.group(0)
            counts[rule.kind] += 1
            token = f"[REDACTED:{rule.kind}]"
            if rule.group == 0:
                return token
            start, end = match.span(rule.group)
            whole_start = match.start(0)
            whole = match.group(0)
            return whole[: start - whole_start] + token + whole[end - whole_start :]

        return rule.pattern.sub(replace, text)

    def findings(self, text: str) -> dict[str, int]:
        """Counts of what *would* be redacted (used to verify sanitized output)."""
        return self.redact(text).counts


def apply_privacy(
    text: str | None, policy: TracePrivacy, redactor: Redactor | None = None
) -> str | None:
    """Apply a capture policy to a piece of text."""
    if text is None:
        return None
    if policy is TracePrivacy.FULL_TEXT:
        return text
    if policy is TracePrivacy.REDACTED_TEXT:
        return (redactor or Redactor()).redact(text).text
    if policy is TracePrivacy.HASH_ONLY:
        return "sha256:" + sha256_hex(text)
    return None
