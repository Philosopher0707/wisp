"""Graph-layer scrubbing — secret redaction at persist/display boundaries.

Reuses ``wisp.auth.secrets`` (the single detection mechanism) plus a small
set of graph-relevant extras the shared patterns miss (OpenAI/Anthropic
key assignments, ``sk-`` tokens). New secret *families* belong in
``wisp/auth/secrets.py``; this module only closes the graph layer's gap
without forking the mechanism.
"""

from __future__ import annotations

import re
from typing import Any


_EXTRA = (
    ("openai-key-assignment", re.compile(
        r"(?i)\b(openai[_-]?api[_-]?key|anthropic[_-]?api[_-]?key)\b\s*[:=]\s*"
        r"['\"]?([^'\"\s]{6,})['\"]?")),
    ("sk-token", re.compile(r"\bsk-(ant|proj)-[A-Za-z0-9\-_]{8,}\b")),
    ("sk-bare", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
)


def scrub_text(text: str) -> str:
    from wisp.auth.secrets import redact
    out = redact(text)
    for name, rx in _EXTRA:
        if name == "openai-key-assignment":
            out = rx.sub(lambda m: m.group(0).replace(m.group(2), f"[REDACTED:{name}]"), out)
        else:
            out = rx.sub(f"[REDACTED:{name}]", out)
    return out


def scrub(obj: Any) -> Any:
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return obj
