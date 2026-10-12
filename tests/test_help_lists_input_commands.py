"""`/help` lists the commands the prompt itself handles, and completion offers what those commands accept.

`/multiline` and `/converge` are intercepted by the input loop before the dispatcher sees the line (`wisp/cli/repl.py`, and `/multiline` again in `wisp/entry.py`),
so they are in no registry and `/help`, which lists the registries, never showed them. `/help converge` answered "no such command". Separately, completion offered
`/multiline on|off` while the command accepts only `single` or `multi`, so taking the suggestion failed with "Unknown mode 'on'".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wisp.cli.dispatcher import INPUT_LOOP_COMMANDS, CommandResult, Dispatcher, ReplContext

REPO = Path(__file__).resolve().parents[1]


def _help(args: str = "") -> str:
    out: list[str] = []
    ctx = ReplContext(runtime=None, transport=None, session={}, config={"model": "m", "provider": "mock"}, out=out)  # type: ignore[arg-type]
    assert Dispatcher().dispatch(ctx, f"/help {args}".strip()) is CommandResult.CONSUMED
    return "\n".join(out)


def _intercepted() -> set[str]:
    """Every command an input loop handles before dispatch, read from the source so a new one cannot be added silently."""
    names: set[str] = set()
    for rel in ("wisp/cli/repl.py", "wisp/entry.py"):
        names.update(re.findall(r"""prompt\s*==\s*["']/([a-z][a-z0-9_-]*)["']""", (REPO / rel).read_text(encoding="utf-8")))
    return names


def test_the_source_scan_finds_the_known_ones():
    assert {"multiline", "converge"} <= _intercepted()


def test_every_intercepted_command_is_described():
    assert _intercepted() <= set(INPUT_LOOP_COMMANDS)


@pytest.mark.parametrize("name", sorted(INPUT_LOOP_COMMANDS))
def test_help_lists_it(name):
    text = _help()
    assert re.search(rf"^\s*/{name}\s+\S", text, re.M), text


@pytest.mark.parametrize("name", sorted(INPUT_LOOP_COMMANDS))
@pytest.mark.parametrize("spelling", ["{}", "/{}"])
def test_help_for_one_gives_its_usage(name, spelling):
    text = _help(spelling.format(name))
    assert "no such command" not in text
    assert INPUT_LOOP_COMMANDS[name][1] in text


def test_the_converge_usage_is_the_one_the_command_prints():
    from wisp.autonomous_repl import _USAGE

    assert INPUT_LOOP_COMMANDS["converge"][1] in _USAGE


def test_an_unknown_name_is_still_unknown():
    assert "no such command" in _help("convergee")


def test_completion_offers_exactly_the_modes_multiline_accepts():
    from wisp.cli.repl import ReplRunner
    from wisp.repl.completion import COMMAND_COMPLETERS

    assert sorted(COMMAND_COMPLETERS["multiline"]("")) == sorted(ReplRunner._VALID_MODES)


def test_the_entry_loop_accepts_the_same_modes():
    source = (REPO / "wisp/entry.py").read_text(encoding="utf-8")
    match = re.search(r"_VALID_MODES\s*=\s*\(([^)]*)\)", source)
    assert match and sorted(re.findall(r"""["'](\w+)["']""", match.group(1))) == ["multi", "single"]
