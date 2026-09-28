"""The simulated world, and what telemetry makes of it: routing, faults, the twin, alerts, the service."""

from __future__ import annotations

import pytest

from wisp_net.service import NetService
from wisp_net.sim.gnmi import SimGnmi
from wisp_net.sim.network import BGP_ESTABLISH_DELAY_S, DeviceUnreachable, SimNetwork
from wisp_net.sim.topology import build_lab
from wisp_net.state.twin import DigitalTwin


def _net(**kw) -> SimNetwork:
    return SimNetwork(build_lab(**kw), seed=3)


def _hops(net: SimNetwork, dev: str, prefix: str) -> list[str]:
    return [peer for _, peer, _ in net.devices[dev].rib[prefix].next_hops]


class TestRouting:
    def test_fabric_converges_with_ecmp(self):
        net = _net()
        assert all(s.state == "ESTABLISHED" for s in net.sessions.values())
        assert _hops(net, "leaf1", "10.2.0.0/24") == ["spine1", "spine2"]
        assert net.devices["leaf1"].rib["10.2.0.0/24"].as_path == (65100, 65202)
        assert _hops(net, "leaf1", "198.51.100.0/24") == ["spine1", "spine2"]

    def test_as_path_loop_prevention_keeps_spines_from_transiting_each_other(self):
        net = _net()
        net.inject("link_down", "leaf2:Ethernet49")
        net.step(1)
        assert "10.2.0.0/24" not in net.devices["spine1"].rib, \
            "spine1 must not learn leaf2 via a core: the path would carry its own AS 65100"
        assert _hops(net, "leaf1", "10.2.0.0/24") == ["spine2"]

    def test_link_recovery_waits_for_bgp(self):
        net = _net()
        fid = net.inject("link_down", "leaf1:Ethernet49")
        net.step(1)
        net.clear(fid)
        net.step(1)
        assert _hops(net, "leaf1", "10.2.0.0/24") == ["spine2"], "link up is not session up"
        for _ in range(int(BGP_ESTABLISH_DELAY_S) + 1):
            net.step(1)
        assert _hops(net, "leaf1", "10.2.0.0/24") == ["spine1", "spine2"]

    def test_export_policy_withdraws_a_prefix(self):
        net = _net()
        net.devices["leaf3"].config["bgp"]["export-deny"] = ["10.3.0.0/24"]
        net._routes_dirty = True
        net.step(1)
        assert "10.3.0.0/24" not in net.devices["leaf1"].rib

    def test_peer_as_mismatch_keeps_the_session_down(self):
        net = _net()
        net.devices["leaf1"].config["bgp"]["neighbors"]["10.255.0.1"]["peer-as"] = 64999
        for _ in range(5):
            net.step(1)
        assert net.sessions[("leaf1", "10.255.0.1")].state == "IDLE"
        assert net.sessions[("spine1", "10.255.0.0")].state == "IDLE", "the other side never sees an OPEN it accepts"

    def test_same_seed_same_world(self):
        a, b = _net(), _net()
        for net in (a, b):
            net.inject("optic_degrade", "leaf2:Ethernet50", rate_db_per_min=6)
            for _ in range(120):
                net.step(1)
        ca = a.devices["leaf2"].interfaces["Ethernet50"].counters
        cb = b.devices["leaf2"].interfaces["Ethernet50"].counters
        assert ca == cb and ca.in_fcs_errors > 0

    def test_clone_is_independent(self):
        net = _net()
        twin = net.clone()
        twin.inject("link_down", "leaf1:Ethernet49")
        twin.step(1)
        assert net.devices["leaf1"].interfaces["Ethernet49"].oper_up
        assert not twin.devices["leaf1"].interfaces["Ethernet49"].oper_up

    def test_hosts_cannot_exceed_line_rate(self):
        net = _net()
        net.inject("congestion", "leaf1->leaf4", multiplier=200)
        net.step(1)
        for port in ("Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"):
            iface = net.devices["leaf1"].interfaces[port]
            assert iface.rx_bps <= iface.speed_bps + 1
        assert net.devices["leaf4"].interfaces["Ethernet1"].counters.out_discards > 0

    @pytest.mark.parametrize("kind,target", [("nope", "leaf1"), ("link_down", "leaf1:Ethernet7"),
                                             ("bgp_down", "leaf1:1.2.3.4"), ("congestion", "leaf1")])
    def test_bad_faults_are_rejected(self, kind, target):
        with pytest.raises(ValueError):
            _net().inject(kind, target)

    def test_unreachable_management_plane(self):
        net = _net()
        net.inject("mgmt_unreachable", "spine2")
        gnmi = SimGnmi(net)
        with pytest.raises(DeviceUnreachable):
            gnmi.get("spine2", "/system")
        assert {n.target for n in gnmi.get("*", "/system/state/hostname")} == \
            set(net.devices) - {"spine2"}


