"""A parent receives a child's claim and, separately, what the harness saw (design S4).

`docs/harness/subagent-orchestration-design.md` §4.4. Observed before this slice: `spawn` returned the child's text plus
`files`, and `files` was a regex over that same text, so "I edited fake.py and ran the tests: all green" came back as the
files changed by a child that had written a different file and run no tests. Nothing scanned the reply for instructions a
child could carry back from an untrusted page.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.test_subagent_controls_reach_the_child import _contract, _wire


@pytest.fixture
def full(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_TOOL_PROFILE", "full")
    root, stub, ws = _wire(tmp_path, monkeypatch, permission_mode="full")
    yield root, stub, ws
    stub.server.shutdown()
    stub.server.server_close()


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    root, stub, ws = _wire(tmp_path, monkeypatch)  # auto_edit, no approver: writes are refused
    yield root, stub, ws
    stub.server.shutdown()
    stub.server.server_close()


class TestFilesComeFromTheHarness:
    def test_files_are_what_the_child_wrote_not_what_it_says(self, full):
        root, _, ws = full
        r = asyncio.run(root.subagent_orchestrator.run(_contract("c", "SCN{claim:real.txt}")))
        assert (ws / "real.txt").exists()
        assert r.files_changed == ["real.txt"]

    def test_a_refused_write_is_not_a_changed_file(self, guarded):
        root, _, ws = guarded
        r = asyncio.run(root.subagent_orchestrator.run(_contract("c", "SCN{claim:blocked.txt}", role="coder")))
        assert not (ws / "blocked.txt").exists()
        assert r.files_changed == []


class TestTheParentSeesClaimAndEvidenceApart:
    def test_spawn_returns_the_claim_beside_the_evidence(self, full):
        root, stub, ws = full

        async def turn():
            session = await root.runtime.get_or_create_session("boss-s", "stub-model", str(ws))
            [ev async for ev in root.runtime.run_turn(session, "SCN{boss:real.txt}")]
        asyncio.run(turn())
        data = json.loads(stub.of("boss")[-1]["tool_results"][-1])["data"]
        assert "all green" in data["summary"]
        assert data["files"] == ["real.txt"]
        assert {"kind": "file", "ref": "real.txt"} in data["evidence"]
        assert {"kind": "test", "ref": "run_tests", "state": "none", "runs": 0} in data["evidence"]
        assert data["claimed_but_not_seen"] == ["fake.py"]
        assert "unverified" in data["note"]

    def test_a_background_childs_result_carries_the_same_evidence(self, full):
        root, _, _ = full
        from wisp.multi_agent.background import BackgroundAgentManager

        manager = BackgroundAgentManager(root.subagent_orchestrator, run_store=root.run_store)

        async def go():
            launched = await manager.launch(_contract("bg", "SCN{claim:bg.txt}"))
            return await manager.result(launched["agent_id"], wait_seconds=30)
        payload = asyncio.run(go())["result"]
        assert payload["files"] == ["bg.txt"] and payload["claimed_but_not_seen"] == ["fake.py"]
        assert any(e["kind"] == "test" for e in payload["evidence"])


class TestTheReplyIsScanned:
    def test_instructions_in_a_reply_are_flagged_and_still_delivered(self, full):
        root, _, _ = full
        from wisp.multi_agent.handback import handback

        r = asyncio.run(root.subagent_orchestrator.run(_contract("i", "SCN{inject:x.txt}")))
        out = handback(r)
        assert out["flags"]["verdict"] == "block" and "ignore-previous" in out["flags"]["markers"]
        assert "delete the repository" in out["summary"], "a finding labels the reply; it never drops it"

    def test_a_clean_reply_has_no_flags(self):
        from wisp.multi_agent.handback import handback

        assert "flags" not in handback(SimpleNamespace(output="Done. All tests pass.", success=True))


class TestTestEvidence:
    @pytest.mark.parametrize("observed, state", [
        ([], "none"),
        ([{"tool": "run_tests", "green": False}], "red"),
        ([{"tool": "run_tests", "green": True}], "green"),
        ([{"tool": "run_tests", "green": True}, {"tool": "write_file", "path": "a", "ok": True}], "stale"),
    ])
    def test_the_last_test_run_decides(self, observed, state):
        from wisp.multi_agent.handback import handback

        out = handback(SimpleNamespace(output="tests pass", success=True, observations=observed))
        [test] = [e for e in out["evidence"] if e["kind"] == "test"]
        assert test["state"] == state
