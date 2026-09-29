"""N6 closed loop: incidents from alerts, agent dispatch by tier, the agent API, the MCP proxy."""

from __future__ import annotations

import socket
import time

import pytest

from wisp_net.loop.watcher import Watcher
from wisp_net.service import NetService


class FakeAgent:
    def __init__(self):
        self.calls = []

    def __call__(self, incident, prompt, env):
        self.calls.append((incident.incident_id, prompt, dict(env)))
        return {"exit_code": 0, "output_tail": "diagnosed", "stderr_tail": ""}


def _lab(**kw):
    return NetService(warmup_s=10, **kw)


class TestWatcher:
    def test_a_failure_and_its_consequences_are_one_incident_run_read_only(self):
        s, agent = _lab(), FakeAgent()
        try:
            s.watcher = Watcher(s, agent, "diagnose", debounce_s=10)
            s.inject("link_down", "leaf1:Ethernet49")
            s.advance(30)
            s.watcher.join()
            (incident,) = s.watcher.incidents
            rules = {(a["rule"], a["device"]) for a in incident.alerts}
            assert {("interface_down", "spine1"), ("bgp_session_down", "leaf1"), ("bgp_session_down", "spine1")} <= rules
            (call,) = agent.calls
            assert call[2]["WISP_PERMISSION_MODE"] == "read_only" and "Do not propose" in call[1]
            assert incident.status == "finished"
            kinds = [r.kind for r in s.ledger.records()]
            assert kinds[-3:] == ["incident_opened", "agent_dispatched", "agent_finished"]
        finally:
            s.stop()

    def test_cooldown_suppresses_a_repeat_and_observe_never_dispatches(self):
        s, agent = _lab(), FakeAgent()
        try:
            s.watcher = Watcher(s, agent, "diagnose", debounce_s=5, cooldown_s=600)
            fid = s.inject("bgp_down", "leaf3:10.255.4.1")["fault_id"]
            s.advance(20)
            s.clear_fault(fid)
            s.advance(400)
            s.inject("bgp_down", "leaf3:10.255.4.1")
            s.advance(20)
            s.watcher.join()
            assert [i.status for i in s.watcher.incidents] == ["finished", "suppressed"]
            assert len(agent.calls) == 1
        finally:
            s.stop()
        s, agent = _lab(), FakeAgent()
        try:
            s.watcher = Watcher(s, agent, "observe", debounce_s=5)
            s.inject("bgp_down", "leaf3:10.255.4.1")
            s.advance(20)
            assert [i.status for i in s.watcher.incidents] == ["recorded"] and agent.calls == []
        finally:
            s.stop()

    def test_alerts_open_at_start_are_adopted_and_minor_ones_ignored(self):
        s, agent = _lab(), FakeAgent()
        try:
            s.inject("bgp_down", "leaf3:10.255.4.1")
            s.inject("cpu_spike", "core1")
            s.advance(15)
            s.watcher = Watcher(s, agent, "propose", debounce_s=5)
            s.advance(10)
            s.watcher.join()
            (incident,) = s.watcher.incidents
            assert {a["rule"] for a in incident.alerts} == {"bgp_session_down"}, "cpu_high is minor"
            assert "net_what_if" in agent.calls[0][1] and "Do not apply" in agent.calls[0][1]
        finally:
            s.stop()

    def test_alerts_arriving_within_the_debounce_window_are_one_incident(self):
        s, agent = _lab(), FakeAgent()
        try:
            s.watcher = Watcher(s, agent, "diagnose", debounce_s=20)
            s.inject("bgp_down", "leaf3:10.255.4.1")
            s.advance(10)
            s.inject("mgmt_unreachable", "core2")
            s.advance(20)
            s.watcher.join()
            (incident,) = s.watcher.incidents
            assert {a["rule"] for a in incident.alerts} == {"bgp_session_down", "device_unreachable"}
        finally:
            s.stop()

    def test_minor_alerts_do_not_open_incidents(self):
        s, agent = _lab(), FakeAgent()
        try:
            s.watcher = Watcher(s, agent, "diagnose", debounce_s=5)
            s.inject("cpu_spike", "core1")
            s.advance(30)
            assert s.watcher.incidents == [] and agent.calls == []
        finally:
            s.stop()

    def test_act_tier_is_not_read_only(self):
        s, agent = _lab(), FakeAgent()
        try:
            s.watcher = Watcher(s, agent, "act", debounce_s=5)
            s.inject("bgp_down", "leaf3:10.255.4.1")
            s.advance(20)
            s.watcher.join()
            assert agent.calls[0][2]["WISP_PERMISSION_MODE"] == "auto_edit"
        finally:
            s.stop()

    def test_unknown_tier(self):
        s = _lab()
        try:
            with pytest.raises(ValueError):
                Watcher(s, None, "autopilot")
        finally:
            s.stop()


class TestAgentApi:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from wisp_net.governance.cockpit import create_app

        s = _lab()
        with TestClient(create_app(s, "op-token", "agent-token")) as c:
            yield s, c
        s.stop()

    def test_the_agent_token_opens_tools_and_nothing_else(self, client):
        s, c = client
        agent = {"Authorization": "Bearer agent-token"}
        operator = {"Authorization": "Bearer op-token"}
        names = {t["name"] for t in c.get("/agent/tools", headers=agent).json()}
        assert "net_apply_change" in names and not any(n.startswith("lab_") for n in names)
        reply = c.post("/agent/tools/net_status", headers=agent, json={}).json()
        assert reply["isError"] is False and '"devices":8' in reply["content"][0]["text"]
        assert c.post("/kill-switch", headers=agent, json={"engaged": True, "operator": "x"}).status_code == 401
        assert c.post("/approvals/R0001/grant", headers=agent, json={"operator": "x"}).status_code == 401
        assert c.post("/agent/tools/net_status", headers=operator, json={}).status_code == 401
        assert c.post("/agent/tools/lab_inject_fault", headers=agent,
                      json={"arguments": {"kind": "link_down", "target": "leaf1:Ethernet49"}}).json()["isError"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_mcp_proxy_serves_the_shared_platform(tmp_path, monkeypatch):
    from wisp_net.governance.cockpit import CockpitServer
    from wisp_net.mcp_server import McpServer, RemotePlatform

    monkeypatch.setenv("WISP_NET_HOME", str(tmp_path))
    s, port = _lab(), _free_port()
    cockpit = CockpitServer(s, port)
    cockpit.start()
    try:
        remote = RemotePlatform(f"http://127.0.0.1:{port}", cockpit.agent_token, timeout_s=30)
        for _ in range(50):
            if not remote.call("net_status", {})["isError"]:
                break
            time.sleep(0.1)
        proxy = McpServer(None, lab_control=True, remote=remote)
        listed = proxy.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        assert not any(t["name"].startswith("lab_") for t in listed), "a proxy never exposes lab control"
        s.inject("bgp_down", "leaf3:10.255.4.1")
        s.advance(10)
        reply = proxy.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "net_alerts", "arguments": {"min_severity": "major"}}})
        assert "bgp_session_down" in reply["result"]["content"][0]["text"], "the proxy sees the platform's network"
    finally:
        cockpit.stop()
        s.stop()
    down = RemotePlatform(f"http://127.0.0.1:{_free_port()}", "t", timeout_s=2).call("net_status", {})
    assert down["isError"] and "unreachable" in down["content"][0]["text"]
