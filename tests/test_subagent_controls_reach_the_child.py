"""A subagent's contract limits hold on the path the REPL actually uses.

Found 2026-10-13 by driving the composition-wired orchestrator against a local OpenAI-compatible
stub (every existing test injected a fake core or ran without the runtime):

* a child with ``max_iterations=3`` made 201 model calls: the runtime path never handed the
  contract's limit to the turn, so the parent's default (200) applied;
* ``/spawn researcher`` and ``/swarm`` passed the role's name but not its tool list, so a
  researcher (which reads untrusted web pages) was offered ``write_file`` and ``edit_file``;
* an isolated child's patch carried Wisp's own state (``.wisp/wisp.db``, its WAL, the audit
  log) out of the worktree; ``git apply`` refused the whole patch because the parent has its own
  ``.wisp/wisp.db``, the worktree was deleted, and the child still reported success.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

_SCN = re.compile(r"SCN\{(\w+)(?::([^}]*))?\}")
_WRITE_TOOLS = {"write_file", "edit_file", "edit_file_multi", "run_bash"}


class _Stub:
    """OpenAI-compatible SSE endpoint; the task's SCN{kind:arg} picks the reply."""

    def __init__(self):
        self.requests: list[dict] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"object": "list", "data": [{"id": "stub-model", "object": "model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                msgs = body.get("messages", [])
                hit = next((m for m in (_SCN.search(str(x.get("content", ""))) for x in msgs
                                        if x.get("role") == "user") if m), None)
                kind, arg = (hit.group(1), hit.group(2) or "") if hit else ("none", "")
                stub.requests.append({"kind": kind, "arg": arg,
                                      "tools": sorted(t["function"]["name"] for t in body.get("tools") or [])})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                answered = any(m.get("role") == "tool" for m in msgs)
                if kind == "loop" and body.get("tools"):
                    n = sum(1 for m in msgs if m.get("role") == "tool")
                    self._tool("read_file", {"path": "README.md"}, f"call_{n}")
                elif kind == "write" and not answered:
                    self._tool("write_file", {"path": arg, "content": "child wrote this\n"}, "call_1")
                else:
                    self._text(f"ANSWER {kind} {arg}")

            def _send(self, delta, finish=None):
                chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()

            def _text(self, text):
                self._send({"role": "assistant", "content": text})
                self._send({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")

            def _tool(self, name, args, call_id):
                self._send({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                                            "function": {"name": name, "arguments": json.dumps(args)}}]})
                self._send({}, "tool_calls")
                self.wfile.write(b"data: [DONE]\n\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def of(self, kind, arg=None):
        return [r for r in self.requests if r["kind"] == kind and (arg is None or r["arg"] == arg)]


def _git(ws, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=ws, check=True,
                   capture_output=True)


def _wire(tmp_path, monkeypatch, **overrides):
    """The orchestrator as the REPL gets it: CompositionRoot, real provider, real runtime."""
    for var in ("WISP_PROVIDER", "WISP_MODEL", "WISP_API_BASE", "WISP_TOOL_PROFILE", "WISP_MAX_ITERATIONS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WISP_USER_GUIDELINES", "off")
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("hello\n")
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "add", ".")
    _git(ws, "commit", "-qm", "init")  # deliberately no .gitignore: a user project need not ignore .wisp/
    stub = _Stub()
    cfg = WispConfig().replace(provider="openai", api_base=stub.url, api_key="k", model="stub-model",
                               workspace=str(ws), permission_mode="auto_edit", **overrides)
    return CompositionRoot(config=cfg), stub, ws


@pytest.fixture
def wired(tmp_path, monkeypatch):
    root, stub, ws = _wire(tmp_path, monkeypatch)
    yield root, stub, ws
    stub.server.shutdown()
    stub.server.server_close()


def _contract(name, task, **kw):
    from wisp.multi_agent import SubagentContract

    kw.setdefault("timeout_seconds", 60)
    return SubagentContract(name=name, task=task, cache=False, **kw)


class TestIterationLimit:
    def test_the_contracts_max_iterations_bounds_the_child(self, wired):
        root, stub, _ = wired
        orch = root.subagent_orchestrator
        assert orch._runner._agent_runtime is not None, "must exercise the runtime path"
        asyncio.run(orch.run(_contract("looper", "SCN{loop}", max_iterations=3)))
        # Three tool rounds, then the core's one wrap-up request (no tools offered).
        assert len(stub.of("loop")) <= 4, len(stub.of("loop"))

    def test_a_contract_cannot_raise_the_hosts_limit(self, tmp_path, monkeypatch):
        root, stub, _ = _wire(tmp_path, monkeypatch, max_iterations=2)
        try:
            asyncio.run(root.subagent_orchestrator.run(_contract("looper", "SCN{loop}", max_iterations=10)))
        finally:
            stub.server.shutdown()
            stub.server.server_close()
        assert len(stub.of("loop")) <= 3, len(stub.of("loop"))

    def test_the_limit_does_not_leak_into_the_parents_next_turn(self, wired):
        root, stub, _ = wired
        asyncio.run(root.subagent_orchestrator.run(_contract("looper", "SCN{loop}", max_iterations=2)))
        assert root.config.max_iterations == 200


class TestRoleTools:
    def test_slash_spawn_researcher_offers_no_write_or_shell(self, wired):
        root, stub, _ = wired
        from wisp.commands import dispatch

        class _Agent:
            runtime = root.runtime
            config = root.config
        with contextlib.redirect_stdout(io.StringIO()):
            dispatch("/spawn researcher SCN{say:viaspawn}", _Agent())
        offered = set(stub.of("say", "viaspawn")[-1]["tools"])
        assert not _WRITE_TOOLS & offered, sorted(_WRITE_TOOLS & offered)
        assert {"read_file", "grep"} <= offered

    def test_a_role_with_default_tools_gets_the_roles_list(self, wired):
        root, stub, _ = wired
        asyncio.run(root.subagent_orchestrator.run(_contract("r", "SCN{say:role}", role="researcher")))
        assert not _WRITE_TOOLS & set(stub.of("say", "role")[-1]["tools"])

    def test_an_explicit_tool_list_still_wins(self, wired):
        root, stub, _ = wired
        asyncio.run(root.subagent_orchestrator.run(
            _contract("r", "SCN{say:explicit}", role="researcher", tools=["read_file"])))
        assert stub.of("say", "explicit")[-1]["tools"] == ["read_file"]

    def test_the_generalist_keeps_its_full_surface(self, wired):
        root, stub, _ = wired
        asyncio.run(root.subagent_orchestrator.run(_contract("g", "SCN{say:gen}", role="generalist")))
        assert "write_file" in stub.of("say", "gen")[-1]["tools"]


class TestIsolatedWork:
    def _allow(self, orch):
        async def yes(event):
            return True
        orch.set_approval_handler(yes)

    def test_an_isolated_childs_file_reaches_the_workspace(self, wired):
        root, _, ws = wired
        orch = root.subagent_orchestrator
        self._allow(orch)
        r = asyncio.run(orch.run(_contract("iso", "SCN{write:iso.txt}", role="coder", worktree_isolated=True)))
        assert r.worktree_patch, "the child must have run in a worktree"
        assert ".wisp/" not in r.worktree_patch and ".agent/" not in r.worktree_patch
        assert r.patch_applied and r.success, (r.success, r.error)
        assert (ws / "iso.txt").read_text() == "child wrote this\n"

    def test_changes_that_cannot_be_applied_are_not_reported_as_success(self, wired):
        root, _, ws = wired
        orch = root.subagent_orchestrator
        self._allow(orch)
        (ws / "clash.txt").write_text("the parent's own file\n")  # untracked: the child's new file collides
        r = asyncio.run(orch.run(_contract("iso", "SCN{write:clash.txt}", role="coder", worktree_isolated=True)))
        assert r.worktree_patch and not r.patch_applied
        assert not r.success
        assert "NOT APPLIED" in r.output and "NOT APPLIED" in (r.error or "")
        assert (ws / "clash.txt").read_text() == "the parent's own file\n"
