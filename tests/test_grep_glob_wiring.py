"""`grep` and `glob` are registered, classified read-only everywhere a tool is classified, and work through the real
registry in every permission mode that allows reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from wisp.capability_filter import READ_ONLY_TOOLS, filter_schemas_for_mode
from wisp.core.contracts import ToolRisk, risk_for_tool
from wisp.infra.policy_engine import _DEFAULT_SAFE_READ_TOOLS
from wisp.infra.security import _SAFE_READ_TOOLS
from wisp.mcp.manager import _SHADOW_BUILTIN_TOOLS
from wisp.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS, execute_tool

NEW = ("grep", "glob")


@pytest.fixture
def ws(tmp_path: Path) -> str:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def needle():\n    return 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("nothing\n", encoding="utf-8")
    return str(tmp_path)


def _schema(name: str) -> dict:
    return next(t["function"] for t in TOOL_SCHEMAS if t["function"]["name"] == name)


@pytest.mark.parametrize("name", NEW)
class TestRegistered:
    def test_there_is_a_schema_and_an_implementation(self, name):
        assert name in TOOL_IMPLS
        assert _schema(name)["parameters"]["required"] == ["pattern"]

    def test_the_risk_is_read(self, name):
        assert risk_for_tool(name) is ToolRisk.READ

    def test_every_read_only_set_agrees(self, name):
        assert name in READ_ONLY_TOOLS and name in _DEFAULT_SAFE_READ_TOOLS and name in _SAFE_READ_TOOLS

    def test_the_read_only_capability_partition_keeps_it(self, name):
        kept = {t["function"]["name"] for t in filter_schemas_for_mode(TOOL_SCHEMAS, "read_only")}
        assert name in kept

    def test_an_mcp_server_cannot_shadow_it(self, name):
        assert name in _SHADOW_BUILTIN_TOOLS

    def test_the_schema_stays_cheap(self, name):
        import json

        assert len(json.dumps(_schema(name))) // 3 < 300  # tokens at the assembler's ruler


@pytest.mark.parametrize("mode", ["read_only", "auto_edit"])
class TestThroughTheRealRegistry:
    def test_grep(self, ws, mode):
        out = execute_tool("grep", {"pattern": "def needle"}, ws, permission_mode=mode)
        assert "pkg/mod.py:1:def needle():" in out

    def test_glob(self, ws, mode):
        out = execute_tool("glob", {"pattern": "**/*.py"}, ws, permission_mode=mode)
        assert "pkg/mod.py" in out and "notes.md" not in out


class TestSafetyHoldsThroughTheRegistry:
    def test_a_path_outside_the_workspace_is_refused(self, ws, tmp_path):
        out = execute_tool("grep", {"pattern": "x", "path": "/etc"}, ws)
        assert "outside workspace" in out.lower() or "access denied" in out.lower()

    def test_a_catastrophic_pattern_is_refused_not_run(self, ws):
        out = execute_tool("grep", {"pattern": "(a+)+$"}, ws)
        assert "refused" in out.lower() or "backtrack" in out.lower()

    def test_a_traversal_glob_is_refused(self, ws):
        out = execute_tool("glob", {"pattern": "../*"}, ws)
        assert "traversal" in out.lower() or "invalid pattern" in out.lower()
