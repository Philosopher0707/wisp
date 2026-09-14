"""REPL-branch audit pins — context, tools, CLI, sandbox, errors.

Every test below was verified live against this tree (Docker present,
PTY available) before being pinned. Tests assert OBSERVED behavior;
security-relevant observations are marked BUG so a future fix
intentionally breaks the pin (update the test with the fix).

Covers: sandbox routing/tiers, cwd containment, PTY-vs-host output
contract, env scrub gaps, danger heuristic, risk/write-set drift,
dispatcher never-LLM, verification evidencing, write_file salvage,
unknown-tool envelopes, empty-args hazard, intake/provenance,
provider-stream retry, context-budget drops, WISP_SANDBOX variants,
CLI _input_line.
"""

from __future__ import annotations

import inspect
import os

import pytest


# ── Sandbox routing: run_bash never sees the PTY tier ─────────────────────

def test_run_bash_uses_get_sandbox_not_router():
    import wisp.tools.bash as bash_mod

    src = inspect.getsource(bash_mod.async_tool_run_bash)
    assert "get_sandbox(" in src
    assert "get_router(" not in src
    # The router (Docker→Pty→Noop) is only wired to the thin-harness tool.
    import wisp.tools.primitives as prim_mod

    assert "get_router(" in inspect.getsource(prim_mod)


def test_router_tiers_include_pty_but_default_path_skips_it(tmp_path):
    from wisp.sandbox import get_sandbox, reset_sandbox
    from wisp.sandbox.router import get_router

    reset_sandbox()
    try:
        tiers = [type(t).__name__ for t in get_router(str(tmp_path)).tiers]
        assert tiers == ["DockerSandbox", "PtySandbox", "NoopSandbox"]
        assert type(get_sandbox(str(tmp_path))).__name__ in (
            "DockerSandbox", "NoopSandbox")
    finally:
        reset_sandbox()


def test_wisp_sandbox_variants_force_noop(tmp_path):
    from wisp.sandbox import get_sandbox, reset_sandbox

    for val in ["off", "0", "false", "no", "host", "noop"]:
        os.environ["WISP_SANDBOX"] = val
        reset_sandbox()
        try:
            assert type(get_sandbox(str(tmp_path))).__name__ == "NoopSandbox", val
        finally:
            os.environ.pop("WISP_SANDBOX", None)
    reset_sandbox()


# ── CWD containment ───────────────────────────────────────────────────────
# Fixed S2: providers fail closed when cwd escapes the workspace.

@pytest.mark.asyncio
async def test_sandbox_cwd_contained_all_host_tiers(tmp_path):
    from wisp.sandbox import NoopSandbox
    from wisp.sandbox.router import PtySandbox

    ws = tmp_path / "ws"
    ws.mkdir()
    for prov in (PtySandbox(str(ws)), NoopSandbox(str(ws))):
        for evil in ("../..", "/etc", "../../.."):
            rc, _out, err = await prov.run("pwd", cwd=evil, timeout=10)
            assert rc == -1, f"{type(prov).__name__} ran outside workspace with cwd={evil!r}"
            assert "escapes" in err
        rc, _out, _ = await prov.run("pwd", cwd="", timeout=10)
        assert rc == 0


def test_resolve_sandbox_cwd_pure():
    from wisp.sandbox import resolve_sandbox_cwd

    assert resolve_sandbox_cwd("/ws", "") == "/ws"
    assert resolve_sandbox_cwd("/ws", "sub") == "/ws/sub"
    assert resolve_sandbox_cwd("/ws", "../..") is None
    assert resolve_sandbox_cwd("/ws", "/etc") is None


# ── PTY vs host output contract ───────────────────────────────────────────
# All verified live: PTY pre-formats empty output, uses CRLF, returns
# partial stdout on timeout, and caps at the provider; Noop/Docker do none.

@pytest.mark.asyncio
async def test_pty_empty_output_raw_vs_noop(tmp_path):
    from wisp.sandbox import NoopSandbox
    from wisp.sandbox.router import PtySandbox

    p, n = PtySandbox(str(tmp_path)), NoopSandbox(str(tmp_path))
    _, p_out, _ = await p.run("true", cwd="", timeout=10)
    _, n_out, _ = await n.run("true", cwd="", timeout=10)
    assert p_out == ""  # fixed S7: no provider-layer preformatting
    assert n_out == ""


