"""Layer 3: secret detection and scrubbing. Pure, deterministic, stdlib only.

INVARIANTS (each has a witness in tests/gates/test_secrets.py):
  S1  `scrub` never returns text that contains a value one of its detectors recognises (idempotent: `scrub(scrub(t)) == scrub(t)`).
  S2  Findings carry a kind and a span, never the secret or a digest of it.
  S3  Text with no finding is returned unchanged (same string), so scrubbing clean output costs nothing and changes nothing.
  S4  The result is a function of the text alone: same input, same output, in any order of calls.

Detectors are the repository's existing patterns (`wisp.auth.secrets`, the single source for audit redaction) plus vendor patterns
in the style of gitleaks, plus an entropy detector for tokens no pattern names. The entropy detector is deliberately narrow: a
long token needs digits and mixed case, or a credential-looking word on the same line, so hashes, UUIDs and identifiers pass.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from wisp.auth.secrets import SECRET_PATTERNS

# Vendor patterns not in the shared list. Ordered specific to generic; spans are merged afterwards so order only breaks ties.
EXTRA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openrouter-key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}\b")),
    ("nvidia-key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{30,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{20,}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("pypi-token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{40,}\b")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+")),
    ("discord-webhook", re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]{1,64}:([^\s/@]{3,128})@[^\s/]+", re.IGNORECASE)),
    ("env-secret-line", re.compile(
        r"(?im)^\s*(?:export\s+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|CREDENTIAL)[A-Z0-9_]*\s*=\s*['\"]?([^\s'\"#]{8,})")),
)

PLACEHOLDER = "[REDACTED:{kind}]"
_PLACEHOLDER_RE = re.compile(r"\[REDACTED:[a-z0-9-]+\]")

# Kinds the network-command gate treats as certain enough to refuse on (a generic `password=` or an entropy hit is not).
HIGH_CONFIDENCE_EXCLUDED = frozenset({"secret-assignment", "env-secret-line", "entropy", "bearer-token"})

_CANDIDATE = re.compile(r"[A-Za-z0-9+/_=-]{20,}")
_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CONTEXT_WORD = re.compile(r"(?i)(secret|token|passw(?:or)?d|passwd|credential|api[_-]?key|auth|bearer|private[_-]?key)")
_BENIGN_CONTEXT = re.compile(r"(?i)(integrity|checksum|sha\d{1,3}[-:=]?|digest|etag|\bhash\b|md5|blake)")
_ENTROPY_BARE = 4.1  # bits per character, for a token with no credential word near it
_ENTROPY_CONTEXT = 3.4  # ... with one on the same line


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class ScrubResult:
    text: str
    findings: tuple[Finding, ...]

    @property
    def clean(self) -> bool:
        return not self.findings


def shannon_entropy(token: str) -> float:
    if not token:
        return 0.0
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, len(text) if end == -1 else end


def _inside_placeholder(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(ps <= start and end <= pe for ps, pe in spans)


def _pattern_findings(text: str) -> list[Finding]:
    found: list[Finding] = []
    placeholder_spans = [(m.start(), m.end()) for m in _PLACEHOLDER_RE.finditer(text)]
    for kind, rx in SECRET_PATTERNS + EXTRA_PATTERNS:
        for m in rx.finditer(text):
            if kind in ("secret-assignment", "provider-key-assignment", "env-secret-line", "url-credentials") and m.lastindex:
                start, end = m.start(m.lastindex), m.end(m.lastindex)
            else:
                start, end = m.start(), m.end()
            if _inside_placeholder(start, end, placeholder_spans):
                continue  # `secret=[REDACTED:x]`: the value is already a placeholder, and re-matching it would never reach a fixed point (S1)
            found.append(Finding(kind, start, end))
    return found


def _entropy_findings(text: str) -> list[Finding]:
    found: list[Finding] = []
    placeholder_spans = [(m.start(), m.end()) for m in _PLACEHOLDER_RE.finditer(text)]
    for m in _CANDIDATE.finditer(text):
        tok = m.group(0)
        if any(m.start() < pe and ps < m.end() for ps, pe in placeholder_spans):
            continue  # already scrubbed; re-detecting the label would break idempotency (S1)
        if _UUID.match(tok) or tok.count("/") >= 2:
            continue
        ls, le = _line_bounds(text, m.start())
        before = text[ls:m.start()]
        if _BENIGN_CONTEXT.search(before):
            continue  # lockfile integrity strings, checksums and digests are high-entropy by design
        has_context = bool(_CONTEXT_WORD.search(before))
        if _HEX_ONLY.match(tok) and not has_context:
            continue  # git SHAs, content hashes
        classes = sum(bool(re.search(p, tok)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
        if not has_context and not (classes == 3 and len(tok) >= 24):
            continue
        h = shannon_entropy(tok)
        if h >= (_ENTROPY_CONTEXT if has_context else _ENTROPY_BARE) and (len(tok) >= 20 if has_context else len(tok) >= 24):
            found.append(Finding("entropy", m.start(), m.end()))
    return found


def _merge(findings: list[Finding]) -> tuple[Finding, ...]:
    """Overlapping spans become one, labelled by the earliest-starting, then longest, then alphabetically first kind."""
    ordered = sorted(findings, key=lambda f: (f.start, -(f.end - f.start), f.kind))
    out: list[Finding] = []
    for f in ordered:
        if out and f.start < out[-1].end:
            last = out[-1]
            out[-1] = Finding(last.kind, last.start, max(last.end, f.end))
        else:
            out.append(f)
    return tuple(out)


def find(text: str, *, entropy: bool = True) -> tuple[Finding, ...]:
    if not isinstance(text, str) or not text:
        return ()
    found = _pattern_findings(text)
    if entropy:
        found += _entropy_findings(text)
    return _merge(found)


def scrub(text: str, *, entropy: bool = True) -> ScrubResult:
    """Replace every finding with `[REDACTED:kind]`. Returns `text` itself when nothing was found (S3)."""
    findings = find(text, entropy=entropy)
    if not findings:
        return ScrubResult(text, ())
    out: list[str] = []
    cursor = 0
    for f in findings:
        out.append(text[cursor:f.start])
        out.append(PLACEHOLDER.format(kind=f.kind))
        cursor = f.end
    out.append(text[cursor:])
    return ScrubResult("".join(out), findings)


def high_confidence_kinds(text: str) -> tuple[str, ...]:
    """Sorted, de-duplicated kinds of the findings certain enough to refuse an action on."""
    return tuple(sorted({f.kind for f in find(text, entropy=False) if f.kind not in HIGH_CONFIDENCE_EXCLUDED}))
