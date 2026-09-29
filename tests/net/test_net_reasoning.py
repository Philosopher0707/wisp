"""N2 reasoning layer: domain analyses, the intent compiler, and the domain-agent skills."""

from __future__ import annotations

import pytest

from wisp_net.reasoning.intents import IntentError
from wisp_net.service import NetService, load_scenario

SCENARIOS = "wisp_net/scenarios/{}.json"


def _lab(scenario: str | None = None, seconds: float = 0.0, warmup: float = 30.0) -> NetService:
    s = NetService(scenario=load_scenario(SCENARIOS.format(scenario)) if scenario else None,
                   warmup_s=0 if scenario else warmup)
    if seconds:
        s.advance(seconds)
    return s


class TestPredictiveDiagnostics:
    def test_a_healthy_lab_forecasts_nothing(self):
        s = _lab(warmup=600)
        try:
            assert s.optics_forecast() == []
        finally:
            s.stop()

    def test_a_degrading_optic_is_forecast_before_it_fails(self):
        s = _lab("optic-degradation", 300)
        try:
            (row,) = s.optics_forecast()
            assert row["port"] == "leaf2:Ethernet50" and row["fit_r2"] > 0.99
            assert row["slope_db_per_hour"] == pytest.approx(-90, abs=5)
            assert row["minutes_to_loss_of_signal"] is not None and row["minutes_to_loss_of_signal"] > 10
            assert row["risk"] in ("high", "critical")
        finally:
            s.stop()

    def test_optics_errors_are_told_apart_from_port_errors(self):
        s = _lab("optic-degradation", 700)
        try:
            v = s.error_correlation("leaf2", "Ethernet50")
            assert v["verdict"].startswith("physical layer, optics") and v["spearman_rho_below_onset"] < -0.9
        finally:
            s.stop()
        s = _lab("optic-degradation", 480)
        try:
            assert s.error_correlation("leaf2", "Ethernet50")["verdict"].startswith("consistent with optics but not")
        finally:
            s.stop()
        s = _lab(warmup=10)
        try:
            s.inject("crc_surge", "leaf3:Ethernet49", error_rate=1e-4)
            s.advance(300)
            assert s.error_correlation("leaf3", "Ethernet49")["verdict"].startswith("not optics")
            assert s.error_correlation("leaf3", "Ethernet50")["verdict"] == "no FCS errors in the window"
        finally:
            s.stop()


class TestCompliance:
    def test_drift_finds_exactly_the_out_of_band_changes(self):
        s = _lab(warmup=10)
        try:
            assert s.config_drift()["compliant"] is True
        finally:
            s.stop()
        s = _lab("config-drift", 90)
        try:
            d = s.config_drift()
            assert sorted((f["device"], f["rule"]) for f in d["findings"]) == [
                ("leaf4", "SEC-LOGIN-BANNER"), ("leaf4", "SEC-SSH-V2-ONLY"), ("spine1", "OPS-NTP-SERVERS")]
            assert d["by_class"] == {"security": 3}
            assert {e["subject"] for e in s.events(kind="config_commit")} == {"jdoe"}
        finally:
            s.stop()

    def test_advisories_match_reported_versions(self):
        s = _lab(warmup=10)
        try:
            exposed = {(e["device"], e["advisory"]) for e in s.advisories()["exposed"]}
            assert ("leaf1", "LAB-ADV-2026-0001") in exposed and ("spine1", "LAB-ADV-2026-0002") in exposed
            assert not any(dev.startswith("core") for dev, _ in exposed), "4.32.2 has no advisory"
        finally:
            s.stop()


class TestSecurityAndTraffic:
    def test_a_volumetric_surge_is_an_anomaly_and_a_quiet_lab_is_not(self):
        s = _lab(warmup=400)
        try:
            assert s.flow_anomalies() == []
            s.inject("congestion", "leaf1->leaf4", multiplier=40)
            s.advance(70)
            anomalies = s.flow_anomalies()
            assert anomalies and all(a["src"].startswith("10.1.0.") for a in anomalies)
            assert anomalies[0]["entering_at"][0].startswith("leaf1:Ethernet")
            te = s.te_assess()
            assert te["hot_ports"] and all(p.startswith("leaf4:Ethernet") for p in te["hot_ports"])
            assert te["drain_candidates"] == [] and "incast" in te["note"]
        finally:
            s.stop()


