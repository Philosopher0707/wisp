"""Tool output is data, never instructions — the scan that enforces it.

## The five properties, and where each lives

1. **Tool output is data, never instructions.** `wisp/core/context_trust.py`
   already classifies it (`TrustTag.TOOL_OUTPUT`, untrusted) and T1 keeps it out
   of instruction position. This module adds the *content* check that
   classification alone cannot make.
2. **Always enveloped.** `ContextItem.render()` fences untrusted content with
   `<<UNTRUSTED:…>>`. Structural separation is the envelope's job, not this
   module's.
3. **Always scanned.** This module. `scan()` is total: it never raises, never
   returns "unknown", and is safe on empty and enormous input.
4. **Fail-closed.** A `BLOCK` verdict means the result is **withheld**, not
   annotated. On doubt the run loses information rather than gaining an
   instruction.
5. **Tiered, with a measured false-positive rate.** See below. The rate is not
   asserted here — it is **measured** by `tests/reliability/test_injection_scan.py`
   against `tests/fixtures/injection_corpus.py`, and both share the threshold
   constants in this module so the report and the tripwire cannot disagree.

## Why tiered, and what the earlier version got wrong

A single-tier, fail-closed scan built from intuition **refused 36% of benign
tool output** — it would have refused a file-reading tool on 10 of this
repository's own 46 files, mostly for the bare phrase *"system prompt"*. Recall
was 87% at the same time: it was not trading precision for safety, it was simply
bad at both. That version is not in this repository; the number is recorded
because the method that produced it is the trap, and a rate without its origin
teaches nothing.

**The tiers, and why each exists** (every marker is justified by a corpus sample,
and every marker that caught nothing was deleted — see `MEASURED` in the
tripwire):

* **Tier 1 — instruction shapes in instruction position.** A phrase that only
  makes sense as a directive *to the reader* ("ignore all previous
  instructions"), a fabricated role boundary (`system:` / `assistant:` at line
  start), or an attempt to switch off the control that would catch it.
* **Tier 2 — evasive encodings.** A base64 token that decodes to a tier-1
  marker, or zero-width characters splitting a phrase. These exist because a
  tier-1-only scan is evaded by `echo aWdub3Jl… | base64 -d`, and a corpus
  without that shape would make tier 2 look like dead weight (the ablation's
  *other* direction: an ablation measures the corpus, not the marker).
* **`SUSPECT` — a marker in a *discussing* context.** The precision limit, made
  explicit. A lexical detector cannot tell a payload from a *discussion* of a
  payload: a security article, a bug report, a threat-model table, this
  repository's own fixtures, or a comment explaining a false positive all
  legitimately contain the markers. When **every** occurrence is quoted,
  commented, fenced or tabulated, the verdict is `SUSPECT` — **withheld and
  named**, never a killed run.

**The honest boundary.** `SUSPECT` is a heuristic and it will be wrong in both
directions on inputs nobody has written yet. It is measured, and the measurement
is the deliverable — not a claim of correctness.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable


class Verdict(StrEnum):
    """What the scan concluded about one piece of tool output."""

    CLEAN = "clean"
    #: A marker was found, but every occurrence sits in a context that discusses
    #: rather than instructs. The result is **withheld and named** — see the
    #: module docstring on granularity.
    SUSPECT = "suspect"
    #: Fail-closed. The result is not shown to the model.
    BLOCK = "block"


#: Tier 1 — instruction shapes. Each is a **phrase**, never a bare word: the
#: earlier version's failure was matching "system prompt" and "ignore", which are
#: ordinary English in a coding agent's own output.
TIER1_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore-previous", re.compile(
        r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)"
        r"\s+(?:instruction|prompt|rule|message|direction)s?", re.I)),
    ("disregard-previous", re.compile(
        r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)"
        r"(?:\s+(?:instruction|prompt|rule|message|direction)s?)?", re.I)),
    ("fake-role-boundary", re.compile(r"(?m)^\s*(?:system|assistant)\s*:", re.I)),
    ("disable-the-control", re.compile(
        r"(?:approval|confirmation|guardrail|safety|policy)(?:\s+\w+){0,3}?\s+"
        r"(?:is\s+|has\s+been\s+|was\s+|are\s+)?"
        r"(?:disabled|disab\w*|off|bypassed|waived|not\s+required)", re.I)),
    ("do-not-ask", re.compile(
        r"do\s+not\s+(?:ask|request)\s+(?:for\s+)?(?:approval|permission|confirmation)",
        re.I)),
    # Added on evidence: the first measurement missed `hidden-in-comment`
    # ("override your approval requirement"), a payload hidden where a reader
    # skims past. The shape is instruction-like and specific — it names the
    # control it wants switched off — so it belongs in tier 1 rather than in a
    # broad "override" rule that would catch ordinary prose.
    ("override-the-control", re.compile(
        r"override\s+(?:your\s+|the\s+|all\s+|any\s+)?"
        r"(?:approval|confirmation|guardrail|safety|policy|permission|restriction)s?",
        re.I)),
    ("role-override", re.compile(
        r"you\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|unfiltered|unlimited|"
        r"different|new|free)\b", re.I)),
    ("exfiltrate-credentials", re.compile(
        r"(?:id_rsa|\.ssh/|\.aws/credentials|\.env\b|private\s+key)"
        r"[\s\S]{0,120}?(?:include|send|paste|print|upload|exfiltrat)", re.I)),
)

#: Tier 2 — evasion shapes. A tier-1-only scan is evaded by one `base64 -d`, so
#: a corpus with the evasion and no tier for it would make tier 2 untested.
TIER2_BASE64 = re.compile(r"\b[A-Za-z0-9+/]{16,}={0,2}\b")
TIER2_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

#: Contexts that mark an occurrence as **discussing** rather than instructing.
#: Deliberately conservative: each must be a *structural* signal a reader would
#: recognise, not a guess about intent.
#:
#: **`COMMENT_LINE` was here and was deleted — by ablation, on evidence.** The
#: measurement scored it: it rescued **no** sample in the quoted bucket (the
#: corpus's comment sample contains no literal marker), so by the ablation rule it
#: caught nothing and was removed. **A comment is not a discussion.**
#:
#: *A correction to this note, made when the probe disagreed with it.* The first
#: version of this comment also claimed the rule "cost a payload"
#: (`hidden-in-comment`). That was true only **before** `override-the-control` was
#: added on evidence; with that marker present the sample is caught either way, and
#: restoring the comment rule now changes nothing. The deletion still stands — a
#: rule that catches nothing is removed — but the *reason* is the ablation, not a
#: measured cost. Recorded because a justification that outlives its evidence is
#: how a marker list rots.
_FENCE = re.compile(r"```[\s\S]*?```")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)

#: The thresholds the tripwire asserts. **Zero on benign**: every benign sample
#: is hand-chosen to represent output this repository really produces, so a
#: single false positive is a defect and not noise. Named here so the report and
#: the test read the same number.
MAX_BENIGN_TRIPS = 0
MIN_PAYLOAD_RECALL = 1.0
MAX_QUOTES_BLOCKED = 0


@dataclass(frozen=True)
class ScanResult:
    """One scan's outcome. `markers` is what fired; `tier` is the highest."""

    verdict: Verdict
    tier: int
    markers: tuple[str, ...] = ()
    reason: str = ""
    discussing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        """Fail-closed means only `BLOCK` withholds the result outright."""
        return self.verdict is Verdict.BLOCK