class TestTwin:
    def _twin(self, net: SimNetwork) -> DigitalTwin:
        twin = DigitalTwin()
        gnmi = SimGnmi(net)
        by: dict[str, list] = {}
        for n in gnmi.subscribe_sample(["/"]):
            by.setdefault(n.target, []).append(n)
        for dev in net.devices:
            if dev in by:
                twin.ingest(dev, by[dev], net.now)
            else:
                twin.mark_unreachable(dev, net.now)
        twin.rebuild(net.now)
        return twin

    def test_built_from_telemetry_alone(self):
        net = _net()
        twin = self._twin(net)
        assert len(twin.links) == len(net.links)
        assert twin.devices["leaf1"].bgp_global["as"] == 65201
        assert twin.lookup("leaf1", "10.2.0.77") == "10.2.0.0/24"
        trace = twin.trace("leaf1", "10.2.0.77")
        assert trace.outcome == "delivered"
        assert sorted(trace.paths) == [("leaf1", "spine1", "leaf2"), ("leaf1", "spine2", "leaf2")]
        assert twin.physical_paths("leaf1", "core2") == [["leaf1", "spine1", "core2"], ["leaf1", "spine2", "core2"]]
        assert twin.reachability()["broken"] == []

    def test_withdrawn_prefix_is_a_blackhole_with_a_reason(self):
        net = _net()
        net.devices["leaf3"].config["bgp"]["export-deny"] = ["10.3.0.0/24"]
        net._routes_dirty = True
        net.step(1)
        twin = self._twin(net)
        trace = twin.trace("leaf1", "10.3.0.9")
        assert trace.outcome == "blackhole"
        assert "leaf1 has no route" in trace.failures[0]
        broken = twin.reachability()["broken"]
        assert {b["prefix"] for b in broken} == {"10.3.0.0/24"}

    def test_silent_device_is_traversed_on_last_known_state(self):
        net = _net()
        twin = self._twin(net)
        net.inject("mgmt_unreachable", "spine2")
        by: dict[str, list] = {}
        for n in SimGnmi(net).subscribe_sample(["/"]):
            by.setdefault(n.target, []).append(n)
        for dev in net.devices:
            if dev in by:
                twin.ingest(dev, by[dev], net.now)
            else:
                twin.mark_unreachable(dev, net.now)
        twin.rebuild(net.now)
        trace = twin.trace("leaf1", "10.2.0.9")
        assert trace.outcome == "delivered" and trace.stale == ("spine2",)
        assert twin.reachability()["verified_on_stale_state"] == ["spine2"]

    def test_snapshot_diff_names_what_changed(self):
        net = _net()
        twin = self._twin(net)
        before = twin.snapshot(net.now, "before")
        net.inject("link_down", "leaf1:Ethernet49")
        net.step(1)
        by: dict[str, list] = {}
        for n in SimGnmi(net).subscribe_sample(["/"]):
            by.setdefault(n.target, []).append(n)
        for dev, notes in by.items():
            twin.ingest(dev, notes, net.now)
        assert twin.rebuild(net.now) is True
        after = twin.snapshot(net.now, "after")
        diff = twin.diff(before, after)
        assert diff["links"]["changed"] == [{"key": "leaf1:Ethernet49 <-> spine1:Ethernet1",
                                             "from": {"up": True, "speed_bps": 100e9},
                                             "to": {"up": False, "speed_bps": 100e9}}]
        assert {c["key"] for c in diff["bgp"]["changed"]} == {"leaf1|10.255.0.1", "spine1|10.255.0.0"}


