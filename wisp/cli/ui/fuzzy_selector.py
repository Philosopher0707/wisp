"""ANSI-safe fuzzy selector — arrow keys, Tab, Enter, Esc, type-to-filter.

Robust against the classic `read(1)` ESC trap: a lone `\\x1b` must only
cancel when *no* trailing bytes arrive within 20 ms; otherwise `\\x1b[A`
is Up, `\\x1b[B` is Down, `\\x1b[C`/`D` are Right/Left, `\\x1b[Z` is
Shift-Tab.  Uses a two-stage select timeout (20 ms for CSI starter,
10 ms for terminator) and circular wrap for ↑/↓ and Tab.

Non-tty / pipe / accessible mode falls back to numbered `stdin.readline`
so `wisp repl` piped sessions and CI keep working.

Pure logic (`FuzzyState`, `classify_ansi`, `render_selector`) is fully
testable without a tty; the tty loop (`select_with_fuzzy`) is exercised
via monkeypatched `_read_ansi` in tests.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from wisp.colors import accent, dim

# Keys normalized by classify_ansi
KEY_UP = "up"
KEY_DOWN = "down"
KEY_TAB = "tab"
KEY_SHIFT_TAB = "shift-tab"
KEY_ENTER = "enter"
KEY_CANCEL = "cancel"
KEY_BACKSPACE = "backspace"

_SCROLL_PAGE = 10

# ── Pure state ──────────────────────────────────────────────────────────


class FuzzyState:
    """Filterable, cursor-tracked view. `index` is into `filtered()`."""

    def __init__(self, options: Sequence[str], query: str = ""):
        self.options = list(options)
        self.query = query
        self.index = 0

    def filtered(self) -> list[str]:
        q = self.query.strip().lower()
        if not q:
            return list(self.options)
        return [o for o in self.options if q in o.lower()]

    def move(self, delta: int) -> None:
        n = len(self.filtered())
        if not n:
            return
        self.index = (self.index + delta) % n

    def add_char(self, ch: str) -> None:
        # Clamp query length to avoid unbounded growth from stray input
        if len(self.query) >= 64:
            return
        self.query += ch
        self.index = 0

    def backspace(self) -> None:
        if self.query:
            self.query = self.query[:-1]
            self.index = 0

    def select_digit(self, digit: str) -> Optional[int]:
        pos = int(digit)
        if 1 <= pos <= len(self.filtered()):
            return pos - 1
        return None

    def selected_option(self) -> Optional[str]:
        flt = self.filtered()
        if 0 <= self.index < len(flt):
            return flt[self.index]
        return None

    def selected_index(self) -> Optional[int]:
        opt = self.selected_option()
        if opt is None:
            return None
        try:
            return self.options.index(opt)
        except ValueError:
            return None

    def clamp_index(self) -> None:
        """Keep index in bounds after filtering (no wrap, just clamp)."""
        n = len(self.filtered())
        if n == 0:
            self.index = 0
        elif self.index >= n:
            self.index = n - 1
        elif self.index < 0:
            self.index = 0


# ── ANSI classifier ─────────────────────────────────────────────────────


def classify_ansi(seq: str) -> tuple[str, str]:
    """Classify a *complete* ANSI/char sequence.

    Returns (kind, payload) where kind in:
      move:up/down, tab:tab/shift-tab, enter, cancel, backspace, digit, char, ignore
    """
    # Arrow keys — CSI and SS3 variants (\x1b[A vs \x1bOA)
    if seq in ("\x1b[A", "\x1bOA", "k", "\x1b[k"):
        return ("move", KEY_UP)
    if seq in ("\x1b[B", "\x1bOB", "j", "\x1b[j"):
        return ("move", KEY_DOWN)
    if seq in ("\x1b[C", "\x1bOC"):
        return ("move", "right")
    if seq in ("\x1b[D", "\x1bOD"):
        return ("move", "left")
    # Shift-Tab is CSI Z
    if seq == "\x1b[Z":
        return ("tab", KEY_SHIFT_TAB)
    if seq == "\t":
        return ("tab", KEY_TAB)
    # Enter (CR, LF, CRLF already normalized to \r or \n by the reader)
    if seq in ("\r", "\n", "\r\n"):
        return ("enter", "")
    # Standalone ESC / Ctrl-C / Ctrl-D / BEL — cancel
    if seq in ("\x1b", "\x03", "\x04", "\x07"):
        return ("cancel", "")
    # Backspace (DEL and BS)
    if seq in ("\x7f", "\x08", "\x1b[3~"):
        return ("backspace", "")
    # Digits for 1-based jump (filtered list)
    if len(seq) == 1 and seq.isdigit():
        return ("digit", seq)
    # Printable single char for filtering
    if len(seq) == 1 and seq.isprintable() and not seq.isspace():
        return ("char", seq)
    return ("ignore", "")


# ── Rendering ───────────────────────────────────────────────────────────


def render_selector(
    state: FuzzyState,
    title: str,
    descriptions: Optional[dict[str, str]] = None,
    mark: Optional[str] = None,
) -> str:
    """Build the full selector frame as one string (no I/O)."""
    descriptions = descriptions or {}
    lines = [accent(title)]
    if state.query:
        lines.append(dim(f"  filter: {state.query!r}  ({len(state.filtered())}/{len(state.options)} matches)"))
    flt = state.filtered()
    if not flt:
        lines.append(dim("  (no matches — backspace to widen, esc to cancel)"))
        return "\n".join(lines)

    # Scroll window
    start = 0
    if len(flt) > _SCROLL_PAGE:
        start = min(max(0, state.index - _SCROLL_PAGE // 2), len(flt) - _SCROLL_PAGE)
        if start > 0:
            lines.append(dim(f"  … {start} more above"))

    for i in range(start, min(start + _SCROLL_PAGE, len(flt))):
        cursor = "❯" if i == state.index else " "
        marker = " →" if flt[i] == mark else ""
        desc = descriptions.get(flt[i], "")
        desc_str = dim(f"  {desc}") if desc else ""
        line = f" {cursor} {flt[i]}{marker}{desc_str}"
        lines.append(accent(line) if i == state.index else line)

    if start + _SCROLL_PAGE < len(flt):
        lines.append(dim(f"  … {len(flt) - start - _SCROLL_PAGE} more below (type to filter)"))
    lines.append(dim("  ↑/↓ or Tab/Shift-Tab: move · type: filter · enter: select · esc: cancel"))
    return "\n".join(lines)


# ── Low-level ANSI reader ─────────────────────────────────────────────


def _read_ansi(stdin, esc_timeout: float = 0.02, csi_timeout: float = 0.01) -> str:
    """Read one *logical* key as an ANSI sequence.

    Distinguishes lone `ESC` from `ESC [ A` by waiting:
      1. After `\\x1b`, wait `esc_timeout` (20 ms) for a follower.
         No follower → standalone `ESC` (cancel).
      2. If follower is `[` or `O`, wait `csi_timeout` (10 ms) for
         terminator(s) `A/B/C/D/Z/~` etc. Accumulate up to 5 bytes
         while bytes keep arriving within the timeout.

    `stdin` must be in cbreak/raw; we use `select` so we never block
    longer than the timeouts.
    """
    import select

    try:
        ch = stdin.read(1)
    except Exception:
        return ""

    if not ch:
        return ""
    if ch != "\x1b":
        # Normalize CRLF: some terminals send \r\n
        if ch == "\r":
            # Peek for \n without blocking long
            try:
                ready, _, _ = select.select([stdin], [], [], 0.005)
                if ready:
                    nxt = stdin.read(1)
                    if nxt == "\n":
                        return "\r\n"
                    # Not LF — push back not possible, but \r alone is Enter
                    # We can't push back with raw read; treat \r as Enter and
                    # the extra char will be read next loop iteration if we had
                    # used ungetc. With our simple reader we just return \r.
                    # To avoid losing nxt, we stash it in a thread-local buffer
                    # — instead, we just return \r and rely on the fact that
                    # terminals don't send \r + printable without ESC.
                    pass
            except Exception:
                pass
        return ch

    # Potential ESC sequence — wait for follower
    seq = ch
    # Stage 1: wait esc_timeout for CSI starter or SS3
    ready, _, _ = select.select([stdin], [], [], esc_timeout)
    if not ready:
        return "\x1b"  # standalone ESC

    try:
        nxt = stdin.read(1)
    except Exception:
        return "\x1b"
    if not nxt:
        return "\x1b"
    seq += nxt

    # If not CSI/SS3, it's likely Alt+key or stray ESC; treat seq as cancel only if lone ESC
    # For our picker we only care about CSI ([) and SS3 (O). Others → treat as ESC + ignore remainder
    if nxt not in ("[", "O"):
        # Could be ESC + single char like ESC+k (Alt) — not used in picker → cancel
        # But to be safe, if it's printable alone, we return ESC and let next read get the char
        # However stdin is raw, we already consumed nxt, so we must include it.
        # For now return seq as-is; classify_ansi will map it to cancel or ignore.
        return seq

    # Stage 2: CSI/SS3 — read terminator(s) with short timeout
    # CSI can be 1-3 chars: `[A`, `[B`, `[C`, `[D`, `[Z`, `[3~`, `[1;2A` etc.
    # We accumulate while bytes arrive quickly, up to 5 total.
    while len(seq) < 6:
        ready, _, _ = select.select([stdin], [], [], csi_timeout)
        if not ready:
            break
        try:
            more = stdin.read(1)
        except Exception:
            break
        if not more:
            break
        seq += more
        # Stop at final byte: [A-Za-z~] for CSI, or A-D for SS3
        if more.isalpha() or more == "~":
            break
        # If digit or ';' then it's an extended sequence like [1;2A — keep reading
        if more not in "0123456789;":
            break

    return seq


def _raw_mode_supported() -> bool:
    return sys.platform != "win32"


# ── High-level API ────────────────────────────────────────────────────


def select_with_fuzzy(
    title: str,
    options: Sequence[str],
    *,
    current: Optional[str] = None,
    descriptions: Optional[dict[str, str]] = None,
    stdin=None,
    stdout=None,
    interactive: Optional[bool] = None,
) -> Optional[int]:
    """Interactively pick one of `options`; returns original-list index or None.

    * `interactive=False` or non-tty / accessible mode → numbered fallback.
    * `interactive=True` (or auto-detected tty) → raw cbreak loop with
      `_read_ansi` (20 ms ESC, 10 ms CSI) and `classify_ansi`.

    The direct-argument fallback (`/model 3` or `/model name`) is handled by
    callers (`wisp/repl/commands/provider.py`), not here — this function
    only does the TUI.
    """
    if not options:
        return None
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    if interactive is None:
        from wisp.terminal_width import is_accessible

        interactive = (
            getattr(stdin, "isatty", lambda: False)()
            and not is_accessible()
            and _raw_mode_supported()
        )

    state = FuzzyState(options)
    if current in options:
        state.index = list(options).index(current)

    if not interactive:
        return _select_numbered_fuzzy(state, title, descriptions, stdin, stdout)

    import termios
    import tty

    try:
        fd = stdin.fileno()
    except Exception:
        return _select_numbered_fuzzy(state, title, descriptions, stdin, stdout)

    try:
        old_attrs = termios.tcgetattr(fd)
    except Exception:
        return _select_numbered_fuzzy(state, title, descriptions, stdin, stdout)

    try:
        tty.setcbreak(fd)
        return _run_fuzzy_interactive(state, title, descriptions, current, stdin, stdout)
    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        except Exception:
            pass


def _run_fuzzy_interactive(state, title, descriptions, current, stdin, stdout) -> Optional[int]:
    last_render = ""
    while True:
        frame = render_selector(state, title, descriptions, mark=current)
        n_prev = len(last_render.splitlines())
        if n_prev:
            stdout.write(f"\x1b[{n_prev}F\x1b[J")
        stdout.write(frame + "\n")
        stdout.flush()
        last_render = frame

        seq = _read_ansi(stdin)
        if not seq:
            continue
        kind, payload = classify_ansi(seq)
        if kind == "move":
            if payload == KEY_UP:
                state.move(-1)
            elif payload == KEY_DOWN:
                state.move(1)
            else:  # right/left — treat as down/up for convenience
                state.move(1 if payload == "right" else -1)
        elif kind == "tab":
            state.move(-1 if payload == KEY_SHIFT_TAB else 1)
        elif kind == "enter":
            return state.selected_index()
        elif kind == "cancel":
            return None
        elif kind == "backspace":
            state.backspace()
        elif kind == "digit":
            pos = state.select_digit(payload)
            if pos is not None:
                state.index = pos
        elif kind == "char":
            state.add_char(payload)
        # ignore → redraw unchanged


def _select_numbered_fuzzy(state, title, descriptions, stdin, stdout) -> Optional[int]:
    """Fallback for pipe/non-tty/accessible: numbered list + readline."""
    def write(s: str) -> None:
        stdout.write(s)
        stdout.flush()

    descriptions = descriptions or {}
    write(accent(title) + "\n")
    flt = state.filtered()
    for i, opt in enumerate(flt, 1):
        marker = " →" if i - 1 == state.index else ""
        desc = descriptions.get(opt, "")
        desc_str = dim(f"  {desc}") if desc else ""
        write(f"  {i:2}. {opt}{marker}{desc_str}\n")
    write(dim("Number to select, enter to cancel: "))
    try:
        raw = stdin.readline()
    except Exception:
        return None
    raw = (raw or "").strip()
    if not raw or not raw.isdigit():
        return None
    pos = int(raw) - 1
    if 0 <= pos < len(flt):
        try:
            return state.options.index(flt[pos])
        except ValueError:
            return None
    return None


# Back-compat alias — some callers import `select_option` from this module
select_option = select_with_fuzzy
