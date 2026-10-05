"""The default tool profile: 8 core tools plus grep, glob and rewind.

Evidence for the set (wisp's own audit logs, 18,132 tool calls): seven tools were 95% of all calls. Everything else
cost about 8,200 schema tokens on every provider call for under 2% of use. The profile narrows what is OFFERED, not
what is callable: a tool outside it is still in the registry, still authorised the same way, and a role subagent with
an explicit tool list still gets exactly that list. Extension tools (MCP servers, skills) are the operator's own
configuration and are not touched by the profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.infra.extensions import ExtensionHost
from wisp.tools.profile import CORE_TOOLS, DEFAULT_PROFILE, builtin_names_for, parse_profile
from wisp.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS

CORE = {
    "read_file", "write_file", "edit_file", "edit_file_multi", "run_bash", "run_tests",
    "list_files", "grep", "glob", "search_symbols", "rewind",
}
BUILTIN = {t["function"]["name"] for t in TOOL_SCHEMAS}


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": f"{name} tool. More text.",
                                             "parameters": {"type": "object", "properties": {}}}}


class _Ext:
    name = "fake"

    def start(self):
        pass

    def stop(self):
        pass

    def intercept(self, event):
        return {"action": "allow"}

    def tools(self):
        return [_schema("skill__alpha"), _schema("mcp__srv__lookup")]


def _core(tmp_path: Path, **cfg) -> WispAgentCore:
    host = ExtensionHost()
    host.register(_Ext())
    config = WispConfig().replace(workspace=str(tmp_path), model="m", provider="ollama", chars_per_token=4, **cfg)
    return WispAgentCore(provider=None, config=config, extensions=host)


def _names(tools) -> set[str]:
    return {t["function"]["name"] for t in tools}


def _session(tmp_path: Path, **kw) -> dict:
    return {"id": "s", "workspace": str(tmp_path), "messages": [], **kw}


SUB = {"subagent_system_prompt": "You are a focused subagent."}

_MENU_HEAD = "\n## Tools available\n- "  # the real menu; the base prompt also mentions the phrase in prose


def _menu_block(prompt: str) -> str:
    start = prompt.index(_MENU_HEAD) + 1
    end = prompt.find("\n\n", start)
    return prompt[start: end if end != -1 else len(prompt)]


def _outside_the_menu(prompt: str) -> str:
    start = prompt.index(_MENU_HEAD) + 1
    end = prompt.find("\n\n", start)
    return prompt[:start] + (prompt[end:] if end != -1 else "")


class TestTheProfileDefinition:
    def test_the_core_set_is_exactly_eleven_named_tools(self):
        assert set(CORE_TOOLS) == CORE and len(CORE_TOOLS) == 11

    def test_every_core_tool_exists_in_the_registry(self):
        assert CORE <= BUILTIN and CORE <= set(TOOL_IMPLS)

    def test_core_is_the_default(self):
        assert DEFAULT_PROFILE == "core"

    @pytest.mark.parametrize("raw,expected", [("core", "core"), ("FULL", "full"), (" full ", "full"), ("", "core"),
                                              ("everything", "core"), (None, "core"), (3, "core")])
    def test_parsing_fails_to_the_smaller_surface(self, raw, expected):
        assert parse_profile(raw) == expected

    def test_full_means_no_narrowing(self):
        assert builtin_names_for("full") is None
        assert builtin_names_for("core") == frozenset(CORE)


class TestTheConfigSetting:
    def test_the_default_is_core(self, monkeypatch):
        monkeypatch.delenv("WISP_TOOL_PROFILE", raising=False)
        assert WispConfig().tool_profile == "core"

    def test_the_environment_can_restore_the_full_surface(self, monkeypatch):
        monkeypatch.setenv("WISP_TOOL_PROFILE", "full")
        assert WispConfig().tool_profile == "full"

    def test_an_unknown_value_falls_back_to_core(self, monkeypatch):
        monkeypatch.setenv("WISP_TOOL_PROFILE", "wide-open")
        assert WispConfig().tool_profile == "core"


class TestWhatTheProviderIsOffered:
    def test_the_parent_gets_the_core_builtins_and_keeps_its_extension_tools(self, tmp_path):
        names = _names(_core(tmp_path)._provider_tools(_session(tmp_path)))
        assert names & BUILTIN == CORE
        assert {"skill__alpha", "mcp__srv__lookup"} <= names  # the operator's own configuration is untouched

    def test_full_restores_every_builtin(self, tmp_path):
        names = _names(_core(tmp_path, tool_profile="full")._provider_tools(_session(tmp_path)))
        assert names & BUILTIN == BUILTIN

    def test_an_unrestricted_subagent_gets_the_core_set_and_mcp_but_not_skills(self, tmp_path):
        names = _names(_core(tmp_path)._provider_tools(_session(tmp_path, allowed_tools=["all"], **SUB)))
        assert names & BUILTIN == CORE
        assert "mcp__srv__lookup" in names and "skill__alpha" not in names

    def test_an_explicit_role_list_is_honoured_exactly_whatever_the_profile(self, tmp_path):
        want = ["read_file", "git_commit", "spawn", "lsp_hover"]  # none of the last three are in the core set
        names = _names(_core(tmp_path)._provider_tools(_session(tmp_path, allowed_tools=want, **SUB)))
        assert names == set(want)

    def test_the_surface_is_much_smaller(self, tmp_path):
        size = lambda c: len(json.dumps(c._provider_tools(_session(tmp_path))))  # noqa: E731
        assert size(_core(tmp_path)) < 0.4 * size(_core(tmp_path, tool_profile="full"))

    def test_hidden_tools_stay_in_the_registry_and_callable(self, tmp_path):
        """The profile hides tools from the model's menu; it does not delete them. Internal and CLI flows call by name."""
        from wisp.tools.registry import execute_tool

        _core(tmp_path)
        assert {"git_status", "spawn", "fanout", "remember"} <= set(TOOL_IMPLS)
        out = execute_tool("git_status", {}, str(tmp_path))
        assert "git_status" in out  # it ran (the envelope names the tool), though not offered to the model


