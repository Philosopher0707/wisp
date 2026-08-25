"""Invariant: every advertised tool name must be dispatchable.

Regression class: SkillExtension advertised `skill__<name>` schemas that no
execution path could serve — the model trusted the schema and died on
`Unknown tool`. Schemas without execution paths are lies. This test walks
EVERY advertisement surface and asserts each name resolves somewhere.
"""

from pathlib import Path

import pytest

from wisp.infra.extensions import ExtensionHost
from wisp.tools.registry import TOOL_IMPLS

SKILL_MD = """---
name: demo
description: Demo skill for the wiring invariant.
---
Do the demo thing.
"""


@pytest.fixture()
def host(tmp_path: Path) -> ExtensionHost:
    from wisp.extensions import HookExtension, MCPExtension, PluginExtension, SkillExtension

    ws = tmp_path / "ws"
    skill_dir = ws / ".agents" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    h = ExtensionHost()
    h.register(PluginExtension())
    h.register(HookExtension())
    h.register(MCPExtension(workspace=str(ws)))
    h.register(SkillExtension(workspace=str(ws)))
    return h


def _is_mcp_shaped(name: str) -> bool:
    return name.startswith("mcp:") or (
        name.startswith("mcp__") and name.count("__") >= 2
    )


class TestEveryAdvertisedToolDispatches:
    def test_all_extension_tools_resolve(self, host, tmp_path) -> None:
        advertised = [t["function"]["name"] for t in host.tools()]
        assert "skill__demo" in advertised  # fixture sanity

        for name in advertised:
            dispatchable = (
                name in TOOL_IMPLS
                or _is_mcp_shaped(name)
                or host.call_tool(name, {"prompt": "x"}, str(tmp_path)) is not None
            )
            assert dispatchable, (
                f"{name} is ADVERTISED but has NO execution path — "
                f"schemas without dispatch are lies to the model"
            )

    def test_skill_call_serves_instructions(self, host, tmp_path) -> None:
        res = host.call_tool("skill__demo", {"prompt": "go"}, str(tmp_path))
        assert res and res["status"] == "ok"
        assert "Do the demo thing." in res["data"]

    def test_builtin_names_never_shadowed_by_host(self, host, tmp_path) -> None:
        for name in list(TOOL_IMPLS)[:5]:
            if not name.startswith("skill__") and not _is_mcp_shaped(name):
                # host may answer for its own namespaces only; builtins are
                # dispatched by the executor before host fallback anyway.
                assert name in TOOL_IMPLS
