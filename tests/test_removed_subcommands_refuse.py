"""A subcommand removed in M0 is refused with a pointer, never sent to the model as a prompt.

`wisp <word>` treats an unknown word as a prompt. After M0 removed `server`, `tui` and `acp`
(docs/removed/2026-10-14-m0-front-ends.md), `wisp server` started a model turn with the prompt
"server": a request, a cost, and no explanation to someone who typed a command they used yesterday.
"""

from __future__ import annotations

import sys

import pytest

import wisp.__main__ as cli


@pytest.mark.parametrize("word", ["server", "tui", "acp"])
def test_a_removed_subcommand_is_refused_and_no_turn_starts(word, monkeypatch, capsys):
    started = []
    monkeypatch.setattr("wisp.entry.run_mode", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["wisp", word, "--port", "9000"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert started == [], f"`wisp {word}` reached run_mode: it would have been sent to the model"
    err = capsys.readouterr().err
    assert f"`wisp {word}` was removed" in err and "docs/removed/2026-10-14-m0-front-ends.md" in err


def test_control_a_word_that_was_never_a_subcommand_is_still_a_prompt(monkeypatch):
    started = []
    monkeypatch.setattr("wisp.entry.run_mode", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["wisp", "serverless"])
    cli.main()
    assert started and started[0][1]["prompt"] == "serverless"