def _tier1_hits(text: str) -> list[tuple[str, int, int]]:
    hits: list[tuple[str, int, int]] = []
    for name, pattern in TIER1_PATTERNS:
        for m in pattern.finditer(text):
            hits.append((name, m.start(), m.end()))
    return hits


def _decoded_base64_markers(text: str) -> list[str]:
    """Tier 2 — base64 tokens that decode to a tier-1 phrase.

    Bounded on purpose: only tokens of a plausible length are decoded, and a
    decode failure is ignored rather than reported. A scan that raised on
    malformed input would fail *open* the first time a tool printed a stray `+`.
    """
    found: list[str] = []
    for m in TIER2_BASE64.finditer(text):
        token = m.group(0)
        if len(token) % 4:
            token += "=" * (-len(token) % 4)
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            continue
        if not decoded or not decoded.isprintable():
            continue
        for name, pattern in TIER1_PATTERNS:
            if pattern.search(decoded):
                found.append(f"{name}@base64")
    return found


def _discussing_spans(text: str) -> list[tuple[int, int]]:
    """Spans in which a marker is *discussed* rather than *instructed*.

    A quote, a fenced block, a table row, or a comment line. These are the
    contexts the corpus's third bucket lives in — and the reason the earlier
    version's 36% is avoidable at all.
    """
    spans: list[tuple[int, int]] = []
    for pattern in (_FENCE, _TABLE_ROW):
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    return spans


