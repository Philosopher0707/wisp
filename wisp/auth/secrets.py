"""Secret hygiene: detection + redaction (M2 authority layer, pure).

Redaction happens at record construction (audit entries, trace spans), not
at export — a misconfigured sink cannot leak. Provider keys and keychain
handles must never reach sessions, traces, or audit logs.
"""
from __future__ import annotations
import re
from typing import Any

# (pattern name, compiled regex). Order matters: specific before generic.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{8,}={0,2}\b")),
    ("private-key-block", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")),
    ("secret-assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*"
        r"['\"]?([^'\"\s]{6,})['\"]?")),
)

REDACTED = "[REDACTED:{name}]"


def scan_for_secrets(text: str) -> list[str]:
    """Return the names of secret patterns found in text (empty = clean)."""
    if not isinstance(text, str):
        return []
    return [name for name, rx in SECRET_PATTERNS if rx.search(text)]


def redact(text: str) -> str:
    """Replace secret material with labeled placeholders.

    PEM blocks keep their header/footer labels (useful for audit) with the
    base64 body removed; all other matches are replaced wholesale.
    """
    if not isinstance(text, str):
        return text
    out = text
    for name, rx in SECRET_PATTERNS:
        if name == "private-key-block":
            out = rx.sub(
                lambda m: m.group(0).splitlines()[0]
                + "\n" + REDACTED.format(name=name) + "\n"
                + m.group(0).splitlines()[-1],
                out,
            )
        elif name == "secret-assignment":
            out = rx.sub(
                lambda m: m.group(0).replace(m.group(2), REDACTED.format(name=name)),
                out,
            )
        else:
            out = rx.sub(REDACTED.format(name=name), out)
    return out


def redact_record(obj: Any) -> Any:
    """Recursively redact strings in dicts/lists/tuples (audit payloads)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_record(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_record(v) for v in obj]
    return obj
