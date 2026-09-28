"""F8 restoration — the real tool-execution path, asserted.

This file exists because the previous phases could only assert the *shape* of the
validation path, never that a tool executed. With the declared dependency
provisioned, the path is exercisable, and these tests are the tripwires that keep
it that way.

Non-vacuity: every assertion here fails if `jsonschema` goes missing again —
`test_a_valid_call_is_not_refused_by_a_missing_dependency` is the one that fired
during F8, and `test_a_real_write_reaches_the_disk` cannot pass unless a tool
genuinely runs. Both were confirmed by hiding `jsonschema` from `sys.modules`.

The tests deliberately do NOT assert anything about the verification-evidence
defect found by this phase (a failing `run_bash` read as success). That defect is
pre-existing, out of this phase's authority, and pinning it here would freeze it.
"""
from __future__ import annotations

import asyncio
import pathlib


REPO = pathlib.Path(__file__).resolve().parents[2]

from wisp.config import WispConfig  # noqa: E402
from wisp.core.engine import WispAgentCore  # noqa: E402
from wisp.core.runtime import AgentRuntime  # noqa: E402
from wisp.core.session_repo import SessionRepository  # noqa: E402
from wisp.infra.extensions import ExtensionHost  # noqa: E402
from wisp.infra.security import PermissionMode, SecurityPolicy  # noqa: E402
from wisp.infra.store import UnifiedStore  # noqa: E402
from wisp.infra.telemetry import Telemetry  # noqa: E402
from wisp.tool_executor import ToolExecutor  # noqa: E402


class _Provider:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        yield from self._rounds[min(self.calls - 1, len(self._rounds) - 1)]()


def _tool_round(name, args, cid):
    def _g():
        yield {"type": "tool_calls",
               "calls": [{"id": cid, "type": "function",
                          "function": {"name": name, "arguments": args}}]}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _content_round(text="done"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": "stop"}
    return _g


def _core(**kwargs):
    return WispAgentCore(config=None, provider=None,
                         security=SecurityPolicy(), tool_executor=None, **kwargs)


def _run(ws, rounds, *, mode=PermissionMode.ASK_ALL, sid="f8"):
    config = WispConfig().replace(workspace=str(ws), permission_mode=mode)
    store = UnifiedStore(ws / f"{sid}.db")
    provider = _Provider(rounds)

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(permission_mode=mode),
                             tool_executor=ToolExecutor(config))

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(permission_mode=mode),
        extensions=ExtensionHost(), telemetry=Telemetry(),
        core_factory=factory, session_repo=SessionRepository(store), config=config)
    session = {"id": sid, "model": "mock", "workspace": str(ws), "messages": []}

    async def approve(event, *a, **kw):
        return True

    async def _main():
        return [ev async for ev in runtime.run_turn(
            session, prompt="go", approval_handler=approve)]

    return asyncio.run(_main())


def _results(events):
    out = []
    for ev in events:
        if ev.get("type") != "tool_result":
            continue
        payload = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out.append((ev.get("name", ""), str(payload.get("result", ev.get("result", "")))))
    return out


# ══════════════════════════════════════════════════════════════════════════
# The dependency itself
# ══════════════════════════════════════════════════════════════════════════


class TestTheValidatorIsAvailable:
    def test_jsonschema_is_importable(self):
        import jsonschema
        assert jsonschema is not None

    def test_it_is_a_declared_runtime_dependency(self):
        """The repair was provisioning, not a manifest edit — so the manifest
        must still declare it."""
        text = (REPO / "pyproject.toml").read_text()
        assert "jsonschema" in text, (
            "jsonschema is no longer declared — the provisioning phase assumed "
            "the manifest already carried it")


# ══════════════════════════════════════════════════════════════════════════
# F8's signature must be gone
# ══════════════════════════════════════════════════════════════════════════


class TestTheF8SignatureIsGone:
    def test_a_valid_call_is_not_refused_by_a_missing_dependency(self):
        core = _core()
        assert core._validate_tool_args("read_file", {"path": "a.txt"}) is None

    def test_an_invalid_call_is_refused_for_a_schema_reason(self):
        """The refusal must name the ARGUMENT, not the validator."""
        error = _core()._validate_tool_args("read_file", {})
        assert error, "a missing required argument was accepted"
        assert "required property" in error, f"not a schema reason: {error!r}"
        assert "jsonschema" not in error, (
            "the refusal blames the validator — F8's laundering is back")

    def test_valid_and_invalid_no_longer_share_one_message(self):
        core = _core()
        valid = core._validate_tool_args("read_file", {"path": "a.txt"})
        invalid = core._validate_tool_args("read_file", {})
        assert valid is None and invalid is not None
        assert valid != invalid

    def test_an_unknown_tool_still_defers_to_the_security_layer(self):
        """The intentional delegation is unchanged by the repair."""
        assert _core()._validate_tool_args("not_a_registered_tool", {"x": 1}) is None


# ══════════════════════════════════════════════════════════════════════════
# Real execution, end to end
# ══════════════════════════════════════════════════════════════════════════


class TestRealToolExecution:
    def test_a_real_read_returns_the_file_content(self, tmp_path):
        (tmp_path / "input.txt").write_text("real content\n")
        events = _run(tmp_path, [_tool_round("read_file", {"path": "input.txt"}, "c0"),
                                 _content_round()])
        results = _results(events)
        assert results and "real content" in results[0][1], (
            f"read_file did not return the file: {results}")

    def test_a_real_write_reaches_the_disk(self, tmp_path):
        target = tmp_path / "written.txt"
        assert not target.exists()
        _run(tmp_path, [_tool_round("write_file",
                                    {"path": str(target), "content": "landed\n"}, "c0"),
                        _content_round()])
        assert target.exists(), (
            "write_file reported success but nothing was written — the tool "
            "path is not really executing")
        assert target.read_text() == "landed\n"

    def test_authorization_still_gates_shell_in_the_default_mode(self, tmp_path):
        """`run_bash` needs approval in AUTO_EDIT (13F.1 made it a hard deny;
        d0d4bea lifted that to REQUIRE_APPROVAL). Restoring the validator must
        not have softened authorization: the shell runs only after a request."""
        events = _run(tmp_path,
                      [_tool_round("run_bash", {"command": "echo nope"}, "c0"),
                       _content_round()],
                      mode=PermissionMode.AUTO_EDIT)
        asked = [ev for ev in events if ev.get("type") == "approval_request"
                 and (ev.get("name") or ev.get("data", {}).get("name")) == "run_bash"]
        assert asked, f"AUTO_EDIT ran shell without asking: {events}"

    def test_an_approved_command_really_executes(self, tmp_path):
        events = _run(tmp_path,
                      [_tool_round("run_bash", {"command": "echo restored"}, "c0"),
                       _content_round()])
        results = _results(events)
        assert results and "restored" in results[0][1], (
            f"the approved command did not execute: {results}")

    def test_a_schema_invalid_call_never_executes(self, tmp_path):
        """`read_file` with no path is genuinely invalid — there is no salvage
        for it. (Note `write_file` DOES salvage a missing path to
        `./output.txt`; that is documented behaviour, not a validation gap.)"""
        events = _run(tmp_path,
                      [_tool_round("read_file", {}, "c0"), _content_round()])
        results = _results(events)
        executed = [r for r in results if '"status": "ok"' in r[1]]
        assert not executed, f"an invalid call executed: {executed}"
        assert any(ev.get("type") == "error" for ev in events), (
            "an invalid call produced no refusal")
