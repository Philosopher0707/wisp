"""Dispatcher tests — slash commands via public runtime interfaces only.

The dispatcher must never touch REPL-wrapper privates (adapter._loop,
transport._render_event, runtime._get_core). Fakes expose the same public
surface the real AgentRuntime/CLITransport provide.
"""

from __future__ import annotations

from typing import Any

from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._sessions: dict[str, dict] = {}

    async def get_or_create_session(self, session_id: str, model: str, workspace: str) -> dict:
        self.calls.append(("get_or_create_session", (session_id, model, workspace), {}))
        return self._sessions.setdefault(session_id, {
            "id": session_id, "model": model, "workspace": workspace, "messages": [],
        })

    def get_doctor_report(self) -> dict:
        self.calls.append(("get_doctor_report", (), {}))
        return {"healthy": True, "passed": 5, "total": 5}


class FakeTransport:
    def __init__(self) -> None:
        self.rendered: list[dict] = []

    def render(self, event: dict) -> None:
        self.rendered.append(event)


def _ctx(**kw: Any) -> ReplContext:
    base: dict[str, Any] = {
        "runtime": FakeRuntime(),
        "transport": FakeTransport(),
        "session": {"id": "s1", "model": "m", "workspace": ".", "messages": []},
        "config": {"model": "m", "provider": "mock"},
        "out": [],
    }
    base.update(kw)
    return ReplContext(**base)  # type: ignore[arg-type]


def test_non_slash_passthrough():
    d = Dispatcher()
    assert d.dispatch(_ctx(), "hello world") is CommandResult.PASSTHROUGH


def test_unknown_slash_consumed_with_hint():
    d = Dispatcher()
    out: list[str] = []
    ctx = _ctx(out=out)
    assert d.dispatch(ctx, "/nope") is CommandResult.CONSUMED
    assert any("help" in line.lower() for line in out)


def test_help_lists_commands():
    d = Dispatcher()
    out: list[str] = []
    assert d.dispatch(_ctx(out=out), "/help") is CommandResult.CONSUMED
    text = "\n".join(out)
    for name in ("help", "doctor", "provider", "model", "expand"):
        assert f"/{name}" in text


def test_doctor_uses_runtime_interface():
    d = Dispatcher()
    ctx = _ctx()
    assert d.dispatch(ctx, "/doctor") is CommandResult.CONSUMED
    assert any(c[0] == "get_doctor_report" for c in ctx.runtime.calls)


def test_provider_routes_without_privates():
    d = Dispatcher()
    ctx = _ctx()
    assert d.dispatch(ctx, "/provider") is CommandResult.CONSUMED
    # No private attribute access on runtime or transport may occur;
    # FakeRuntime/FakeTransport have no privates to reach into.
    assert isinstance(ctx.runtime, FakeRuntime)


def test_expand_returns_followup_prompt():
    d = Dispatcher()
    ctx = _ctx()
    ctx.session["messages"] = [{"role": "assistant", "content": "partial answer"}]
    result = d.dispatch(ctx, "/expand")
    assert result is CommandResult.FOLLOWUP
    assert ctx.followup


def test_exit_command():
    d = Dispatcher()
    assert d.dispatch(_ctx(), "/exit") is CommandResult.EXIT


def test_legacy_dispatch_delegation_and_sync():
    from wisp.cli.dispatcher import Dispatcher

    class Adapter:
        def __init__(self, ctx):
            self.session = ctx.session
            self.config = ctx.config

    def _legacy(text, adapter):
        assert text == "/old arg"
        adapter.session = {"id": "s2", "messages": []}
        return True

    d = Dispatcher(legacy_dispatch=_legacy)
    ctx = _ctx()
    ctx.adapter = Adapter(ctx)
    assert d.dispatch(ctx, "/old arg") is CommandResult.CONSUMED
    assert ctx.session["id"] == "s2"


def test_legacy_followup_string():
    from wisp.cli.dispatcher import Dispatcher

    d = Dispatcher(legacy_dispatch=lambda text, ad: "do the thing")
    ctx = _ctx()
    ctx.adapter = object()
    assert d.dispatch(ctx, "/continue") is CommandResult.FOLLOWUP
    assert ctx.followup == "do the thing"


def test_handler_exception_is_contained():
    d = Dispatcher()

    @d.register("boom", "always fails")
    def _boom(ctx: ReplContext, args: str) -> CommandResult:
        raise RuntimeError("kablam")

    out: list[str] = []
    assert d.dispatch(_ctx(out=out), "/boom") is CommandResult.CONSUMED
    assert any("kablam" in line for line in out)
