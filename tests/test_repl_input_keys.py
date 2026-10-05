"""The REPL input line: keys that are shortcuts only when they have something to do, and one history writer.

Real prompt_toolkit session on piped input (no mocks of the library): the bugs were in how our key bindings
interact with the real buffer, which a mock cannot show.
"""

from __future__ import annotations

import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from wisp.cli import repl
from wisp.cli.ui.blocks import ScreenModel


def _type(keys: str, model: ScreenModel | None) -> str:
    """Feed ``keys`` to a real session using our bindings; return what the prompt returns."""
    box: dict[str, str] = {}

    def run() -> None:
        with create_pipe_input() as pipe:
            session: PromptSession[str] = PromptSession(
                input=pipe, output=DummyOutput(), key_bindings=repl.build_key_bindings(model))
            pipe.send_text(keys)
            box["text"] = session.prompt("> ")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "prompt did not return"
    return box["text"]


def test_v_is_a_letter_when_there_is_no_diff_to_page():
    assert _type("view the logs\r", ScreenModel()) == "view the logs"


def test_space_is_a_space_when_there_is_nothing_to_expand():
    assert _type("   hello\r", ScreenModel()) == "   hello"


def test_text_after_the_first_character_is_never_touched():
    assert _type("a v b\r", ScreenModel()) == "a v b"


def test_space_still_expands_the_newest_collapsible_block(capsys):
    model = ScreenModel()
    model.append("thought", {"text": "deep thought"})  # thoughts start collapsed
    assert _type(" hi\r", model) == "hi"  # the space was consumed as the expand shortcut
    assert any(b.kind == "log" for b in model.blocks), "expanding appends the full text as a log block"


def test_v_still_opens_the_pager_for_a_pageable_diff(monkeypatch):
    model = ScreenModel()
    model.append("diff", {"files": [("a.py", "x\n" * 200, "y\n" * 200)]})
    opened: list[object] = []
    monkeypatch.setattr("wisp.cli.ui.pager.show_diff", lambda pairs: opened.append(pairs))
    monkeypatch.setattr("wisp.cli.ui.pager.should_page_diff", lambda pairs: True)
    assert _type("v\r", model) == ""
    assert opened, "v on an empty prompt opens the diff pager"


# ── history: prompt_toolkit's FileHistory is the only writer ────────────────────────────────────────────────


def test_readline_does_not_overwrite_history_owned_by_prompt_toolkit(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    hist.write_text("\n# 2026-10-05 08:00:00.000000\n+remember me\n")
    monkeypatch.setenv("WISP_HISTORY_FILE", str(hist))
    before = hist.read_bytes()
    repl.own_history(hist)
    try:
        assert repl.load_command_history() is False
        assert repl.save_command_history() is False
    finally:
        repl.own_history(None)
    assert hist.read_bytes() == before, "readline's empty in-memory history must not wipe the file"


def test_readline_history_still_works_when_prompt_toolkit_is_not_in_use(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_HISTORY_FILE", str(tmp_path / "history"))
    repl.own_history(None)
    assert repl.save_command_history() is True
