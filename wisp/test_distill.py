"""Pytest traceback distiller (smart test reflection).

Raw pytest logs (10k+ tokens of warnings, captured stdout, passing dots)
flood the model's context via the verification floor. The model needs three
things only:

  1. the failing test name and assertion line;
  2. the localized traceback stack (file, line number, offending code);
  3. the terminal exception message (AssertionError, TypeError, ...).

:func:`distill_traceback` is a pure function — no pytest, subprocess, or
workspace imports — so it is unit-testable offline. It handles ``--tb=short``
and ``--tb=long`` styles plus bare tracebacks, preserves chained exceptions,
and degrades gracefully (unknown input passes through truncated, never lost).
"""

from __future__ import annotations

import re

# pytest --tb=short frame: "tests/test_x.py:12: in test_y"
_SHORT_FRAME_RE = re.compile(r"^(?P<loc>\S+:\d+): in (?P<func>\S+)\s*$")
# Python 3.11+ traceback frame: '  File "/path/x.py", line 12, in func'
_LONG_FRAME_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>\S+))?')
# Terminal exception line: "AssertionError: ..." / "TypeError" / "E   ..." stripped form.
_EXCEPTION_RE = re.compile(r"^(?P<kind>[A-Za-z_][\w.]*?(?:Error|Exception|Warning|Exit|Interrupt|Fail))\b(?P<msg>:.*)?$")
# Section headers worth keeping vs noise worth dropping.
_KEEP_HEADER_RE = re.compile(r"^(FAILED|ERROR)\s+\S+")
_NOISE_START_RES = (
    re.compile(r"^=+ warnings summary =+$"),
    re.compile(r"^=+ short test summary info =+$"),
    re.compile(r"^-+ (Captured (stdout|stderr|log)|Docs|Coverage)"),
    re.compile(r"^\S+ warnings? (summary|in )"),
)
_CHAIN_MARKERS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)
_MAX_FRAMES_HEAD = 3
_MAX_FRAMES_TAIL = 5
_DEFAULT_MAX_CHARS = 1500


def distill_traceback(raw: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Distill a raw pytest traceback/log to failing-test essentials.

    Keeps: FAILED/ERROR headers, traceback frames (head+tail with elision
    marker), source lines (assertion/offending code), ``E``-prefixed detail
    lines, chaining markers, terminal exception lines. Drops: warnings
    summaries, captured stdout/stderr/log sections, coverage/docs footers,
    separator banners, progress noise. Result is capped at *max_chars*.
    """
    if not raw or not raw.strip():
        return ""
    kept: list[str] = []
    frames = 0
    in_noise = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            in_noise = False
            continue
        if any(rx.match(stripped) for rx in _NOISE_START_RES):
            in_noise = True
            continue
        if in_noise:
            # Noise sections end at the next section banner or failure header.
            if stripped.startswith("=") or stripped.startswith("_") or _KEEP_HEADER_RE.match(stripped):
                in_noise = False
            else:
                continue
        if stripped.startswith("=") and stripped.endswith("="):
            continue  # separator banners ("=== FAILURES ===")
        kept.append(_classify_line(line, stripped))
        if kept[-1] is None:
            kept.pop()
            continue
        if _is_frame(stripped):
            frames += 1
    if not kept:
        # Nothing recognizable — truncate, never lose the signal entirely.
        return _cap(raw.strip(), max_chars)
    out = _elide_middle_frames(kept)
    return _cap("\n".join(out), max_chars)


def _is_frame(stripped: str) -> bool:
    return bool(_SHORT_FRAME_RE.match(stripped) or _LONG_FRAME_RE.match(stripped))


def _classify_line(line: str, stripped: str) -> str | None:
    """Return the distilled form of one line, or None to drop it."""
    if _KEEP_HEADER_RE.match(stripped):
        return stripped
    if stripped in _CHAIN_MARKERS:
        return stripped
    if _SHORT_FRAME_RE.match(stripped):
        m = _SHORT_FRAME_RE.match(stripped)
        assert m is not None
        return f"  {m.group('loc')} in {m.group('func')}"
    m = _LONG_FRAME_RE.match(line)
    if m:
        func = f" in {m.group('func')}" if m.group("func") else ""
        return f"  {m.group('file')}:{m.group('line')}{func}"
    if stripped.startswith("E   "):
        return f"  {stripped[4:].strip()}"
    if stripped.startswith("E "):
        return f"  {stripped[2:].strip()}"
    if stripped.startswith("_") and stripped.endswith("_") and " " in stripped:
        return stripped.strip("_ ")  # "______ test_y ______" -> test name
    if _EXCEPTION_RE.match(stripped):
        return stripped
    # Indented source line (assertion / offending code) — keep, dedented.
    if line[:1] == " " and stripped and not stripped.startswith(("at ", "-")):
        return f"    {stripped}"
    return None


def _elide_middle_frames(lines: list[str]) -> list[str]:
    """Collapse long frame runs to head + tail with an elision marker."""
    frame_idx = [i for i, ln in enumerate(lines) if ln.startswith("  ") and ": in " in ln or _frame_like(ln)]
    if len(frame_idx) <= _MAX_FRAMES_HEAD + _MAX_FRAMES_TAIL + 1:
        return lines
    keep = set(frame_idx[:_MAX_FRAMES_HEAD]) | set(frame_idx[-_MAX_FRAMES_TAIL:])
    out: list[str] = []
    elided = 0
    for i, ln in enumerate(lines):
        if i in frame_idx and i not in keep:
            elided += 1
            continue
        if elided and out and not out[-1].endswith("frames elided ..."):
            out.append(f"  ... {elided} frames elided ...")
            elided = 0
        out.append(ln)
    return out


def _frame_like(line: str) -> bool:
    return line.startswith("  ") and re.match(r"^\s*\S+:\d+(\s+in\s+\S+)?$", line) is not None


def _cap(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (distilled output truncated)"
