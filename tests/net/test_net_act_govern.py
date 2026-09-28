"""N4 actuation and N5 governance: preconditions, commit-confirm, rollback, approvals, ledger, cockpit."""

from __future__ import annotations

import json

import pytest

from wisp_net.governance.control import APPROVAL_TTL_S, ApprovalError, ApprovalQueue
from wisp_net.governance.ledger import Ledger, LedgerCorrupt
from wisp_net.safety.policy import ChangePolicy
from wisp_net.service import NetService

IF = "/interfaces/interface[name={}]/config/{}"
ACL = "/acl/acl-sets/acl-set[name={n}][type=ACL_IPV4]/acl-entries/acl-entry[sequence-id={q}]/config"
BIND = "/acl/interfaces/interface[id={i}]/ingress-acl-sets/ingress-acl-set[set-name={n}][type=ACL_IPV4]/config/set-name"


def _lab(scenario=None, windows=False) -> NetService:
    s = NetService(warmup_s=30, scenario=scenario)
    if not windows:
        s.change_policy = ChangePolicy(windows=())
    return s


def _drain(port="Ethernet49", confidence=0.95):
    return {"intent": f"drain leaf1 {port}", "confidence": confidence,
            "ops": [{"device": "leaf1", "path": IF.format(port, "enabled"), "value": False}]}


def _describe(dev="leaf2", text="server (web)", confidence=0.95):
    return {"intent": "describe a port", "confidence": confidence,
            "ops": [{"device": dev, "path": IF.format("Ethernet1", "description"), "value": text}]}


def _enabled(s, dev, port):
    return s.net.devices[dev].config["interfaces"][port]["enabled"]


