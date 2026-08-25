"""CI guard: the runtime-generated tool menu must match live registries.

The system prompt's '## Tools available' block is GENERATED from
TOOL_SCHEMAS + extension tools by WispAgentCore._build_tools_block().
These tests pin that generation so drift stays impossible:

- every registered schema tool is announced, exactly once
- no phantom names (the old hardcoded dict advertised 'spawn_subagent',
  which never existed — this class of bug must stay dead)
- every announced tool is dispatchable
- every line carries a non-empty description
"""

import re

import pytest

from wisp.core.engine import WispAgentCore
from wisp.tools import TOOL_SCHEMAS, TOOL_IMPLS


def _generated_block() -> str:
    core = WispAgentCore()  # extensions=None → built-in tools only
    return core._build_tools_block()


def _extract_tools_from_prompt(block: str) -> set[str]:
    match = re.search(r"## Tools available\n(.*?)(?:\n\(|\n## |\Z)", block, re.DOTALL)
    assert match, "No '## Tools available' section found in generated block"
    return {m.group(1) for m in re.finditer(r"- (\S+):", match.group(1))}


class TestSystemPromptToolSync:
    def test_generated_prompt_matches_schema_exactly(self):
        prompt_tools = _extract_tools_from_prompt(_generated_block())
        schema_tools = {s["function"]["name"] for s in TOOL_SCHEMAS}

        missing_in_prompt = schema_tools - prompt_tools
        extra_in_prompt = prompt_tools - schema_tools

        assert missing_in_prompt == set(), (
            f"Tools registered in TOOL_SCHEMAS but MISSING from prompt block: "
            f"{sorted(missing_in_prompt)}"
        )
        assert extra_in_prompt == set(), (
            f"Phantom tools announced in prompt but NOT in TOOL_SCHEMAS: "
            f"{sorted(extra_in_prompt)}"
        )

    def test_every_schema_tool_is_dispatchable(self):
        for schema in TOOL_SCHEMAS:
            name = schema["function"]["name"]
            assert name in TOOL_IMPLS, (
                f"Schema tool '{name}' has no TOOL_IMPLS entry — it would "
                f"crash at dispatch time"
            )

    def test_no_legacy_phantom_names(self):
        block = _generated_block()
        assert "spawn_subagent" not in block

    def test_every_announced_tool_has_description(self):
        block = _generated_block()
        for line in block.splitlines():
            if not line.startswith("- "):
                continue
            if ":" not in line[2:]:
                pytest.fail(f"Tool line without description: {line}")
            _, desc = line[2:].split(":", 1)
            assert desc.strip(), f"Tool has empty description: {line}"

    def test_new_delegation_tools_are_announced(self):
        block = _generated_block()
        for name in ("spawn", "fanout", "spawn_background", "subagent_list",
                     "subagent_result", "subagent_send", "subagent_cancel",
                     "orchestrate_vote", "orchestrate_map_reduce",
                     "orchestrate_chain"):
            assert f"- {name}:" in block, f"{name} not announced in prompt"

    def test_extension_tools_are_appended_not_shadowed(self):
        """Extension-provided tools appear in the menu alongside built-ins."""
        from unittest.mock import MagicMock
        ext_schema = {
            "type": "function",
            "function": {
                "name": "ext_demo_tool",
                "description": "A demo tool from a fake extension.",
            },
        }
        extensions = MagicMock()
        extensions.tools.return_value = [ext_schema]
        core = WispAgentCore(extensions=extensions)
        block = core._build_tools_block()
        assert "- ext_demo_tool:" in block
        assert "provided by plugins/MCP servers" in block
