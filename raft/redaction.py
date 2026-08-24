from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class RedactionResult:
    text: str
    replacements: dict[str, str]


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)")
# Real keys carry internal separators - `sk-live-9f2b71c4ad55e0`, `api_key_v2_...`
# - so the body has to allow them, not stop at the first hyphen.
KEY_RE = re.compile(
    r"\b(?:sk|pk|rk|api[-_]?key|access[-_]?token|bearer|token|secret|password)"
    r"[-_][A-Za-z0-9][A-Za-z0-9_-]{9,}\b",
    re.IGNORECASE,
)


def _stable_replace(text: str, pattern: re.Pattern[str], kind: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}
    counters: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        source = match.group(0)
        normalized = source.casefold()
        if normalized not in replacements:
            counters[kind] = counters.get(kind, 0) + 1
            replacements[normalized] = f"[{kind}_{counters[kind]}]"
        return replacements[normalized]

    return pattern.sub(replace, text), replacements


def redact_text(text: str) -> RedactionResult:
    output = text
    combined: dict[str, str] = {}
    for pattern, kind in ((EMAIL_RE, "EMAIL"), (PHONE_RE, "PHONE"), (KEY_RE, "SECRET")):
        output, replacements = _stable_replace(output, pattern, kind)
        combined.update(replacements)
    return RedactionResult(text=output, replacements=combined)


def contains_pii(text: str) -> bool:
    return any(pattern.search(text) for pattern in (EMAIL_RE, PHONE_RE, KEY_RE))


def verify_literal_quote(quote: str, redacted_source: str) -> bool:
    return bool(quote) and quote in redacted_source and not contains_pii(quote)

