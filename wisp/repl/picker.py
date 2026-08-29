"""Interactive terminal picker — ↑/↓/Tab selection with type-to-filter.

Pure selection logic (``PickerState``) is fully testable without a tty;
the tty path uses POSIX cbreak mode for single-key input. Non-tty,
Windows, and accessible-mode callers get the numbered-list fallback so
piped sessions and tests keep working unchanged.

Mode-aware per AGENTS.md: honors ``is_accessible()`` from
``wisp.terminal_width`` — accessible mode never enters raw mode.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from wisp.colors import accent, dim

_SCROLL_PAGE = 10  # visible rows when the list is longer than this

# Keys normalized by classify_key
KEY_UP = "up"
KEY_DOWN = "down"
KEY_TAB = "tab"
KEY_SHIFT_TAB = "shift-tab"


# ── Pure selection logic (no I/O) ────────────────────────────────────


class PickerState:
    """Filterable, cursor-tracked view over a fixed option list.

    ``index`` is a position into ``filtered()``, not into ``options`` —
    callers map back with ``selected_index()``.
    """

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
        """Move the cursor with wrap-around within the filtered list."""
        n = len(self.filtered())
        if not n:
            return
        self.index = (self.index + delta) % n

    def add_char(self, ch: str) -> None:
        self.query += ch
        self.index = 0

    def backspace(self) -> None:
        if self.query:
            self.query = self.query[:-1]
            self.index = 0

    def select_digit(self, digit: str) -> Optional[int]:
        """Jump to 1-based position in the *filtered* list. None = out of range."""
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
        """Original-list index of the cursor position, or None when empty."""
        opt = self.selected_option()
        if opt is None:
            return None
        return self.options.index(opt)


def classify_key(seq: str) -> tuple[str, str]:
    """Classify a raw key sequence into (kind, payload).

    Pure so tests can drive the same dispatch the tty loop uses:
      ("move", "up"/"down"), ("tab", "tab"/"shift-tab"), ("enter", ""),
      ("cancel", ""), ("backspace", ""), ("digit", "3"), ("char", "x"),
      ("ignore", "")
    """
    if seq in ("\x1b[A", "k"):
        return ("move", KEY_UP)
    if seq in ("\x1b[B", "j"):
        return ("move", KEY_DOWN)
    if seq == "\x1b[Z":
        return ("tab", KEY_SHIFT_TAB)
    if seq == "\t":
        return ("tab", KEY_TAB)
    if seq in ("\r", "\n"):
        return ("enter", "")
    if seq in ("\x1b", "\x03", "\x04", "\x07"):
        return ("cancel", "")
    if seq in ("\x7f", "\x08"):
        return ("backspace", "")
    if seq.isdigit():
        return ("digit", seq)
    if seq.isprintable() and not seq.isspace():
        return ("char", seq)
    return ("ignore", "")


# ── Rendering helper (pure string building) ──────────────────────────


def render_menu(state: PickerState, title: str,
                descriptions: Optional[dict[str, str]] = None,
                mark: Optional[str] = None) -> str:
    """Build the full menu frame as one string (no printing)."""
    descriptions = descriptions or {}
    lines = [accent(title)]
    if state.query:
        lines.append(dim(f"  filter: {state.query!r}"))
    flt = state.filtered()
    if not flt:
        lines.append(dim("  (no matches — backspace to widen, esc to cancel)"))
        return "\n".join(lines)

    # Scroll window around the cursor
    start = 0
    if len(flt) > _SCROLL_PAGE:
        start = min(max(0, state.index - _SCROLL_PAGE // 2), len(flt) - _SCROLL_PAGE)
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
    lines.append(dim("  ↑/↓ or Tab: move · type: filter · enter: select · esc: cancel"))
    return "\n".join(lines)


# ── I/O layer ────────────────────────────────────────────────────────


def _raw_mode_supported() -> bool:
    return sys.platform != "win32"


def _read_key(stdin) -> str:
    """Read one logical key sequence from a tty in cbreak mode."""
    ch = stdin.read(1)
    if ch != "\x1b":
        return ch
    # Escape sequence — probe for follow-up bytes so a lone Esc press
    # still cancels promptly.
    import select
    seq = ch
    while len(seq) < 3:
        ready, _, _ = select.select([stdin], [], [], 0.05)
        if not ready:
            break
        seq += stdin.read(1)
    return seq


def select_option(
    title: str,
    options: Sequence[str],
    *,
    current: Optional[str] = None,
    descriptions: Optional[dict[str, str]] = None,
    stdin=None,
    stdout=None,
    interactive: Optional[bool] = None,
) -> Optional[int]:
    """Interactively pick one of ``options``; returns its index or None.

    ``interactive=False`` (or a non-tty stdin / accessible output mode /
    Windows) falls back to the classic numbered list + typed number, so
    piped sessions and tests behave exactly as before.
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

    state = PickerState(options)
    if current in options:
        state.index = list(options).index(current)

    if not interactive:
        return _select_numbered(state, title, descriptions, stdin, stdout)

    import termios
    import tty

    fd = stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return _run_interactive(state, title, descriptions, current, stdin, stdout)
    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def _run_interactive(state, title, descriptions, current, stdin, stdout) -> Optional[int]:
    last_render = ""
    while True:
        frame = render_menu(state, title, descriptions, mark=current)
        n_prev = len(last_render.splitlines())
        if n_prev:
            # Rewind over the previous frame and clear it
            stdout.write(f"\x1b[{n_prev}F\x1b[J")
        stdout.write(frame + "\n")
        stdout.flush()
        last_render = frame

        kind, payload = classify_key(_read_key(stdin))
        if kind == "move" and payload == KEY_UP:
            state.move(-1)
        elif kind == "move":
            state.move(1)
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
        # "ignore" → loop redraws unchanged (keeps the frame in sync)


def _select_numbered(state, title, descriptions, stdin, stdout) -> Optional[int]:
    """Fallback: print numbered list, read a typed number (old behavior)."""
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
        return state.options.index(flt[pos])
    return None
