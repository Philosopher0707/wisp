"""`/swarm` when the provider refuses every call (an out-of-credit key, HTTP 402).

Observed live: four agents each hit the same 402, then the command printed "Swarm complete" with a green tick,
synthesized a "final answer" out of four identical error blocks, and the configured model had silently been replaced
by the first model in the provider's alphabetical listing (`aion-labs/aion-2.0` for `qwen2.5-coder`).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


import pytest

from wisp.config import WispConfig
from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.multi_agent.task import SubagentContract, SubagentResult
from wisp.provider_catalog import resolve_selection

BILLING = ("Incomplete provider round (API error 402: This request requires more credits — the provider refused "
           "this request on billing: the account is out of credit)")


# ── the model is never replaced by an arbitrary one ───────────────────────────────────────────────────────────


def _resolve(monkeypatch, model: str, listing: list[str]):
    monkeypatch.setattr("wisp.provider_catalog.list_models", lambda provider, cfg=None: list(listing))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = WispConfig().replace(provider="openrouter", model=model, api_key="test-key")
    return resolve_selection(cfg)


LISTING = ["aion-labs/aion-2.0", "kwaipilot/kat-coder-pro-v2.5", "qwen/qwen-2.5-coder-32b-instruct", "qwen/qwen3-coder"]


def test_an_unknown_model_with_no_strong_match_is_not_replaced(monkeypatch):
    r = _resolve(monkeypatch, "qwen2.5-coder", LISTING)
    assert r.status == "unknown_model"
    assert r.suggested == "", "auto-correct needs a prefix/substring match, not the first model alphabetically"
    assert "qwen/qwen3-coder" in r.alternatives, "close names are still offered to the user"


def test_a_name_that_resembles_nothing_still_falls_back_to_the_first_listed_model(monkeypatch):
    """The existing contract (test_nvidia_unknown_autocorrects_in_composition): a stale name from another provider,
    with no look-alike in the live listing, is replaced so the first turn is not a 404. Only the case where
    look-alikes exist (and the first listed model is something else entirely) changed."""
    r = _resolve(monkeypatch, "zzz-unrelated", LISTING)
    assert r.alternatives == [] and r.suggested == LISTING[0]


def test_a_strong_match_may_still_be_suggested(monkeypatch):
    r = _resolve(monkeypatch, "qwen-2.5-coder", LISTING)  # a substring of one real id
    assert r.suggested == "qwen/qwen-2.5-coder-32b-instruct"


# ── a failure that will hit every agent stops the ones that have not started ──────────────────────────────────


class _Orchestrator(SubagentOrchestrator):
    """Real `run_parallel`; `run` is scripted so no provider is involved."""

    ran: list[str]

    async def run(self, contract, *a, **k):  # type: ignore[override]
        self.ran.append(contract.name)
        await asyncio.sleep(0.01)
        if contract.name == "boom":
            return SubagentResult(task_id=contract.name, success=False, error=BILLING)
        return SubagentResult(task_id=contract.name, success=True, output="ok")


def _orchestrator(tmp_path) -> _Orchestrator:
    cfg = WispConfig().replace(model="m", provider="ollama", workspace=str(tmp_path), permission_mode="full")
    orch = _Orchestrator(config=cfg, workspace=tmp_path)
    orch.ran = []
    return orch


def _contracts(*names: str) -> list[SubagentContract]:
    return [SubagentContract(name=n, task="t", role="researcher", timeout_seconds=5, max_iterations=2) for n in names]


def test_queued_agents_are_not_started_after_a_billing_failure(tmp_path):
    orch = _orchestrator(tmp_path)
    results = asyncio.run(orch.run_parallel(_contracts("boom", "a", "b", "c", "d", "e"), max_concurrent=1,
                                            adaptive=False, shared_context=False))
    assert orch.ran == ["boom"], "a 402 fails every agent the same way; starting the rest only spends time"
    assert len(results) == 6 and not any(r.success for r in results)
    skipped = [r for r in results if r.task_id != "boom"]
    assert all("not started" in (r.error or "").lower() for r in skipped)
    assert all("402" in (r.error or "") or "billing" in (r.error or "").lower() for r in skipped), \
        "a skipped agent says why"


def test_a_non_systemic_failure_does_not_stop_the_others(tmp_path):
    class Flaky(_Orchestrator):
        async def run(self, contract, *a, **k):  # type: ignore[override]
            self.ran.append(contract.name)
            if contract.name == "boom":
                return SubagentResult(task_id="boom", success=False, error="tool crashed: KeyError 'x'")
            return SubagentResult(task_id=contract.name, success=True, output="ok")

    orch = Flaky(config=WispConfig().replace(model="m", provider="ollama", workspace=str(tmp_path),
                                              permission_mode="full"), workspace=tmp_path)
    orch.ran = []
    results = asyncio.run(orch.run_parallel(_contracts("boom", "a", "b"), max_concurrent=1, adaptive=False,
                                            shared_context=False))
    assert orch.ran == ["boom", "a", "b"] and [r.success for r in results] == [False, True, True]


# ── the swarm's verdict is honest ─────────────────────────────────────────────────────────────────────────────


def _r(ok: bool, error: str | None = None) -> SubagentResult:
    return SubagentResult(task_id="x", success=ok, output="out" if ok else "", error=error)


def test_failures_are_described_in_words_a_person_can_act_on():
    from wisp.core.recovery import describe_failure as _describe_failure

    assert "out of credit" in _describe_failure(BILLING)
    assert "HTTP 401" in _describe_failure("Incomplete provider round (API error 401: bad key)")
    assert _describe_failure(None) == "no error reported"
    assert len(_describe_failure("x" * 1000)) == 300


def test_verdict_is_complete_partial_or_failed():
    from wisp.multi_agent.verdict import verdict as _swarm_verdict

    assert _swarm_verdict([_r(True), _r(True)]) == "complete"
    assert _swarm_verdict([_r(True), _r(False, "e")]) == "partial"
    assert _swarm_verdict([_r(False, "e"), _r(False, "e")]) == "failed"
    assert _swarm_verdict([]) == "failed"


# ── end to end: the real CLI, runtime, orchestrator and provider against a stub that answers 402 ──────────────


class _Stub(BaseHTTPRequestHandler):
    chat_posts: list[str] = []

    def log_message(self, *a):  # noqa: D401
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # the model listing
        self._send(200, {"data": [{"id": "aion-labs/aion-2.0"}, {"id": "qwen/qwen3-coder"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).chat_posts.append(self.path)
        self._send(402, {"error": {"message": "This request requires more credits, or fewer max_tokens. "
                                              "You requested up to 4096 tokens, but can only afford 162.",
                                   "code": 402}})


@pytest.fixture
def swarm_output(tmp_path):
    from tests.test_cli_surface_e2e import cli_env

    _Stub.chat_posts = []
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    env = cli_env(home, {"WISP_PROVIDER": "openai", "WISP_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
                         "WISP_API_KEY": "test-key", "WISP_MODEL": "qwen2.5-coder"})
    try:
        proc = subprocess.run([sys.executable, "-m", "wisp", "-w", str(ws), "/swarm", "research recursion"],
                              env=env, cwd=str(ws), capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)
    finally:
        server.shutdown()
    return proc.stdout + proc.stderr


def test_a_total_failure_is_reported_as_one(swarm_output):
    assert "Swarm failed" in swarm_output
    assert "Swarm complete" not in swarm_output
    assert "Synthesizing" not in swarm_output, "nothing to synthesize from four errors"
    assert swarm_output.count("[INCOMPLETE]") == 0


def test_the_reason_is_stated_once_with_the_roles_it_affected(swarm_output):
    summary = [ln for ln in swarm_output.splitlines() if "out of credit" in ln]
    assert len(summary) == 1, "four identical refusals are one finding"
    assert "coder, reviewer, tester, researcher" in summary[0] or all(
        role in summary[0] for role in ("coder", "reviewer", "tester", "researcher"))
    assert "Add credit or raise the key's limit" in summary[0], "the remedy is not cut off"


def test_the_configured_model_is_served_not_replaced(swarm_output):
    assert "Auto-correcting to" not in swarm_output
    assert "aion-labs/aion-2.0" not in swarm_output.split("Did you mean")[0]


def test_no_synthesis_request_is_made_after_total_failure(swarm_output):
    assert len(_Stub.chat_posts) <= 4, f"only the four agents' own requests, got {len(_Stub.chat_posts)}"


def test_a_swarm_that_works_still_completes_through_the_real_runtime(tmp_path):
    """The success path must be untouched: mock provider, real CLI, runtime and orchestrator."""
    from tests.test_cli_surface_e2e import cli_env

    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    proc = subprocess.run([sys.executable, "-m", "wisp", "-w", str(ws), "-p", "mock", "-m", "mock-model",
                           "/swarm", "say hi"], env=cli_env(home), cwd=str(ws), capture_output=True, text=True,
                          timeout=150, stdin=subprocess.DEVNULL)
    out = proc.stdout + proc.stderr
    assert "Swarm complete" in out and "Swarm failed" not in out
    assert out.count("done (") == 4