def _is_discussed(text: str, start: int, end: int,
                  spans: Iterable[tuple[int, int]]) -> bool:
    """True when the occurrence lies inside a discussing span, or is quoted.

    The quoted case is checked on the occurrence's own line, because a quote may
    open and close on the same line as the marker.
    """
    for s, e in spans:
        if s <= start and end <= e:
            return True
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start:line_end if line_end != -1 else len(text)]
    rel_start, rel_end = start - line_start, end - line_start
    quoted = re.compile(
        r'["\'`\u201c\u2018][^"\n]*' + re.escape(line[rel_start:rel_end]) + r'[^"\n]*["\'`\u201d\u2019]'
    )
    return bool(quoted.search(line))


def scan(text: str) -> ScanResult:
    """Scan one piece of tool output. Total: never raises, never returns None.

    Empty input is `CLEAN` — a scan that blocked on nothing would refuse every
    tool that printed nothing, which is most of them.
    """
    if not text or not text.strip():
        return ScanResult(Verdict.CLEAN, 0, reason="empty")

    hits = _tier1_hits(text)
    spans = _discussing_spans(text)

    bare = [h for h in hits if not _is_discussed(text, h[1], h[2], spans)]
    if bare:
        markers = tuple(sorted({h[0] for h in bare}))
        return ScanResult(
            Verdict.BLOCK, 1, markers,
            reason=f"instruction shape in instruction position: {', '.join(markers)}",
        )

    # Tier 2 — evasion. Zero-width characters are only a signal when they are
    # splitting *something*; a lone one in a unicode-heavy diff is noise, so the
    # rule requires a tier-1 phrase to be recoverable once they are stripped.
    stripped = TIER2_ZERO_WIDTH.sub("", text)
    if stripped != text:
        recovered = [n for n, p in TIER1_PATTERNS if p.search(stripped)]
        if recovered:
            return ScanResult(
                Verdict.BLOCK, 2, tuple(f"{n}@zero-width" for n in sorted(recovered)),
                reason="a tier-1 phrase recovered after stripping zero-width "
                       "characters — the phrase was split to evade a scan",
            )

    decoded = _decoded_base64_markers(text)
    if decoded:
        return ScanResult(
            Verdict.BLOCK, 2, tuple(sorted(set(decoded))),
            reason="a base64 token decodes to an instruction shape",
        )

    if hits:
        markers = tuple(sorted({h[0] for h in hits}))
        return ScanResult(
            Verdict.SUSPECT, 1, markers,
            discussing=markers,
            reason=f"marker(s) found only in a discussing context: {', '.join(markers)} "
                   "— the result is withheld and named, not refused",
        )

    return ScanResult(Verdict.CLEAN, 0, reason="no marker")


__all__ = [
    "Verdict", "ScanResult", "scan",
    "TIER1_PATTERNS", "TIER2_BASE64", "TIER2_ZERO_WIDTH",
    "MAX_BENIGN_TRIPS", "MIN_PAYLOAD_RECALL", "MAX_QUOTES_BLOCKED",
]