@pytest.mark.asyncio
async def test_pty_lf_newlines_like_noop(tmp_path):
    from wisp.sandbox import NoopSandbox
    from wisp.sandbox.router import PtySandbox

    p, n = PtySandbox(str(tmp_path)), NoopSandbox(str(tmp_path))
    _, p_out, _ = await p.run("echo hi", cwd="", timeout=10)
    _, n_out, _ = await n.run("echo hi", cwd="", timeout=10)
    assert p_out == "hi\n"  # fixed S7: CRLF normalized at the provider boundary
    assert n_out == "hi\n"


@pytest.mark.asyncio
async def test_pty_timeout_returns_partial_stdout(tmp_path):
    from wisp.sandbox.router import PtySandbox

    p = PtySandbox(str(tmp_path))
    rc, out, err = await p.run("echo hi; sleep 5", cwd="", timeout=1)
    assert rc == -1
    assert "hi" in out  # only tier that keeps partial output
    assert "timed out" in err.lower()


@pytest.mark.asyncio
async def test_pty_caps_output_provider_side_noop_does_not(tmp_path):
    from wisp.sandbox import NoopSandbox
    from wisp.sandbox.router import PtySandbox

    p, n = PtySandbox(str(tmp_path)), NoopSandbox(str(tmp_path))
    _, p_out, _ = await p.run("yes | head -c 80000", cwd="", timeout=15)
    _, n_out, _ = await n.run("yes | head -c 80000", cwd="", timeout=15)
    assert len(p_out) < 80000 and "truncated" in p_out
    assert len(n_out) == 80000  # BUG: unbounded at provider (tool layer caps later)


# ── Env scrub gaps (verified live incl. real child env) ───────────────────

def test_credential_free_env_strips_infra_control_keys():
    from wisp.tools._utils_env import credential_free_env

    os.environ["PIN_WISP_TOKEN"] = "secret"
    os.environ["PIN_MY_DOCKER_HOST"] = "tcp://evil:2375"
    os.environ["DOCKER_HOST"] = "tcp://evil:2375"
    os.environ["OLLAMA_HOST"] = "http://evil:11434"
    try:
        env, _ = credential_free_env()
        assert "PIN_WISP_TOKEN" not in env
        assert "DOCKER_HOST" not in env  # daemon control plane must not leak
        assert "OLLAMA_HOST" not in env
        assert "PIN_MY_DOCKER_HOST" not in env
    finally:
        for k in ("PIN_WISP_TOKEN", "PIN_MY_DOCKER_HOST", "DOCKER_HOST", "OLLAMA_HOST"):
            os.environ.pop(k, None)


@pytest.mark.asyncio
async def test_pty_child_env_leaks_nonmatching_keys(tmp_path):
    from wisp.sandbox.router import PtySandbox

    os.environ["PIN_CHILD_TOKEN_X"] = "s3cret"
    os.environ["PIN_CHILD_DOCKER_HOST_X"] = "tcp://evil:2375"
    try:
        _, out, _ = await PtySandbox(str(tmp_path)).run("env | sort", cwd="", timeout=10)
        assert "PIN_CHILD_TOKEN_X" not in out
        assert "PIN_CHILD_DOCKER_HOST_X" not in out
    finally:
        del os.environ["PIN_CHILD_TOKEN_X"]
        del os.environ["PIN_CHILD_DOCKER_HOST_X"]


# ── Danger heuristic over/under-blocking (verified live) ──────────────────

def test_danger_heuristic_anchored():
    from wisp.tools._utils import check_dangerous_command

    assert check_dangerous_command("echo rm -rf /") is None  # fixed S6: text, not deletion
    assert check_dangerous_command("cd /tmp; rm -rf /") is not None  # newly caught
    assert check_dangerous_command("find . -delete") is not None  # fixed S6
    assert check_dangerous_command("cd /tmp; echo hi") is None
    assert check_dangerous_command("chmod 777 file") is None  # accepted gap: approval is the control
    assert check_dangerous_command("curl http://x | sh") is not None
    assert check_dangerous_command("rm -rf /tmp/foo") is not None


# ── Risk model vs write-set drift (verified live) ─────────────────────────