class TestServiceAndAlerts:
    def test_healthy_lab_raises_no_alerts(self):
        s = NetService(warmup_s=600)
        try:
            assert s.alert_list("all") == []
            assert s.reachability()["delivered"] == 24
        finally:
            s.stop()

    def test_optic_degradation_progresses_from_warning_to_errors(self):
        s = NetService(scenario=[{"at": 0, "inject": "optic_degrade", "target": "leaf2:Ethernet50",
                                  "params": {"rate_db_per_min": 2.0}}], warmup_s=0)
        try:
            s.advance(270)
            rules = {a["rule"]: a for a in s.alert_list()}
            assert set(rules) == {"rx_power_low"}, "power warns before any bit errors"
            s.advance(180)
            rules = {a["rule"]: a for a in s.alert_list()}
            assert {"rx_power_low", "fcs_errors"} <= set(rules)
            assert rules["fcs_errors"]["evidence"]["rx_power_dbm"] < -12
            series = s.metrics("leaf2|if.rx_power_dbm|Ethernet50", window_s=600)["series"]
            only = series["leaf2|if.rx_power_dbm|Ethernet50"]
            assert only["max"] == -2.5 and only["last"] < -12
            kinds = {e["kind"] for e in s.events(device="leaf2")}
            assert {"rx_power_low", "fcs_errors"} <= kinds
        finally:
            s.stop()

    def test_a_flapping_link_is_one_alert_with_a_count(self):
        s = NetService(scenario=[{"at": 0, "inject": "link_flap", "target": "spine1:Ethernet31",
                                  "params": {"period_s": 40}}], warmup_s=0)
        try:
            s.advance(400)
            downs = [a for a in s.alert_list("all") if a["rule"] == "interface_down" and a["device"] == "spine1"]
            assert len(downs) == 1 and downs[0]["occurrences"] >= 5
            assert any(a["rule"] == "bgp_flapping" for a in s.alert_list())
        finally:
            s.stop()

    def test_alerts_resolve_when_the_condition_clears(self):
        s = NetService(warmup_s=10)
        try:
            fid = s.inject("bgp_down", "leaf3:10.255.4.1")["fault_id"]
            s.advance(10)
            assert {a["device"] for a in s.alert_list() if a["rule"] == "bgp_session_down"} == {"leaf3", "spine1"}
            s.clear_fault(fid)
            s.advance(15)
            assert [a for a in s.alert_list() if a["rule"] == "bgp_session_down"] == []
            assert {a["state"] for a in s.alert_list("resolved")} == {"resolved"}
        finally:
            s.stop()

    def test_changes_names_what_moved_since_a_point_in_time(self):
        s = NetService(warmup_s=30)
        try:
            s.inject("link_down", "leaf1:Ethernet49")
            s.advance(10)
            diff = s.changes(since_s=20)
            assert diff["to"] == "now"
            assert [c["key"] for c in diff["links"]["changed"]] == ["leaf1:Ethernet49 <-> spine1:Ethernet1"]
            assert {c["key"] for c in diff["bgp"]["changed"]} == {"leaf1|10.255.0.1", "spine1|10.255.0.0"}
        finally:
            s.stop()

    def test_counter_reset_is_not_a_negative_rate(self):
        s = NetService(warmup_s=20)
        try:
            s.net.devices["leaf1"].interfaces["Ethernet49"].counters.in_octets = 0
            s.advance(5)
            assert s.collector.last_view["interfaces"]["leaf1"]["Ethernet49"]["rx_bps"] == 0.0
            s.advance(5)
            assert s.collector.last_view["interfaces"]["leaf1"]["Ethernet49"]["rx_bps"] > 0
        finally:
            s.stop()

    def test_reads_are_bounded_and_validated(self):
        s = NetService(warmup_s=10)
        try:
            with pytest.raises(KeyError):
                s.interfaces("leaf99")
            with pytest.raises(ValueError):
                s.alert_list(state="bogus")
            assert len(s.gnmi_get("leaf1", "/", limit=5)) == 5
            assert s.top_flows(n=3) and len(s.top_flows(n=3)) == 3
            assert s.routes("leaf1", "10.2.0.0/24")[0]["as_path"] == "65100 65202"
        finally:
            s.stop()
