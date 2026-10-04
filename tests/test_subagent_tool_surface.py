"""An unrestricted subagent is not handed the parent's skill menu.

`_build_system_prompt` deliberately omits the skills block for subagents ("the orchestrator's prompt already includes
relevant context"), yet the provider-bound tool list still carried every `skill__*` schema. On one real HOME that was
47 tools and 9,017 tokens, 45% of a 100-schema, 19.9k-token surface re-sent on every provider call, for tools whose
names the child's prompt never lists. Hidden-but-callable stays: only the schemas and the prompt's tool menu change,
and only for a subagent that has no explicit tool list. An explicit list is honoured exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.infra.extensions import ExtensionHost


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
        return [_schema("skill__alpha"), _schema("skill__beta"), _schema("mcp__srv__lookup")]


@pytest.fixture
def core(tmp_path: Path) -> WispAgentCore:
    host = ExtensionHost()
    host.register(_Ext())
    cfg = WispConfig().replace(workspace=str(tmp_path), model="m", provider="ollama", chars_per_token=4)
    return WispAgentCore(provider=None, config=cfg, extensions=host)


def _names(tools) -> set[str]:
    return {t["function"]["name"] for t in tools}


def _session(tmp_path: Path, **kw) -> dict:
    return {"id": "s", "workspace": str(tmp_path), "messages": [], **kw}


SUB = {"subagent_system_prompt": "You are a focused subagent."}


class TestTheProviderToolList:
    def test_the_parent_still_gets_every_tool(self, core, tmp_path):
        names = _names(core._provider_tools(_session(tmp_path)))
        assert {"skill__alpha", "skill__beta", "mcp__srv__lookup"} <= names

    def test_an_unrestricted_subagent_loses_only_the_skill_tools(self, core, tmp_path):
        parent = _names(core._provider_tools(_session(tmp_path)))
        child = _names(core._provider_tools(_session(tmp_path, allowed_tools=["all"], **SUB)))
        assert child == parent - {"skill__alpha", "skill__beta"}
        assert "mcp__srv__lookup" in child and "read_file" in child

    def test_a_subagent_with_no_tool_list_at_all_is_treated_the_same(self, core, tmp_path):
        child = _names(core._provider_tools(_session(tmp_path, **SUB)))
        assert not any(n.startswith("skill__") for n in child)

    def test_an_explicit_list_is_honoured_exactly_even_for_a_skill_tool(self, core, tmp_path):
        child = _names(core._provider_tools(_session(tmp_path, allowed_tools=["read_file", "skill__alpha"], **SUB)))
        assert child == {"read_file", "skill__alpha"}

    def test_the_child_surface_is_smaller(self, core, tmp_path):
        size = lambda s: len(json.dumps(core._provider_tools(s)))  # noqa: E731
        assert size(_session(tmp_path, allowed_tools=["all"], **SUB)) < size(_session(tmp_path))


class TestThePromptAgreesWithTheSchemas:
    """A tool the prompt names but the provider never receives is a hallucination seed (the reason the role filter
    exists), so the prompt's tool menu must follow the same rule."""

    def test_the_parent_prompt_lists_skill_tools(self, core, tmp_path):
        assert "skill__alpha" in core._build_system_prompt(_session(tmp_path))

    def test_the_unrestricted_subagent_prompt_does_not(self, core, tmp_path):
        prompt = core._build_system_prompt(_session(tmp_path, allowed_tools=["all"], **SUB))
        assert "skill__alpha" not in prompt and "skill__beta" not in prompt
        assert "mcp__srv__lookup" in prompt  # only skills are dropped

    def test_the_subagent_and_parent_prompts_are_cached_separately(self, core, tmp_path):
        child = core._build_system_prompt(_session(tmp_path, allowed_tools=["all"], **SUB))
        parent = core._build_system_prompt(_session(tmp_path))
        assert "skill__alpha" in parent and "skill__alpha" not in child