class TestIntentCompiler:
    @pytest.fixture(scope="class")
    def lab(self):
        s = _lab(warmup=30)
        yield s
        s.stop()

    def test_each_intent_compiles_to_a_verifiable_change(self, lab):
        cases = [
            ({"kind": "drain_link", "port": "leaf1:Ethernet49"}, 1),
            ({"kind": "restore_link", "port": "leaf1:Ethernet49"}, 1),
            ({"kind": "quarantine_host", "ip": "10.1.0.11"}, 1),
            ({"kind": "guard_zone", "zone": "pci", "allow": [{"proto": "tcp", "dport": "443"}]}, 6),
            ({"kind": "set_bgp_neighbor", "device": "leaf1", "neighbor": "10.255.0.1", "enabled": False}, 1),
        ]
        for intent, n_ops in cases:
            change = lab.compile_intent(intent)
            assert len(change["ops"]) == n_ops, intent
            report = lab.what_if(change)
            assert report["verification_passed"], (intent, [c for c in report["checks"] if not c["passed"]])

    def test_the_guard_keeps_bgp_and_closes_the_leak(self, lab):
        change = lab.compile_intent({"kind": "guard_zone", "zone": "pci", "allow": [{"proto": "tcp", "dport": "443"}]})
        assert change["ops"][0]["value"]["dport"] == "179", "BGP between fabric addresses comes first"
        seg = next(c for c in lab.what_if(change)["checks"] if c["name"] == "segmentation_no_new_leaks")
        assert "general -> pci" not in {v["rule"] for v in seg["baseline"]}

    @pytest.mark.parametrize("intent,msg", [
        ({"kind": "reboot"}, "unknown intent"),
        ({"kind": "drain_link", "port": "leaf1:Ethernet1"}, "not an up fabric link"),
        ({"kind": "drain_link", "port": "leaf9:Ethernet49"}, "no port"),
        ({"kind": "quarantine_host", "ip": "10.9.9.9"}, "no server"),
        ({"kind": "guard_zone", "zone": "nope"}, "no zone"),
        ({"kind": "guard_zone", "zone": "pci", "at": "middle"}, "destination or source"),
        ({"kind": "set_bgp_neighbor", "device": "leaf1", "neighbor": "1.2.3.4"}, "no BGP neighbor"),
    ])
    def test_intents_that_do_not_fit_the_network_are_refused(self, lab, intent, msg):
        with pytest.raises(IntentError, match=msg):
            lab.compile_intent(intent)

    def test_a_drain_that_would_isolate_a_device_is_refused(self):
        s = _lab(warmup=10)
        try:
            s.inject("link_down", "leaf1:Ethernet50")
            s.advance(10)
            with pytest.raises(IntentError, match="would isolate leaf1"):
                s.compile_intent({"kind": "drain_link", "port": "leaf1:Ethernet49"})
        finally:
            s.stop()


def test_domain_agent_skills_install_and_wisp_discovers_them(tmp_path):
    from wisp.skills import discover_skills
    from wisp_net.__main__ import main

    assert main(["install-skills", "--dest", str(tmp_path / ".agents" / "skills")]) == 0
    names = sorted(s.name for s in discover_skills(str(tmp_path)) if s.name.startswith("net-"))
    assert names == ["net-compliance-audit", "net-orchestrator", "net-predictive-diagnostics",
                     "net-security-sentinel", "net-traffic-engineering"]
    (tmp_path / ".agents" / "skills" / "net-orchestrator" / "SKILL.md").write_text("---\nname: mine\n---\nx")
    assert main(["install-skills", "--dest", str(tmp_path / ".agents" / "skills")]) == 1, "never overwrites unasked"


def test_reasoning_tools_are_reads_over_mcp():
    from wisp_net.mcp_server import McpServer

    s = _lab(warmup=10)
    try:
        tools = {t["name"]: t for t in McpServer(s).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        new = {"net_optics_forecast", "net_error_correlation", "net_config_drift", "net_advisories",
               "net_te_assess", "net_flow_anomalies", "net_compile_intent"}
        assert new <= set(tools) and all(tools[n]["annotations"]["readOnlyHint"] for n in new)
    finally:
        s.stop()


class TestDiagnosticsOnSyntheticSeries:
    """Cases the lab does not produce on its own: noise that is not a trend, and correlation
    at healthy power that is not an optic."""

    def _store(self, rx, fcs=None):
        from wisp_net.state.tsdb import MetricStore

        db = MetricStore()
        points = [("leaf1|if.rx_power_dbm|Ethernet49", float(t * 5), v) for t, v in enumerate(rx)]
        if fcs is not None:
            points += [("leaf1|if.fcs_per_s|Ethernet49", float(t * 5), v) for t, v in enumerate(fcs)]
        db.write_many(points)
        return db, float((len(rx) - 1) * 5)

    def test_noise_is_not_a_trend(self):
        from wisp_net.reasoning.diagnostics import optics_forecast

        noisy = [-2.0 if t % 2 else -3.2 for t in range(40)]
        noisy[-1] = -3.3
        db, now = self._store(noisy)
        assert optics_forecast(db, now) == [], "a sawtooth around -2.6 dBm has a poor fit and is not reported"

    def test_errors_at_healthy_power_are_not_an_optic(self):
        from wisp_net.reasoning.diagnostics import error_correlation

        rx = [-3.0 - 0.1 * t for t in range(40)]
        fcs = [10.0 + 5.0 * t for t in range(40)]
        db, now = self._store(rx, fcs)
        v = error_correlation(db, now, "leaf1", "Ethernet49")
        assert v["spearman_rho"] == -1.0 and v["errors_only_below_onset"] is False
        assert not v["verdict"].startswith("physical layer, optics"), "correlation above onset is not causation"
