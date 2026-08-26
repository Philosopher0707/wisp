"""Inter-chunk stream deadline: a mid-stream provider stall must end the
stream promptly with a visible truncation notice, never wedge the session
until the 30-minute turn watchdog.

Pre-fix, after the first meaningful token `stream.__anext__()` ran
unguarded — one dead connection held the per-session lock for the full
turn budget while the user stared at silence.
"""

import time

import pytest

from wisp.core.engine import WispAgentCore
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import PermissionMode, SecurityPolicy


class _StallMidStream:
    """Yields one real delta, then goes silent forever."""

    def generate_stream_events(self, system_prompt, messages, tools=None,
                               checkpoint_every=50):
        yield {"type": "content", "text": "partial"}
        time.sleep(120)  # far past any test deadline
        yield {"type": "complete"}  # pragma: no cover


class _HealthyStream:
    def generate_stream_events(self, system_prompt, messages, tools=None,
                               checkpoint_every=50):
        yield {"type": "content", "text": "a"}
        yield {"type": "content", "text": "b"}
        yield {"type": "done"}


def _core(provider):
    return WispAgentCore(
        provider=provider,
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
    )


@pytest.mark.asyncio
async def test_mid_stream_stall_ends_bounded_with_truncation_notice(monkeypatch):
    core = _core(_StallMidStream())
    monkeypatch.setattr(WispAgentCore, "CHUNK_DEADLINE_S", 0.2)
    monkeypatch.setattr(WispAgentCore, "FIRST_TOKEN_DEADLINE_S", 1.0)

    events = []
    start = time.monotonic()
    async for event in core._guarded_provider_stream("s", [{"r": "u"}], None):
        events.append(event)
    elapsed = time.monotonic() - start

    types = [e.get("type") for e in events]
    assert "content" in types, f"partial output lost: {types}"
    assert any(t == "provider_status" for t in types), (
        f"truncation notice missing from {types}"
    )
    stall = next(e for e in events if e.get("type") == "provider_status")
    assert "truncat" in str(stall).lower() or "no data" in str(stall).lower()
    assert elapsed < 5.0, f"stall wedged {elapsed:.1f}s (deadline not applied)"


@pytest.mark.asyncio
async def test_healthy_stream_unaffected_by_chunk_deadline():
    core = _core(_HealthyStream())
    monkeypatch_deadline = 0.05  # absurdly tight; deltas are instant
    original = WispAgentCore.CHUNK_DEADLINE_S
    WispAgentCore.CHUNK_DEADLINE_S = monkeypatch_deadline
    try:
        texts = []
        async for event in core._guarded_provider_stream(
            "s", [{"r": "u"}], None,
        ):
            if event.get("type") == "content":
                texts.append(event.get("text"))
    finally:
        WispAgentCore.CHUNK_DEADLINE_S = original
    assert texts == ["a", "b"], f"healthy stream damaged: {texts}"