class TestLedger:
    def test_chain_detects_edits_deletions_and_reordering(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        led = Ledger(path)
        for i in range(4):
            led.append("event", {"i": i}, float(i))
        assert led.verify() == [] and len(Ledger(path)) == 4, "reloads intact"
        lines = path.read_text().splitlines()
        edited = json.loads(lines[1])
        edited["data"]["i"] = 99
        path.write_text("\n".join([lines[0], json.dumps(edited), *lines[2:]]) + "\n")
        with pytest.raises(LedgerCorrupt, match="record 2 .* modified"):
            Ledger(path)
        path.write_text("\n".join([lines[0], *lines[2:]]) + "\n")
        with pytest.raises(LedgerCorrupt, match="removed or reordered"):
            Ledger(path)

    def test_records_filter(self):
        led = Ledger()
        led.append("a", {"fingerprint": "f1"}, 0)
        led.append("b", {"fingerprint": "f2"}, 1)
        assert [r.kind for r in led.records(fingerprint="f2")] == ["b"]
        assert [r.seq for r in led.records(since_seq=1)] == [2]


class TestApprovals:
    def test_single_use_and_expiring(self):
        q = ApprovalQueue()
        req = q.request("fp", "intent", 0.0, ["low confidence"], {})
        assert q.request("fp", "intent", 1.0, [], {}).request_id == req.request_id, "one open request per change"
        with pytest.raises(ApprovalError, match="operator"):
            q.decide(req.request_id, True, "  ", 2.0)
        q.decide(req.request_id, True, "alice", 2.0)
        with pytest.raises(ApprovalError, match="not pending"):
            q.decide(req.request_id, False, "bob", 3.0)
        assert q.consume("fp", 4.0) is not None
        assert q.consume("fp", 5.0) is None, "a grant approves exactly one apply"
        late = q.request("fp2", "x", 0.0, [], {})
        q.decide(late.request_id, True, "alice", 0.0)
        assert q.consume("fp2", APPROVAL_TTL_S + 1) is None, "grants expire"


class TestActuation:
    def test_refusals_come_before_any_commit(self):
        s = _lab()
        try:
            assert "run net_what_if first" in s.apply_change("nope")["reasons"][0]
            bad = s.what_if({"intent": "web only uplink", "confidence": 0.95, "ops": [
                {"device": "leaf1", "path": ACL.format(n="W", q=10), "value": {"action": "permit", "proto": "tcp",
                                                                                 "dport": "443"}},
                {"device": "leaf1", "path": BIND.format(i="Ethernet50", n="W"), "value": "W"}]})
            r = s.apply_change(bad["fingerprint"])
            assert r["outcome"] == "refused" and "did not pass" in r["reasons"][0]
            assert "W" not in s.net.devices["leaf1"].config["acls"], "nothing was committed"
        finally:
            s.stop()

    def test_a_verified_drain_is_committed_confirmed_and_idempotent(self):
        s = _lab()
        try:
            fp = s.what_if(_drain())["fingerprint"]
            r = s.apply_change(fp, rationale="optic trending to loss of signal")
            assert r["outcome"] == "confirmed" and not r["triggers"]
            assert _enabled(s, "leaf1", "Ethernet49") is False
            assert r["diff"] == {"leaf1": [{"path": "/interfaces/Ethernet49/enabled", "from": True, "to": False}]}
            assert r["post_change"]["reachability"] == "24/24 delivered"
            assert "configuration changed since the verification" in s.apply_change(fp)["reasons"][0]
            again = s.what_if(_drain())["fingerprint"]
            assert s.apply_change(again)["outcome"] == "already_applied"
        finally:
            s.stop()

    def test_the_policy_is_decided_again_at_apply_time(self):
        s = _lab(windows=True)
        try:
            fp = s.what_if(_drain())["fingerprint"]
            r = s.apply_change(fp)
            assert r["outcome"] == "refused" and "Peak Enterprise Hours" in r["reasons"][0]
            assert _enabled(s, "leaf1", "Ethernet49") is True
        finally:
            s.stop()

    def test_low_confidence_waits_for_an_operator_and_the_grant_is_single_use(self):
        s = _lab()
        try:
            fp = s.what_if(_describe(confidence=0.6))["fingerprint"]
            first = s.apply_change(fp)
            assert first["outcome"] == "awaiting_approval"
            request = first["approval"]["request_id"]
            s.operator_decide(request, True, "alice", "reviewed the diff")
            done = s.apply_change(fp)
            assert done["outcome"] == "confirmed" and done["approval"]["decided_by"] == "alice"
            fp2 = s.what_if(_describe(text="changed again", confidence=0.6))["fingerprint"]
            assert s.apply_change(fp2)["outcome"] == "awaiting_approval", "a new change needs its own approval"
        finally:
            s.stop()

    def test_the_kill_switch_stops_everything(self):
        s = _lab()
        try:
            s.operator_kill_switch(True, "bob", "incident 42")
            fp = s.what_if(_describe())["fingerprint"]
            assert "kill switch is engaged" in s.apply_change(fp)["reasons"][0]
            s.operator_kill_switch(False, "bob", "resolved")
            assert s.apply_change(s.what_if(_describe())["fingerprint"])["outcome"] == "confirmed"
        finally:
            s.stop()

    def test_management_loss_in_the_confirm_window_rolls_back(self):
        s = _lab(scenario=[{"at": 40, "inject": "mgmt_unreachable", "target": "leaf2"}])
        try:
            fp = s.what_if(_describe())["fingerprint"]
            r = s.apply_change(fp)
            assert r["outcome"] == "rolled_back"
            assert any("management plane of leaf2 unreachable" in t for t in r["triggers"])
            assert s.net.devices["leaf2"].config["interfaces"]["Ethernet1"]["description"] != "server (web)"
        finally:
            s.stop()

    def test_undeclared_reachability_loss_rolls_back(self):
        s = _lab(scenario=[{"at": 40, "inject": "link_down", "target": "leaf1:Ethernet50"}])
        try:
            r = s.apply_change(s.what_if(_drain("Ethernet49"))["fingerprint"])
            assert r["outcome"] == "rolled_back"
            assert any("lost reachability" in t for t in r["triggers"])
            assert _enabled(s, "leaf1", "Ethernet49") is True, "the drain was reverted: leaf1 keeps an uplink"
        finally:
            s.stop()

    def test_operator_can_revert_a_confirmed_change(self):
        s = _lab()
        try:
            r = s.apply_change(s.what_if(_drain())["fingerprint"])
            s.operator_rollback(r["apply_id"], "carol", "optic replaced")
            assert _enabled(s, "leaf1", "Ethernet49") is True
            with pytest.raises(KeyError):
                s.operator_rollback(r["apply_id"], "carol", "twice")
        finally:
            s.stop()

    def test_everything_lands_in_an_intact_ledger_and_explains_itself(self):
        s = _lab()
        try:
            fp = s.what_if(_drain())["fingerprint"]
            s.apply_change(fp, rationale="rx power -13 dBm and falling")
            view = s.ledger_view(limit=100)
            assert view["intact"]
            assert [r["kind"] for r in view["records"]] == ["change_verified", "change_committed", "change_confirmed"]
            story = s.explain(fp)["narrative"]
            assert "verification passed" in story[0] and "rx power -13 dBm" in story[1] and "confirmed" in story[2]
        finally:
            s.stop()


class TestAgentBoundary:
    def test_the_agent_can_apply_but_never_approve(self):
        from wisp_net.mcp_server import McpServer

        s = _lab()
        try:
            tools = {t["name"]: t for t in McpServer(s).handle(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
            apply = tools["net_apply_change"]["annotations"]
            assert apply["readOnlyHint"] is False and apply["destructiveHint"] is True
            writers = [n for n, t in tools.items() if not t["annotations"]["readOnlyHint"]]
            assert writers == ["net_apply_change"], "no tool grants approvals or flips the kill switch"
        finally:
            s.stop()


class TestCockpit:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from wisp_net.governance.cockpit import create_app

        s = _lab()
        with TestClient(create_app(s, "t0ken")) as c:
            yield s, c
        s.stop()

    def test_every_route_needs_the_token(self, client):
        _, c = client
        assert c.get("/status").status_code == 401
        assert c.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/status", headers={"Authorization": "Bearer t0ken"}).status_code == 200

    def test_an_operator_grants_through_the_cockpit(self, client):
        s, c = client
        auth = {"Authorization": "Bearer t0ken"}
        fp = s.what_if(_describe(confidence=0.5))["fingerprint"]
        request = s.apply_change(fp)["approval"]["request_id"]
        pending = c.get("/approvals", params={"status": "pending"}, headers=auth).json()
        assert [p["request_id"] for p in pending] == [request]
        assert c.post(f"/approvals/{request}/grant", json={"operator": ""}, headers=auth).status_code == 422
        assert c.post(f"/approvals/{request}/grant", json={"operator": "alice"}, headers=auth).json()["status"] == \
            "granted"
        assert c.post(f"/approvals/{request}/grant", json={"operator": "alice"}, headers=auth).status_code == 409
        assert s.apply_change(fp)["outcome"] == "confirmed"
        assert c.get("/ledger", headers=auth).json()["intact"] is True
        assert c.post("/kill-switch", json={"engaged": True, "operator": "bob", "reason": "drill"},
                      headers=auth).json()["engaged"] is True
        assert c.get("/status", headers=auth).json()["kill_switch"]["engaged"] is True

    def test_live_ledger_feed(self, client):
        s, c = client
        with pytest.raises(Exception):
            with c.websocket_connect("/events?token=wrong") as ws:
                ws.receive_json()
        s.what_if(_describe())
        with c.websocket_connect("/events?token=t0ken&since=0") as ws:
            assert ws.receive_json()["kind"] == "change_verified"
