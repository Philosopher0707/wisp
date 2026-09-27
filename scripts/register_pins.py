"""The one rule for a derived register's source pin: the quoted words are on the cited line.

A register row cites its source as ``path:line — “the source's own words”``. Until this module,
both register generators checked only that ``path`` exists and ``line`` is in range — the weakness
``17130c7`` closed for ``CURRENT_AUTHORITIES.md`` — so a pin could drift onto unrelated text and
still pass. Measured by corpus governance II: 12 findings and 55 open items had drifted, chiefly
because sections were inserted above ``CONTEXT.md`` §12.

**One module, two callers.** ``derive_current_findings.py`` and ``derive_current_open_items.py``
both import it, so the two registers cannot disagree about what a stale pin is.

**The observation point is the source file's own text** (F96): the check reads the cited file and
looks in a window around the cited line. It never reads a copy of the source kept in the register.

**The window is ±3 lines — the whole design, and the case that needs each edge:**

* *Not 0.* A quotation wraps. ``WISP_MIGRATION_STATUS.md``'s ledger rows are single lines, but the
  phase reports hard-wrap prose at ~100 columns, so a quoted sentence that begins on the cited line
  can end on the next one (``PHASE_DAG_RETIREMENT.md:150``'s *"Rewritten with ``ast``: only an
  ``ImportFrom`` of the name, or a ``Call`` to it, counts."* spans ``:150``–``:151``). A check that
  looked at one line would call a correct pin stale — F92's nuisance class.
* *Not wide.* The quotes are short status phrases (*"OPEN, recorded"*, *"✅ DONE"*), and the same
  phrase recurs across a document. A window of the whole file would pass a pin that has drifted a
  hundred lines onto a *different* row carrying the same words — the vacuous form. ±3 is
  ``17130c7``'s tolerance for the authorities page, and a header shift of a line or two is not drift.
"""
from __future__ import annotations

import pathlib
import re

#: The window, either side of the cited line. See the module docstring for why it is 3.
WINDOW = 3

#: A fragment shorter than this is not evidence: "OPEN" occurs on most lines of a ledger.
MIN_FRAGMENT = 6

#: ``path:line — “quote”`` — the quote that belongs to the pin is the one directly after it.
#: A cell may quote a second source later (e.g. *"… ; `CONTEXT.md` §0 records “…”"*); that
#: quote is not at this pin's path and is deliberately not checked against it.
_PIN = re.compile(r"^(?P<path>[A-Za-z0-9_./-]+\.md):(?P<line>\d+) — “")


def _balanced_quote(text: str, start: int) -> str | None:
    """The text of the curly quote opened just before ``start``, honouring nested “…”."""
    depth = 1
    for i in range(start, len(text)):
        if text[i] == "“":
            depth += 1
        elif text[i] == "”":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return None


def normalise(text: str) -> str:
    """Presentation is not words: emphasis, code ticks, ✅, and the quote glyphs' shape.

    **A table-cell boundary is the register's ``—``.** A register cell may not contain ``|`` —
    the generator refuses one, because a quoted table row once split the register's own table and
    hid F37 and F39 from their guard — so a quote that spans two cells of a source table writes
    the boundary as `` — ``. Normalising the source's `` | `` the same way compares like with like.
    """
    text = text.replace(" | ", " — ")
    text = re.sub(r"[*`✅]", "", text).replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip(" |")


def fragments(quote: str) -> list[str]:
    """The quote's words, split at an elision (``…``). Every fragment must be present."""
    parts = [normalise(p) for p in re.split(r"…|\.\.\.", quote)]
    return [p for p in parts if len(p) >= MIN_FRAGMENT]


def pinned_quote(src: str) -> tuple[str, int, list[str]] | None:
    """``(path, line, fragments)`` for a checkable source cell, else ``None``."""
    m = _PIN.match(src)
    quote = _balanced_quote(src, m.end()) if m else None
    if quote is None:
        return None
    frags = fragments(quote)
    return (m.group("path"), int(m.group("line")), frags) if frags else None


def window_text(lines: list[str], line: int, width: int = WINDOW) -> str:
    return normalise(" ".join(lines[max(0, line - 1 - width):line + width]))


def quote_is_at(lines: list[str], line: int, frags: list[str], width: int = WINDOW) -> bool:
    text = window_text(lines, line, width)
    return all(f in text for f in frags)


def locate(lines: list[str], frags: list[str], near: int) -> int | None:
    """Where the quote now is: the line carrying the first fragment's start, nearest ``near``.

    Used to make a refusal actionable (it names the line to re-pin to). A quote found nowhere
    returns ``None`` — its source has been amended, which is a §Findings entry, not a re-pin.
    """
    head = frags[0][:24]
    hits = [i + 1 for i, ln in enumerate(lines)
            if head in normalise(ln) and quote_is_at(lines, i + 1, frags)]
    if not hits:
        # The start of a wrapped quote may itself wrap; fall back to any window that holds it.
        hits = [i + 1 for i in range(len(lines)) if quote_is_at(lines, i + 1, frags, 0)
                or (head in window_text(lines, i + 1, 1) and quote_is_at(lines, i + 1, frags))]
    return min(hits, key=lambda n: abs(n - near)) if hits else None


def source_quote_problems(rows: list[tuple[str, str]], repo: pathlib.Path) -> tuple[list[str], int]:
    """Check each ``(row id, source cell)``; return ``(problems, how many were checkable)``.

    The caller asserts a floor on the second value (F81): a check that found nothing to check
    has not run.
    """
    problems: list[str] = []
    checkable = 0
    cache: dict[str, list[str]] = {}
    for rid, src in rows:
        pin = pinned_quote(src)
        if pin is None:
            continue
        path, line, frags = pin
        f = repo / path
        if not f.exists():
            continue                       # the existence check reports this
        lines = cache.setdefault(path, f.read_text(encoding="utf-8").splitlines())
        if not (1 <= line <= len(lines)):
            continue                       # the range check reports this
        checkable += 1
        if quote_is_at(lines, line, frags):
            continue
        found = locate(lines, frags, line)
        where = (f"the words are at {path}:{found}" if found
                 else "the words are nowhere in the file — the source was amended (§Findings)")
        problems.append(f"{rid}: {path}:{line} does not carry its quoted words within "
                        f"±{WINDOW} lines — {where}")
    return problems, checkable
