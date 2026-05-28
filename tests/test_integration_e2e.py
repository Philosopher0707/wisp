"""TDD for end-to-end integration.

Verifies that CompositionRoot + Runtime + Core + Transport work together.
This is the migration safety net — if this passes, the legacy code can be deleted.
"""

import pytest
from dataclasses import dataclass
from pathlib import Path


# ── Minimal mock provider ──────────────────────────────────────────

class _EchoProvider:
    """Provider that echoes back the prompt."""

    def generate_stream_events(self, system_prompt: str, messages: list[dict], tools: list | None = None, checkpoint_every: int = 50):
        # Find the last user message
        for msg in reversed(messages):
            if msg.get("role") == "user":
                yield {"type": "content", "text": f"echo: {msg['content']}"}
                break
        yield {"type": "done"}


# ── Test config ────────────────────────────────────────────────────

@dataclass
class _TestConfig:
    db_path: Path
    permission_mode: str
    model: str
    turn_timeout: int = 600


@pytest.fixture
def config(tmp_path):
    from wisp.infra.security import PermissionMode
    return _TestConfig(
        db_path=tmp_path / "test.db",
        permission_mode=PermissionMode.FULL,
        model="qwen2.5-coder",
    )


# ═══════════════════════════════════════════════════════════════════
# 1. CompositionRoot integration
# ═══════════════════════════════════════════════════════════════════

class TestCompositionRootIntegration:
    """CompositionRoot wires everything correctly."""

    def test_can_create_root(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.store is not None
        assert root.security is not None
        assert root.extensions is not None
        assert root.telemetry is not None
        assert root.runtime is not None

    def test_can_start_and_stop(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        root.start()
        root.stop()


# ═══════════════════════════════════════════════════════════════════
# 2. Runtime + Core integration
# ═══════════════════════════════════════════════════════════════════

class TestRuntimeCoreIntegration:
    """Runtime and Core process turns together."""

    @pytest.mark.asyncio
    async def test_full_turn(self, config):
        from wisp.composition import CompositionRoot

        root = CompositionRoot(config)

        # Inject mock provider into core factory
        original_factory = root.runtime.core_factory
        def mock_factory():
            core = original_factory()
            core.provider = _EchoProvider()
            return core
        root.runtime.core_factory = mock_factory

        session = await root.runtime.get_or_create_session(
            session_id="sess-1",
            model="qwen",
            workspace="/tmp",
        )

        events = []
        async for event in root.runtime.run_turn(session, "hello"):
            events.append(event)

        assert len(events) == 2
        assert events[0]["text"] == "echo: hello"
        assert events[1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_session_persisted(self, config):
        from wisp.composition import CompositionRoot

        root = CompositionRoot(config)
        original_factory = root.runtime.core_factory
        def mock_factory():
            core = original_factory()
            core.provider = _EchoProvider()
            return core
        root.runtime.core_factory = mock_factory

        session = await root.runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        async for _ in root.runtime.run_turn(session, "hello"):
            pass

        loaded = root.store.load_session("sess-1")
        assert loaded is not None
        assert len(loaded["messages"]) == 2  # user + assistant


# ═══════════════════════════════════════════════════════════════════
# 3. Transport + Runtime integration
# ═══════════════════════════════════════════════════════════════════

class TestTransportRuntimeIntegration:
    """Transport and Runtime work together."""

    @pytest.mark.asyncio
    async def test_cli_transport_renders_events(self, config):
        from wisp.composition import CompositionRoot
        from wisp.transport.cli import CLITransport
        from io import StringIO

        root = CompositionRoot(config)
        original_factory = root.runtime.core_factory
        def mock_factory():
            core = original_factory()
            core.provider = _EchoProvider()
            return core
        root.runtime.core_factory = mock_factory

        transport = CLITransport(root.runtime)
        transport.start()
        buf = StringIO()

        # Render events directly through the transport pipeline
        event = {"type": "content", "text": "echo: hello"}
        transport._render_event(buf, event)
        transport._flush_content(buf)

        assert "echo: hello" in buf.getvalue()
        transport.stop()


# ═══════════════════════════════════════════════════════════════════
# 4. Security integration
# ═══════════════════════════════════════════════════════════════════

class TestSecurityIntegration:
    """Security policy blocks dangerous operations end-to-end."""

    @pytest.mark.asyncio
    async def test_read_only_blocks_bash(self, config):
        from wisp.composition import CompositionRoot
        from wisp.infra.security import PermissionMode

        config.permission_mode = PermissionMode.READ_ONLY
        root = CompositionRoot(config)

        # Inject provider that tries to run bash
        class _BashProvider:
            def generate_stream_events(self, **kwargs):
                yield {"type": "tool_call", "name": "run_bash", "arguments": {"command": "ls"}}
                yield {"type": "done"}

        original_factory = root.runtime.core_factory
        def mock_factory():
            core = original_factory()
            core.provider = _BashProvider()
            return core
        root.runtime.core_factory = mock_factory

        session = await root.runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        events = []
        async for event in root.runtime.run_turn(session, "run ls"):
            events.append(event)

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) >= 1
        assert "READ_ONLY" in error_events[0].get("message", "")


# ═══════════════════════════════════════════════════════════════════
# 5. Telemetry integration
# ═══════════════════════════════════════════════════════════════════

class TestTelemetryIntegration:
    """Telemetry records metrics end-to-end."""

    @pytest.mark.asyncio
    async def test_turn_records_metrics(self, config):
        from wisp.composition import CompositionRoot

        root = CompositionRoot(config)
        original_factory = root.runtime.core_factory
        def mock_factory():
            core = original_factory()
            core.provider = _EchoProvider()
            return core
        root.runtime.core_factory = mock_factory

        session = await root.runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        async for _ in root.runtime.run_turn(session, "hello"):
            pass

        metrics = root.telemetry.metrics()
        assert metrics["turns_total"] == 1