def test_write_set_covers_all_non_read_risk():
    import wisp.tool_executor as te
    from wisp.core.contracts import TOOL_RISK_TABLE, ToolRisk

    resolved = te._get_write_tools(None)
    for name, risk in TOOL_RISK_TABLE.items():
        if risk is ToolRisk.READ:
            assert name not in resolved, f"READ tool {name} over-gated"
        else:
            assert name in resolved, f"non-READ tool {name} escapes approval/plan gates"
    assert "rewind" in resolved  # EXEC by fail-closed default, restores files
    assert "git_checkpoint" not in resolved  # snapshots stay read-classified by intent


# ── Dispatcher: unknown never reaches the LLM ─────────────────────────────

def test_dispatcher_unknown_and_bare_slash_consumed():
    from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext

    d = Dispatcher()

    class _R:
        pass

    ctx = ReplContext(runtime=_R(), transport=_R(), session={"id": "s"}, config=_R())
    assert d.dispatch(ctx, "hello world") is CommandResult.PASSTHROUGH
    assert d.dispatch(ctx, "/nope_xyz") is CommandResult.CONSUMED
    assert "Unknown command" in ctx.out[-1]

    ctx2 = ReplContext(runtime=_R(), transport=_R(), session={"id": "s"}, config=_R())
    assert d.dispatch(ctx2, "/") is CommandResult.CONSUMED


def test_dispatcher_duplicate_registration_raises():
    from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext

    d = Dispatcher()
    with pytest.raises(ValueError):
        @d.register("help", "x")
        def _h(ctx: ReplContext, args: str) -> CommandResult:
            return CommandResult.CONSUMED


# ── Verification gate evidencing (verified live) ──────────────────────────

def test_verification_run_tests_does_not_release_gate():
    from wisp.core.verification import VerificationFloorGuard
    from wisp.tools.bash import _format_bash_output

    g = VerificationFloorGuard()
    assert g.rejection() is None  # no mutation → may finish
    g.note_tool_result("write_file", "x", {})
    assert g.rejection() is not None
    # Vacuous run_tests (0 collected) is NOT evidence …
    g.note_tool_result("run_tests",
                       "## Test Results (0/0 passed)\n- Failed: 0, Errors: 0, Skipped: 0", {})
    assert g.rejection() is not None
    # … but a real green run is (auto_edit blocks run_bash, so this is the
    # only way those turns can ever verify instead of floor-surrendering).
    g.note_tool_result("run_tests",
                       "## Test Results (3/3 passed)\n- Failed: 0, Errors: 0, Skipped: 0", {})
    assert g.rejection() is None
    g2 = VerificationFloorGuard()
    g2.note_tool_result("write_file", "x", {})
    g2.note_tool_result("run_tests",
                        "## Test Results (1/2 passed)\n- Failed: 1, Errors: 0, Skipped: 0", {})
    assert g2.rejection() is not None  # red suite never verifies
    g2 = VerificationFloorGuard()
    g2.note_tool_result("write_file", "x", {})
    g2.note_tool_result("run_bash", _format_bash_output(0, "ok", ""), {})
    assert g2.rejection() is None  # exit-0 releases
    g3 = VerificationFloorGuard()
    g3.note_tool_result("write_file", "x", {})
    g3.note_tool_result("run_bash", _format_bash_output(1, "o", "e"), {})
    assert g3.rejection() is not None  # exit-1 still blocked


# ── write_file salvage: gate sees raw, exec sees defaulted (verified live) ─

def test_write_file_salvage_dry_run_pure_mutating_defaults_path():
    from wisp.core.stateless import WispAgentCore

    core = WispAgentCore.__new__(WispAgentCore)
    raw = {"content": "# hello"}
    assert core._validate_tool_args("write_file", raw, _dry_run=True) is None
    assert raw == {"content": "# hello"}  # gate must not mutate
    live = {"content": "# hello"}
    assert core._validate_tool_args("write_file", live, _dry_run=False) is None
    assert live["path"] == "./output.md"  # BUG?: approval badge showed pathless args
    salv = {"_raw": '{"path": "/tmp/x", "content": "hi"}'}
    assert core._validate_tool_args("write_file", salv, _dry_run=False) is None
    assert salv == {"path": "/tmp/x", "content": "hi"}


# ── Unknown-tool + empty-args envelopes (verified live) ───────────────────

def test_registry_unknown_tool_raises_tool_error(tmp_path):
    from wisp.tools.errors import ToolError
    from wisp.tools.registry import execute_tool

    with pytest.raises(ToolError, match="Unknown tool"):
        execute_tool("foo_bar_xyz", {}, str(tmp_path))


