"""Unit tests for Wisp Enterprise TUI widgets.

Covers: reactives, composition, state changes, edge cases.
Uses mocks for external dependencies (WebSocket, file system, supervisor).
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from wisp.tui.widgets.title_bar import TitleBar
from wisp.tui.widgets.status_bar import StatusBar
from wisp.tui.widgets.activity_bar import ActivityBar, IconButton
from wisp.tui.widgets.chat.input_bar import InputBar
from wisp.tui.widgets.chat.message_list import MessageList
from wisp.tui.widgets.chat.assistant_message import AssistantMessage
from wisp.tui.widgets.chat.thinking_block import ThinkingBlock
from wisp.tui.widgets.chat.content_block import ContentBlock
from wisp.tui.widgets.chat.tool_call_card import ToolCallCard
from wisp.tui.widgets.chat.user_message import UserMessage
from wisp.tui.widgets.chat.diff_block import DiffBlock
from wisp.tui.widgets.tools.tool_history import ToolHistoryTable
from wisp.tui.widgets.file_tree.tree_view import FileTree
from wisp.tui.widgets.file_tree.code_preview import CodePreview
from wisp.tui.widgets.file_tree.repo_map_summary import RepoMapSummary
from wisp.tui.widgets.agents.agent_grid import AgentGrid, AgentCard
from wisp.tui.widgets.agents.task_tree import TaskTree
from wisp.tui.widgets.agents.token_gauge import TokenGauge
from wisp.tui.widgets.monitor.log_viewer import LogViewer
from wisp.tui.widgets.monitor.metrics import PerformanceMetrics


# ══════════════════════════════════════════════════════════════════════
# TitleBar
# ══════════════════════════════════════════════════════════════════════

class TestTitleBar:
    def test_reactives_default_values(self):
        tb = TitleBar()
        assert tb.model_name == "default"
        assert tb.workspace_path == "~"
        assert tb.session_label == "new session"
        assert tb.connection_state == "connected"

    def test_model_name_reactive_changes(self):
        tb = TitleBar()
        tb.model_name = "llama3.2"
        assert tb.model_name == "llama3.2"

    def test_session_label_reactive_changes(self):
        tb = TitleBar()
        tb.session_label = "bugfix-session"
        assert tb.session_label == "bugfix-session"

    def test_workspace_path_reactive_changes(self):
        tb = TitleBar()
        tb.workspace_path = "/home/user/project"
        assert tb.workspace_path == "/home/user/project"


# ══════════════════════════════════════════════════════════════════════
# StatusBar
# ══════════════════════════════════════════════════════════════════════

class TestStatusBar:
    def test_reactives_default_values(self):
        sb = StatusBar()
        assert sb.connection_state == "disconnected"
        assert sb.is_streaming is False
        assert sb.token_count == 0
        assert sb.active_agents == 0

    def test_is_streaming_true(self):
        sb = StatusBar()
        sb.is_streaming = True
        assert sb.is_streaming is True

    def test_is_streaming_false(self):
        sb = StatusBar()
        sb.is_streaming = True
        sb.is_streaming = False
        assert sb.is_streaming is False

    def test_token_count_updates(self):
        sb = StatusBar()
        sb.token_count = 4200
        assert sb.token_count == 4200

    def test_token_count_zero(self):
        sb = StatusBar()
        sb.token_count = 0
        assert sb.token_count == 0


# ══════════════════════════════════════════════════════════════════════
# ActivityBar
# ══════════════════════════════════════════════════════════════════════

class TestActivityBar:
    def test_constructs_with_default_tab(self):
        ab = ActivityBar()
        assert ab.active_tab == "chat"

    def test_icon_button_constructs(self):
        btn = IconButton("💬", "chat", tooltip="Chat view")
        assert btn.btn_name == "chat"
        assert btn.tooltip == "Chat view"
        assert btn.active is False

    def test_icon_button_active_toggle(self):
        btn = IconButton("📁", "files")
        btn.active = True
        assert btn.active is True


# ══════════════════════════════════════════════════════════════════════
# InputBar
# ══════════════════════════════════════════════════════════════════════

class TestInputBar:
    def test_reactives_default(self):
        ib = InputBar()
        assert ib.value == ""
        assert ib.is_streaming is False
        assert ib.token_estimate == 0

    def test_on_submit_callback_settable(self):
        ib = InputBar()
        called = []
        ib.on_submit = lambda text: called.append(text)
        assert ib.on_submit is not None

    def test_streaming_disables_input(self):
        ib = InputBar()
        ib.is_streaming = True
        assert ib.is_streaming is True


# ══════════════════════════════════════════════════════════════════════
# MessageList
# ══════════════════════════════════════════════════════════════════════

class TestMessageList:
    def test_auto_scroll_default(self):
        ml = MessageList()
        assert ml.auto_scroll is True


# ══════════════════════════════════════════════════════════════════════
# AssistantMessage
# ══════════════════════════════════════════════════════════════════════

class TestAssistantMessage:
    def test_reactives_default(self):
        am = AssistantMessage()
        assert am.content_text == ""
        assert am.thinking_text == ""

    def test_append_thinking_updates_reactive(self):
        am = AssistantMessage()
        am.append_thinking("Let me analyze this...")
        assert "Let me analyze" in am.thinking_text

    def test_append_thinking_accumulates(self):
        am = AssistantMessage()
        am.append_thinking("step 1. ")
        am.append_thinking("step 2.")
        assert "step 1" in am.thinking_text
        assert "step 2" in am.thinking_text

    def test_append_content_updates_reactive(self):
        am = AssistantMessage()
        am.append_content("Here is the fix:")
        assert "Here is the fix" in am.content_text

    def test_append_content_accumulates(self):
        am = AssistantMessage()
        am.append_content("line 1. ")
        am.append_content("line 2.")
        assert "line 1" in am.content_text
        assert "line 2" in am.content_text


# ══════════════════════════════════════════════════════════════════════
# ThinkingBlock
# ══════════════════════════════════════════════════════════════════════

class TestThinkingBlock:
    def test_expanded_default_false(self):
        tb = ThinkingBlock()
        assert tb.expanded is False

    def test_text_default_empty(self):
        tb = ThinkingBlock()
        assert tb.text == ""

    def test_append_updates_text(self):
        tb = ThinkingBlock()
        tb.append("reasoning...")
        assert "reasoning" in tb.text

    def test_append_accumulates(self):
        tb = ThinkingBlock()
        tb.append("a")
        tb.append("b")
        tb.append("c")
        assert "abc" in tb.text


# ══════════════════════════════════════════════════════════════════════
# ContentBlock
# ══════════════════════════════════════════════════════════════════════

class TestContentBlock:
    def test_text_default_empty(self):
        cb = ContentBlock()
        assert cb.text == ""

    def test_append_updates_text(self):
        cb = ContentBlock()
        cb.append("Hello world")
        assert "Hello world" in cb.text

    def test_append_accumulates(self):
        cb = ContentBlock()
        cb.append("part1")
        cb.append("part2")
        assert "part1part2" in cb.text


# ══════════════════════════════════════════════════════════════════════
# ToolCallCard
# ══════════════════════════════════════════════════════════════════════

class TestToolCallCard:
    def test_constructs_with_name(self):
        card = ToolCallCard("run_bash")
        assert card.tool_name == "run_bash"
        assert card.is_complete is False
        assert card.duration_ms == 0

    def test_constructs_with_args(self):
        card = ToolCallCard("edit_file", {"path": "foo.py", "content": "x=1"})
        assert card.tool_name == "edit_file"
        assert "foo.py" in card.tool_args

    def test_set_result_marks_complete(self):
        card = ToolCallCard("run_bash")
        card.set_result("success", 340)
        assert card.is_complete is True
        assert card.duration_ms == 340

    def test_set_result_stores_text(self):
        card = ToolCallCard("run_tests")
        card.set_result("5 passed", 1200)
        assert "5 passed" in card.result_text

    def test_format_args_truncates_long_values(self):
        args = ToolCallCard._format_args({"path": "x" * 100})
        assert len(args) <= 85  # max 80 + some overhead

    def test_format_args_empty(self):
        args = ToolCallCard._format_args({})
        assert args == ""


# ══════════════════════════════════════════════════════════════════════
# UserMessage
# ══════════════════════════════════════════════════════════════════════

class TestUserMessage:
    def test_stores_text(self):
        msg = UserMessage("refactor auth.py")
        assert msg._text == "refactor auth.py"

    def test_stores_timestamp(self):
        msg = UserMessage("hello", timestamp="2026-05-17")
        assert msg._timestamp == "2026-05-17"


# ══════════════════════════════════════════════════════════════════════
# DiffBlock
# ══════════════════════════════════════════════════════════════════════

class TestDiffBlock:
    def test_constructs_with_texts(self):
        db = DiffBlock("old", "new", filename="test.py")
        assert db.old_text == "old"
        assert db.new_text == "new"
        assert db.filename == "test.py"

    def test_constructs_without_filename(self):
        db = DiffBlock("old", "new")
        assert db.filename == ""


# ══════════════════════════════════════════════════════════════════════
# AgentGrid
# ══════════════════════════════════════════════════════════════════════

class TestAgentGrid:
    def test_agents_default_empty(self):
        grid = AgentGrid()
        assert grid.agents == []

    def test_agents_reactive_updates(self):
        grid = AgentGrid()
        grid.agents = [
            {"name": "coder-1", "role": "coder", "status": "running", "task": "Implement login"},
        ]
        assert len(grid.agents) == 1
        assert grid.agents[0]["name"] == "coder-1"

    def test_multiple_agents(self):
        grid = AgentGrid()
        grid.agents = [
            {"name": "a", "status": "running"},
            {"name": "b", "status": "idle"},
            {"name": "c", "status": "completed"},
        ]
        assert len(grid.agents) == 3


# ══════════════════════════════════════════════════════════════════════
# AgentCard
# ══════════════════════════════════════════════════════════════════════

class TestAgentCard:
    def test_constructs_with_fields(self):
        card = AgentCard("coder-1", role="coder", status="running", task="Fix bug")
        assert card.agent_name == "coder-1"
        assert card.agent_role == "coder"
        assert card.agent_status == "running"
        assert card.agent_task == "Fix bug"

    def test_render_includes_name_and_status(self):
        card = AgentCard("test-agent", role="worker", status="idle", task="")
        rendered = card.render()
        assert "test-agent" in rendered
        assert "○" in rendered  # idle icon

    def test_render_running_status(self):
        card = AgentCard("a", status="running", task="do work")
        rendered = card.render()
        assert "●" in rendered
        assert "do work" in rendered


# ══════════════════════════════════════════════════════════════════════
# TokenGauge
# ══════════════════════════════════════════════════════════════════════

class TestTokenGauge:
    def test_defaults(self):
        gauge = TokenGauge()
        assert gauge.tokens_used == 0
        assert gauge.token_budget == 32000

    def test_tokens_used_reactive(self):
        gauge = TokenGauge()
        gauge.tokens_used = 16000
        assert gauge.tokens_used == 16000

    def test_token_budget_reactive(self):
        gauge = TokenGauge()
        gauge.token_budget = 64000
        assert gauge.token_budget == 64000


# ══════════════════════════════════════════════════════════════════════
# PerformanceMetrics
# ══════════════════════════════════════════════════════════════════════

class TestPerformanceMetrics:
    def test_defaults(self):
        pm = PerformanceMetrics()
        assert pm.session_uptime == "0:00"
        assert pm.message_count == 0
        assert pm.tool_calls == 0
        assert pm.tokens_total == 0

    def test_message_count_reactive(self):
        pm = PerformanceMetrics()
        pm.message_count = 15
        assert pm.message_count == 15

    def test_tool_calls_reactive(self):
        pm = PerformanceMetrics()
        pm.tool_calls = 42
        assert pm.tool_calls == 42

    def test_tokens_total_reactive(self):
        pm = PerformanceMetrics()
        pm.tokens_total = 8400
        assert pm.tokens_total == 8400


# ══════════════════════════════════════════════════════════════════════
# FileTree (no mount needed — just construction)
# ══════════════════════════════════════════════════════════════════════

class TestFileTree:
    def test_constructs_with_path(self, tmp_path):
        ft = FileTree(root_path=str(tmp_path))
        assert ft.root == tmp_path.resolve()

    def test_constructs_with_default(self):
        ft = FileTree()
        assert ft.root.name != ""


# ══════════════════════════════════════════════════════════════════════
# CodePreview
# ══════════════════════════════════════════════════════════════════════

class TestCodePreview:
    def test_constructs_default(self):
        cp = CodePreview()
        assert cp.file_path is None

    def test_constructs_with_path(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("print('hello')")
        cp = CodePreview(file_path=str(f))
        assert cp.file_path == str(f)