class TestThePromptAgreesWithTheSchemas:
    def _menu(self, prompt: str) -> set[str]:
        block = _menu_block(prompt)
        return {ln[2:].split(":", 1)[0] for ln in block.splitlines() if ln.startswith("- ")}

    def test_the_parent_menu_lists_exactly_the_offered_builtins(self, tmp_path):
        core = _core(tmp_path)
        menu = self._menu(core._build_system_prompt(_session(tmp_path)))
        assert menu & BUILTIN == CORE

    def test_full_lists_everything(self, tmp_path):
        menu = self._menu(_core(tmp_path, tool_profile="full")._build_system_prompt(_session(tmp_path)))
        assert menu & BUILTIN == BUILTIN

    def test_a_role_subagent_menu_is_unchanged(self, tmp_path):
        menu = self._menu(_core(tmp_path)._build_system_prompt(_session(tmp_path, allowed_tools=["read_file", "git_commit"], **SUB)))
        assert menu & BUILTIN == {"read_file", "git_commit"}

    def test_the_two_profiles_do_not_share_a_cached_prompt(self, tmp_path):
        a = _core(tmp_path)._build_system_prompt(_session(tmp_path))
        b = _core(tmp_path, tool_profile="full")._build_system_prompt(_session(tmp_path))
        assert a != b and "spawn" in b and "- spawn:" not in a


class TestThePromptProseDoesNotPointAtHiddenTools:
    """A tool the prose tells the model to use but the provider never offers is the hallucination seed the role filter
    exists to kill. Tool names are checked outside the menu, in the parent prompt under the default profile."""

    def test_no_instruction_names_a_tool_that_is_not_offered(self, tmp_path):
        import re

        core = _core(tmp_path)
        prompt = core._build_system_prompt(_session(tmp_path))
        prose = _outside_the_menu(prompt)
        offered = _names(core._provider_tools(_session(tmp_path)))
        # Unambiguous identifiers only: `diagnose` is also an ordinary verb ("diagnose the error"), so names without an
        # underscore are checked only when they are not common words.
        hidden = sorted(n for n in BUILTIN - offered if "_" in n or n in {"fanout"})
        mentioned = [n for n in hidden if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", prose)]
        assert not mentioned, f"the prompt tells the model about tools it was not given: {mentioned}"

    def test_full_keeps_the_prose_exactly_as_it_was(self, tmp_path):
        prompt = _core(tmp_path, tool_profile="full")._build_system_prompt(_session(tmp_path))
        assert "## Subagent protocol" in prompt and "run lsp_diagnostics on changed files" in prompt


class TestAdaptSystemProse:
    def test_the_subagent_section_goes_when_no_subagent_tool_is_offered(self):
        from wisp.context_assembler import DEFAULT_BASE_SYSTEM, adapt_system_prose

        out = adapt_system_prose(DEFAULT_BASE_SYSTEM, frozenset(CORE))
        assert "Subagent protocol" not in out and "fanout" not in out and "subagent_wait" not in out
        assert "Tool schemas are generated at runtime" in out  # the paragraph after it survives
        assert "\n\n\n" not in out

    def test_it_stays_when_a_subagent_tool_is_offered(self):
        from wisp.context_assembler import DEFAULT_BASE_SYSTEM, adapt_system_prose

        assert "Subagent protocol" in adapt_system_prose(DEFAULT_BASE_SYSTEM, frozenset(CORE | {"fanout"}))

    def test_lsp_instructions_are_reworded_only_when_lsp_is_hidden(self):
        from wisp.context_assembler import DEFAULT_BASE_SYSTEM, adapt_system_prose

        hidden = adapt_system_prose(DEFAULT_BASE_SYSTEM, frozenset(CORE))
        shown = adapt_system_prose(DEFAULT_BASE_SYSTEM, frozenset(CORE | {"lsp_diagnostics", "fanout"}))
        assert "lsp_diagnostics" not in hidden and "run the project's tests or linter" in hidden
        assert "lsp_diagnostics" in shown

    def test_it_is_a_no_op_on_text_that_names_nothing_hidden(self):
        from wisp.context_assembler import adapt_system_prose

        assert adapt_system_prose("plain text", frozenset(CORE)) == "plain text"
