"""CI guard: ensure DEFAULT_SYSTEM prompt and TOOL_SCHEMAS stay in sync.

If a tool is added to schemas but not listed in the system prompt, the LLM
won't know it exists. If a tool is listed in the prompt but not wired in
TOOL_SCHEMAS / TOOL_IMPLS, the prompt will hallucinate tool names that
crash at dispatch time.
"""

import re

import pytest

from wisp.context_assembler import DEFAULT_SYSTEM
from wisp.tools import TOOL_SCHEMAS, TOOL_IMPLS


def _extract_tools_from_prompt() -> set[str]:
    """Extract tool names from the '## Tools available' section."""
    # Find the Tools section
    match = re.search(r"## Tools available\n(.*?)(?:\n## |\n{3,}|$)", DEFAULT_SYSTEM, re.DOTALL)
    if not match:
        pytest.skip("No '## Tools available' section found in DEFAULT_SYSTEM")
    section = match.group(1)
    return {m.group(1) for m in re.finditer(r"- (\S+):", section)}


class TestSystemPromptToolSync:
    def test_prompt_tools_match_schema_tools(self):
        prompt_tools = _extract_tools_from_prompt()
        schema_tools = {s["function"]["name"] for s in TOOL_SCHEMAS}

        missing_in_prompt = schema_tools - prompt_tools
        extra_in_prompt = prompt_tools - schema_tools

        assert missing_in_prompt == set(), (
            f"Tools registered in TOOL_SCHEMAS but MISSING from DEFAULT_SYSTEM: "
            f"{sorted(missing_in_prompt)}"
        )
        assert extra_in_prompt == set(), (
            f"Tools listed in DEFAULT_SYSTEM but NOT in TOOL_SCHEMAS: "
            f"{sorted(extra_in_prompt)}"
        )

    def test_prompt_tools_match_impl_tools(self):
        prompt_tools = _extract_tools_from_prompt()
        impl_tools = set(TOOL_IMPLS.keys())

        missing_in_prompt = impl_tools - prompt_tools
        extra_in_prompt = prompt_tools - impl_tools

        assert missing_in_prompt == set(), (
            f"Tools registered in TOOL_IMPLS but MISSING from DEFAULT_SYSTEM: "
            f"{sorted(missing_in_prompt)}"
        )
        assert extra_in_prompt == set(), (
            f"Tools listed in DEFAULT_SYSTEM but NOT in TOOL_IMPLS: "
            f"{sorted(extra_in_prompt)}"
        )

    def test_every_tool_has_at_least_basic_description(self):
        """Each tool in the prompt should have a non-empty description."""
        section_match = re.search(
            r"## Tools available\n(.*?)(?:\n## |\n{3,}|$)", DEFAULT_SYSTEM, re.DOTALL
        )
        if not section_match:
            pytest.skip("No Tools section")
        for line in section_match.group(1).splitlines():
            if not line.startswith("-"):
                continue
            # Format:  "- tool_name: Some description here"
            if ":" not in line[2:]:  # after the "- " prefix
                assert False, f"Tool line without description: {line}"
            _, desc = line[2:].split(":", 1)
            assert desc.strip(), f"Tool has empty description: {line}"