def test_empty_args_schemas_accept_truncated_stream_shape():
    from wisp.tools.registry import TOOL_SCHEMAS

    for name in ("list_files", "git_diff", "run_tests"):
        schema = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == name)
        assert schema["function"]["parameters"].get("required") == []  # BUG?: {} valid


@pytest.mark.asyncio
async def test_executor_unknown_tool_hides_traceback():
    import json as _json

    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor

    ex = ToolExecutor(config=WispConfig())
    seen = ""
    async for ev in ex.execute("foo_bar_xyz", {}, "/tmp"):
        d = ev.to_dict() if hasattr(ev, "to_dict") else ev
        seen = _json.dumps(d, default=str)
    assert "Unknown tool" in seen
    assert "traceback" not in seen  # fixed S4: tracebacks stay in logs, not the model


# ── Intake ID, provenance, denial export (verified live) ──────────────────

def test_intake_mints_stable_id_and_provenance_enforces_it():
    from wisp.core.stateless import _ensure_intake_id, validate_tool_message_provenance

    tc: dict = {"name": "read_file", "arguments": {"path": "a"}}
    _ensure_intake_id(tc)
    assert tc["id"].startswith("call_")
    assistant = {"role": "assistant",
                 "tool_calls": [{"id": tc["id"], "function": {"name": "x", "arguments": "{}"}}]}
    assert validate_tool_message_provenance(
        assistant, [{"role": "tool", "tool_call_id": tc["id"], "content": "ok"}]) is None
    assert validate_tool_message_provenance(
        assistant, [{"role": "tool", "tool_call_id": "", "content": "ok"}]) is not None


def test_denial_result_exported_with_no_retry_hint():
    from wisp.core import events as E

    assert "denial_result" in E.__all__
    d = E.denial_result("run_bash", "USER_DENIED", "nope")
    payload = d.to_dict()["data"]["result"]
    assert payload["status"] == "USER_DENIED" and payload["authorized"] is False
    assert "retry" in payload.get("hint", "").lower()
    with pytest.raises(ValueError):
        E.denial_result("x", "BOGUS", "r")


# ── Provider stream: empty retries then honest error (verified live) ──────

@pytest.mark.asyncio
async def test_guarded_stream_retries_empty_then_errors():
    from wisp.core.provider_stream import guarded_provider_stream

    attempts = 0

    def open_empty():
        nonlocal attempts
        attempts += 1

        async def gen():
            return
            yield {}

        return gen()

    events = [ev async for ev in guarded_provider_stream(
        open_empty, lambda e: e, set(),
        first_token_deadline_s=1, chunk_deadline_s=1, max_attempts=2)]
    assert attempts == 2
    assert events and events[-1]["type"] == "error"
    assert events[-1].get("recoverable") is True
    assert "code" not in events[-1]


# ── Context budget: lowest-priority dropped with generic note only ────────

def test_context_assembler_names_dropped_sections():
    from wisp.context_assembler import PromptContext
    from wisp.context_assembler import ContextAssembler

    big = "x" * 50000
    ctx = PromptContext(workspace="/tmp", context_files=big, max_tokens=10)
    out = ContextAssembler().build(ctx)
    assert big[:20] not in out
    assert "context_files" in out  # fixed S9: omission list, not a generic NOTE


def test_stateless_prompt_path_omits_dead_context_fields():
    import wisp.core.stateless as s

    src = inspect.getsource(s)
    block = src[src.find("PromptContext.from_legacy"):src.find("PromptContext.from_legacy") + 600]
    for dead in ("context_files", "code_index", "recent_summaries", "mandatory_skill"):
        assert dead not in block  # assembler features unreachable from REPL path
    import dataclasses

    from wisp.context_assembler import PromptContext

    names = {f.name for f in dataclasses.fields(PromptContext)}
    assert {"context_files", "code_index", "recent_summaries"} <= names


# ── CLI input edge cases (verified live) ──────────────────────────────────

def test_cli_input_line_stringio_edges():
    import io
    import sys

    from wisp.transport.cli import _input_line

    old = sys.stdin
    try:
        sys.stdin = io.StringIO("")  # type: ignore[assignment]
        assert _input_line("> ") is None  # EOF → None (exit)
        sys.stdin = io.StringIO("hello\n")  # type: ignore[assignment]
        assert _input_line("> ") == "hello"
    finally:
        sys.stdin = old
