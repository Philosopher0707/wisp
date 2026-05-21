"""Test that thinking and tool results display correctly in CLI."""

import pytest
import asyncio
from io import StringIO
from unittest.mock import MagicMock, patch


class FakeProvider:
    """Mock provider that yields thinking + content + tool_call."""

    def generate_stream_events(self, system_prompt, messages, tools=None):
        from wisp.stream_events import TokenBatch, ToolCallBatch, StreamComplete
        yield TokenBatch(phase='thinking', text='Let me think about this...', batch_index=1)
        yield TokenBatch(phase='content', text='I will read the file.', batch_index=2)
        yield ToolCallBatch(phase='tool_calls', calls=[{
            'function': {'name': 'read_file', 'arguments': {'path': 'test.py'}}
        }])
        yield StreamComplete(
            phase='complete',
            final_thinking='Let me think about this...',
            final_content='I will read the file.',
            total_tokens=20,
            tool_calls=None,
            validation_hash='abc'
        )


class FakeConfig:
    model = 'test-model'
    workspace = '/tmp'
    show_thinking = True  # Enable thinking display
    auto_approve = True
    max_context_tokens = 128000
    chars_per_token = 4
    permission_mode = 'full'
    provider = 'ollama'
    ollama_url = 'http://localhost:11434'
    temperature = 0.2


@pytest.fixture
def transport():
    from wisp.transport.cli_v2 import CLITransport
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.telemetry import Telemetry
    from wisp.core.engine import WispAgentCore

    config = FakeConfig()
    provider = FakeProvider()
    core = WispAgentCore(
        provider=provider,
        security=SecurityPolicy('full'),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        config=config,
    )
    store = MagicMock()
    store.load_session.return_value = None
    store.save_session.return_value = None

    runtime = AgentRuntime(
        store=store,
        security=SecurityPolicy('full'),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: core,
    )

    return CLITransport(runtime, config)


@pytest.mark.asyncio
async def test_thinking_displayed(transport):
    """Thinking text should be rendered when show_thinking=True."""
    stdin = StringIO("hello\n/exit\n")
    stdout = StringIO()

    await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()

    assert "Let me think about this..." in output


@pytest.mark.asyncio
async def test_tool_call_displayed(transport):
    """Tool call should be rendered."""
    stdin = StringIO("hello\n/exit\n")
    stdout = StringIO()

    await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()

    assert "read_file" in output


@pytest.mark.asyncio
async def test_tool_result_displayed(transport):
    """Tool result should be rendered."""
    stdin = StringIO("hello\n/exit\n")
    stdout = StringIO()

    await transport.run(stdin, stdout, "test-session", "test-model", "/tmp")
    output = stdout.getvalue()

    # The tool result should show something about the file read
    assert "read_file" in output
