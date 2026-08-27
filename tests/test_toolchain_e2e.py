"""End-to-end: the REPL tool-call chain must work over PRODUCTION wiring.

This is the promoted live-verification harness used to diagnose the
tool-history regression pinned in ``test_runtime_tool_history.py``.
Unlike those unit-level pins (core fallback dispatch), THIS test drives
the real composition stack end-to-end:

    CompositionRoot -> AgentRuntime.run_turn -> WispAgentCore.turn
      -> gating -> ToolExecutor.execute -> registry -> feedback -> model

and asserts the properties that only exist on that path:
  - the CompositionRoot-built shared ToolExecutor actually receives calls
  - a CLI-style approval handler re-enters through the core's wrap layer,
    invoked for write tools ONLY (auto_edit permission mode)
  - an ``approval_request`` event precedes the callback
  - tools hit real impls: ``write_file`` lands bytes on disk
  - every emitted event renders cleanly through ``CLITransport._render_event``
  - persisted history matches the protocol-consistent grouped shape
    (one assistant message with all blocks, immediately followed by its
    own role:"tool" replies) -- the regression fixed in runtime.py

Only the LLM network layer is scripted (MockProvider); everything else is
production code. Hermetic: tmp_path workspace/store, no network.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from wisp.config import WispConfig
from wisp.composition import CompositionRoot
from wisp.providers.mock import MockProvider


def _scripted_provider() -> MockProvider:
    """Turn 1: read_file + write_file in ONE iteration; turn 2: final answer."""
    return MockProvider(
        responses=[
            "",
            "Done \u2014 I read notes.md and wrote the report.",
        ],
        tool_calls=[
            [
                {"function": {"name": "read_file",
                              "arguments": {"path": "notes.md"}}},
                {"function": {"name": "write_file",
                              "arguments": {"path": "reports/out.md",
                                            "content": "# report\nwritten\n"}}},
            ],
        ],
        thinking=["need to inspect then write"],
    )

# ── test body appended below ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repl_chain_end_to_end_over_production_wiring(
        tmp_path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.md").write_text("alpha beta gamma")
    (ws / "reports").mkdir()

    provider = _scripted_provider()

    # Route CompositionRoot._create_core to the scripted provider.
    # monkeypatch restores both globals after the test (no cross-test leak).
    import wisp.provider_catalog as pc
    monkeypatch.setattr(
        pc, "resolve_selection",
        lambda cfg: SimpleNamespace(
            status="ok", suggested=None, provider="mock", detail="",
            model="mock-model", alternatives=[]))
    from wisp.providers.factory import ProviderFactory
    monkeypatch.setattr(ProviderFactory, "from_config",
                        lambda self, cfg: provider)

    config = WispConfig().replace(
        workspace=str(ws), provider="mock",
        model="mock-model", auto_approve=False)
    root = CompositionRoot(config)

    # Instrument the SHARED executor CompositionRoot wired into every core:
    # proves the engine delegates through it instead of its fallback branch.
    orig_execute = root.tool_executor.execute
    executed: list[str] = []

    async def counting_execute(tool_name, tool_args, workspace, **kw):
        executed.append(tool_name)
        async for ev in orig_execute(tool_name, tool_args, workspace, **kw):
            yield ev

    root.tool_executor.execute = counting_execute

    # CLI-style simple handler ({name, arguments}) -> bool — exactly the
    # surface CLITransport.approve exposes. The CORE must wrap it into the
    # executor's (name, args, reason) -> (approved, modified) protocol.
    approvals: list[str] = []

    async def cli_like_approve(tool_call: dict) -> bool:
        approvals.append(tool_call.get("name"))
        return True  # simulate pressing [y]

    session = await root.runtime.get_or_create_session(
        "e2e-toolchain", model="mock-model", workspace=str(ws))

    events = [ev async for ev in root.runtime.run_turn(
        session, "read notes.md then write reports/out.md",
        approval_handler=cli_like_approve)]

    by_type: dict[str, list[dict]] = {}
    for ev in events:
        by_type.setdefault(ev.get("type", ""), []).append(ev)

# ── assertions appended below ────────────────────────────────────────────

    results = {e.get("name"): str(e.get("result", ""))
               for e in by_type.get("tool_result", [])}

    # Execution went through the production executor, both calls really ran.
    assert executed == ["read_file", "write_file"], (
        f"expected core -> ToolExecutor.execute for both calls; saw {executed}")
    assert '"status": "ok"' in results.get("read_file", "")
    assert "alpha beta gamma" in results.get("read_file", ""), \
        "registry impl returned synthetic data, not the real file body"

    # Side effect: write_file reached its real implementation on disk.
    outfile = ws / "reports" / "out.md"
    assert outfile.exists() and "written" in outfile.read_text()
    assert '"status": "ok"' in results.get("write_file", "")

    # Approval: write-only, exactly once, request-event precedes callback.
    assert approvals == ["write_file"]
    approval_reqs = by_type.get("approval_request", [])
    assert len(approval_reqs) == 1
    assert approval_reqs[0].get("name") == "write_file"

    # Loop closed: streamed final content and a natural done event.
    content = "".join(str(e.get("text", ""))
                      for e in by_type.get("content", []))
    assert "wrote the report" in content
    assert by_type.get("done"), "turn never emitted a done event"

    # Persisted history over PRODUCTION wiring: grouped exchange shape.
    msgs = session["messages"]
    assistants = [m for m in msgs if m.get("tool_calls")]
    tools = [m for m in msgs if m.get("role") == "tool"]
    assert len(assistants) == 1, (
        f"runtime must persist ONE grouped assistant message; got "
        f"{len(assistants)} — see tests/test_runtime_tool_history.py")
    blocks = assistants[0]["tool_calls"]
    assert [b["function"]["name"] for b in blocks] == [
        "read_file", "write_file"]
    assert {t["tool_call_id"] for t in tools} == {b["id"] for b in blocks}
    a_idx = msgs.index(assistants[0])
    assert [msgs.index(t) for t in tools] == [a_idx + 1, a_idx + 2]

    # Every event renders through the real CLI renderer without error.
    from wisp.transport.cli import CLITransport
    transport = CLITransport(root.runtime, root.config)
    buf = io.StringIO()
    for ev in events:
        transport._render_event(buf, ev)
    rendered = buf.getvalue()
    assert "read_file" in rendered and "write_file" in rendered, \
        "tool panels missing from rendered output"

    try:
        root.shutdown()
    except Exception:
        pass


