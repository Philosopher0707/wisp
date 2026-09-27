"""Detect a reply that announces its next step instead of concluding.

A model that writes "Let me reproduce the issue first." and calls no tool has not
finished; treating that text as the final answer ends the turn with the work undone
and a success claim nobody earned. The engine asks this module before `done` and, for a
bounded number of rounds, sends the model back to either act or conclude.
"""

from __future__ import annotations

import re

_ANNOUNCE = re.compile(
    r"^(?:(?:now|next|first|ok(?:ay)?|so|then)[,:]?\s+)?"
    r"(?:let me(?!\s+(?:know|summari[sz]e))|let's|i'll|i will|i'm going to|i am going to)\b",
    re.I,
)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


def announces_next_step(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.endswith(":"):
        return True
    last_sentence = _SENTENCE_BREAK.split(stripped)[-1].strip().lstrip("*_-> ")
    return bool(_ANNOUNCE.match(last_sentence))


def compose_continue_nudge(text: str) -> str:
    tail = (text or "").strip().splitlines()[-1][:160] if (text or "").strip() else ""
    return (
        "[HARNESS] Your last message announced a next step "
        f'("{tail}") but made no tool call, so the turn would end with that step '
        "undone. Make the tool call now, or, if the task is finished, reply with your "
        "final answer."
    )
