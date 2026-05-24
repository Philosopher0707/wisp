"""TDD for Bug 3: Session compaction drops system messages.

The maybe_compact() method replaces ALL messages with:
    [{"role": "system", "content": summary}] + kept

This drops any existing system messages (persona, delegation context, etc.).
The fix should preserve existing system messages at the start.
"""

import pytest

from wisp.core.runtime import AgentRuntime


class _MockCore:
    async def turn(self, session: dict, prompt: str, approval_handler=None):
        yield {"type": "content", "text": f"echo: {prompt}"}
        yield {"type": "done"}


@pytest.fixture
def tmp_store(tmp_path):
    from wisp.infra.store import UnifiedStore
    return UnifiedStore(tmp_path / "test.db")


@pytest.fixture
def runtime(tmp_store):
    from wisp.infra.security import SecurityPolicy, PermissionMode
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.telemetry import Telemetry

    return AgentRuntime(
        store=tmp_store,
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: _MockCore(),
    )


class TestCompactionPreservesSystemMessages:
    """Compaction must preserve existing system messages."""

    @pytest.mark.asyncio
    async def test_compaction_preserves_system_messages(self, runtime):
        """Existing system messages should be kept after compaction."""
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")

        # Add a system message (e.g., persona)
        session["messages"].append({"role": "system", "content": "You are a helpful assistant."})

        # Add many user/assistant messages to trigger compaction
        for i in range(20):
            session["messages"].append({"role": "user", "content": f"msg{i}"})
            session["messages"].append({"role": "assistant", "content": f"reply{i}"})

        await runtime.maybe_compact(session, max_messages=10)

        # The original system message should still be present
        system_msgs = [m for m in session["messages"] if m["role"] == "system"]
        assert len(system_msgs) >= 2  # original + summary
        contents = [m["content"] for m in system_msgs]
        assert "You are a helpful assistant." in contents

    @pytest.mark.asyncio
    async def test_compaction_preserves_multiple_system_messages(self, runtime):
        """Multiple system messages should all be preserved."""
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")

        session["messages"].append({"role": "system", "content": "Persona A"})
        session["messages"].append({"role": "system", "content": "Persona B"})

        for i in range(20):
            session["messages"].append({"role": "user", "content": f"msg{i}"})
            session["messages"].append({"role": "assistant", "content": f"reply{i}"})

        await runtime.maybe_compact(session, max_messages=10)

        system_msgs = [m for m in session["messages"] if m["role"] == "system"]
        contents = [m["content"] for m in system_msgs]
        assert "Persona A" in contents
        assert "Persona B" in contents
        # Should also have the compaction summary
        assert any("Compacted" in c for c in contents)

    @pytest.mark.asyncio
    async def test_compaction_summary_is_separate_system_message(self, runtime):
        """The compaction summary should be its own system message."""
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")

        session["messages"].append({"role": "system", "content": "Original persona"})

        for i in range(20):
            session["messages"].append({"role": "user", "content": f"msg{i}"})
            session["messages"].append({"role": "assistant", "content": f"reply{i}"})

        await runtime.maybe_compact(session, max_messages=10)

        system_msgs = [m for m in session["messages"] if m["role"] == "system"]
        # Should have original persona + compaction summary
        assert len(system_msgs) >= 2

    @pytest.mark.asyncio
    async def test_no_compaction_when_under_limit(self, runtime):
        """When under the limit, no compaction should occur."""
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")

        session["messages"].append({"role": "system", "content": "Keep me"})
        session["messages"].append({"role": "user", "content": "hello"})

        await runtime.maybe_compact(session, max_messages=100)

        assert len(session["messages"]) == 2
        assert session["messages"][0]["content"] == "Keep me"
