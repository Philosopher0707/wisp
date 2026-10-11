"""An honest finish: a turn never ends in silence about its own state.

Three facts the user is owed even when the model says nothing useful (docs/harness/field-observations-2026-10-07.md, O-9 and O-10):
a code-changing turn that ended without a verification run that exited 0 is UNVERIFIED; a verification-looking command that did not count
should be named, with the reason, so the model can stop repeating it; and a round that ends with no answer at all is said to have ended that way.
Pure text and predicates, no I/O. The engine owns when each is used.
"""

from __future__ import annotations

from wisp.core.gates.secrets import scrub

NO_ANSWER_NOTE = "Harness note: the model produced no answer for this turn; the tool activity above is all there is."

_COMMAND_CAP = 120


def is_empty_answer(text: object) -> bool:
    return not (text.strip() if isinstance(text, str) else "")


def compose_empty_round_nudge() -> str:
    return (
        "[SYSTEM] Your last round produced no answer. In plain words: say what you did, what you found, and anything that is UNVERIFIED. "
        "If there is nothing to say, say that."
    )


def _one_line(command: str) -> str:
    text = " ".join(str(command or "").split())
    text = scrub(text[: _COMMAND_CAP * 4]).text
    return text if len(text) <= _COMMAND_CAP else text[: _COMMAND_CAP - 1] + "…"


def uncounted_reason(command: str, why: str) -> str:
    """The clause the floor's nudge uses when a verification-looking command ran but cannot count as verification."""
    return (
        f"your last test/lint-looking command (`{_one_line(command)}`) did not count as verification: {str(why or '').strip()}. "
        "Run the project's test or lint command on its own, so its own exit status decides the result"
    )


def unverified_note(*, failed: bool, uncounted: tuple[str, str] | None) -> str:
    """One line, written by the harness, for a code-changing turn that is ending without a verification run that exited 0."""
    if failed:
        why = "the most recent verification command failed"
    elif uncounted is not None:
        command, reason = uncounted
        why = f"the command `{_one_line(command)}` does not count as verification ({str(reason or '').strip()})"
    else:
        why = "no verification command has exited 0 since the code was changed"
    return f"Harness note: this turn changed code and the work is UNVERIFIED: {why}."
