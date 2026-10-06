"""`wisp tools`: what an agent is offered, and why a tool is missing. Read-only; changes nothing."""
from __future__ import annotations

import json

from wisp.cli import tools_cmd


def _run(capsys, *argv: str):
    code = tools_cmd.main(list(argv))
    return code, capsys.readouterr().out


def test_a_researcher_child_in_auto_edit_shows_why_run_bash_is_missing(capsys):
    code, out = _run(capsys, "--role", "researcher", "--mode", "auto_edit")
    assert code == 0
    assert "web_fetch" in out and "grep" in out
    lines = out.splitlines()
    i = next(i for i, ln in enumerate(lines) if ln.strip().startswith("[mode]") and "approv" in ln.lower())
    assert "run_bash" in lines[i + 1], "the tool is listed under the reason that removed it"


def test_a_child_role_says_what_it_cannot_show_about_extension_tools(capsys):
    code, out = _run(capsys, "--role", "generalist", "--mode", "auto_edit")
    assert code == 0 and "declared `tool_risk: read`" in out and "never skill tools" in out
    code, out = _run(capsys, "--role", "parent")
    assert "inherits" not in out


def test_the_parent_under_the_core_profile_names_the_switch(capsys):
    code, out = _run(capsys, "--role", "parent", "--profile", "core")
    assert code == 0 and "WISP_TOOL_PROFILE=full" in out


def test_json_output_is_structured(capsys):
    code, out = _run(capsys, "--role", "researcher", "--mode", "auto_edit", "--json")
    data = json.loads(out)
    assert code == 0 and "web_fetch" in data["offered"]
    absent = {a["tool"]: a for a in data["absent"]}
    assert absent["run_bash"]["stage"] == "mode"


def test_an_unknown_role_is_an_error_not_a_guess(capsys):
    code, out = _run(capsys, "--role", "wizard")
    assert code == 2 and "unknown role" in out.lower()


def test_wisp_tools_is_a_registered_subcommand():
    from wisp.__main__ import _SUBCOMMAND_NAMES, print_subcommand_help

    assert "tools" in _SUBCOMMAND_NAMES and print_subcommand_help("tools")
