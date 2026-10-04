"""N3 safety layer: ACL algebra, device-side Set, the change model, verification, what-if, policy."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from wisp_net.acl import (
    ANY_PORT,
    ANY_PROTO,
    UNIVERSE,
    AclError,
    dead_rules,
    describe_box,
    evaluate,
    parse_acl,
    prefix_interval,
    subtract,
    volume,
)
from wisp_net.safety.change import ChangeError, ChangeSet
from wisp_net.safety.policy import ChangePolicy, Window, decide
from wisp_net.safety.verify import SegmentationPolicy, check_loops, segmentation_violations
from wisp_net.safety.whatif import what_if
from wisp_net.service import NetService
from wisp_net.sim.config import SetError, apply_set
from wisp_net.sim.network import SimNetwork
from wisp_net.sim.topology import build_lab
from wisp_net.state.twin import DigitalTwin

IF = "/interfaces/interface[name={}]/config/enabled"
ACL = "/acl/acl-sets/acl-set[name={n}][type=ACL_IPV4]/acl-entries/acl-entry[sequence-id={q}]/config"
BIND = "/acl/interfaces/interface[id={i}]/ingress-acl-sets/ingress-acl-set[set-name={n}][type=ACL_IPV4]/config/set-name"
EXPORT_DENY = "/network-instances/network-instance[name=default]/policy/export-deny[prefix={}]/config/prefix"


class TestAclAlgebra:
    RULES = [
        {"seq": 10, "action": "permit", "proto": "tcp", "dst": "10.3.0.0/24", "dport": "443"},
        {"seq": 20, "action": "deny", "dst": "10.3.0.0/24"},
        {"seq": 30, "action": "permit", "proto": "tcp", "dst": "10.3.0.10/32", "dport": "22"},
        {"seq": 40, "action": "permit"},
        {"seq": 50, "action": "deny", "proto": "udp", "dst": "10.9.0.0/16"},
    ]

    def test_first_match_is_exact_and_exhaustive(self):
        rules = parse_acl(self.RULES)
        space = (prefix_interval("10.1.0.0/24"), prefix_interval("10.3.0.0/24"), ANY_PROTO, ANY_PORT)
        permitted, denied = evaluate(rules, space)
        assert [describe_box(b) for b in permitted] == ["tcp 10.1.0.0/24 -> 10.3.0.0/24 dport 443"]
        assert sum(map(volume, permitted)) + sum(map(volume, denied)) == volume(space)

    def test_no_rule_means_implicit_deny(self):
        permitted, denied = evaluate([], UNIVERSE)
        assert permitted == [] and denied == [UNIVERSE]

    def test_dead_rules_are_found_by_union_not_just_by_one_rule(self):
        findings = {f.rule.seq: (f.kind, [r.seq for r in f.covered_by]) for f in dead_rules(parse_acl(self.RULES))}
        assert findings == {30: ("shadowed", [20]), 50: ("shadowed", [40])}
        split = parse_acl([{"seq": 1, "action": "deny", "dport": "0-400"},
                           {"seq": 2, "action": "deny", "dport": "401-65535"},
                           {"seq": 3, "action": "permit", "proto": "tcp", "dport": "300-500"}])
        assert [(f.rule.seq, f.kind, [r.seq for r in f.covered_by]) for f in dead_rules(split)] == \
            [(3, "shadowed", [1, 2])], "300-500 is covered by the union of two earlier rules, neither alone"

    def test_subtract_is_disjoint(self):
        a = UNIVERSE
        b = (prefix_interval("10.0.0.0/8"), prefix_interval("10.1.0.0/16"), (6, 6), (443, 443))
        pieces = subtract(a, b)
        assert sum(map(volume, pieces)) == volume(a) - volume(b)

    @pytest.mark.parametrize("entry", [{"seq": 1, "action": "allow"}, {"seq": 1, "action": "deny", "dst": "10.0.0/33"},
                                       {"seq": 1, "action": "deny", "dport": "70000"}, {"action": "deny"},
                                       {"seq": 1, "action": "deny", "proto": "gre?"}])
    def test_bad_entries_are_rejected(self, entry):
        with pytest.raises(AclError):
            parse_acl([entry])

    def test_a_port_with_any_protocol_means_tcp_and_udp(self):
        (rule,) = parse_acl([{"seq": 1, "action": "permit", "dport": "53"}])
        assert [b[2] for b in rule.boxes()] == [(6, 6), (17, 17)]


class TestDeviceSet:
    def test_a_transaction_is_atomic(self):
        net = SimNetwork(build_lab(), seed=3)
        before = copy.deepcopy(net.devices["leaf1"].config)
        with pytest.raises(SetError, match="mtu"):
            apply_set(net.devices["leaf1"].config, [(IF.format("Ethernet49"), False),
                                                    ("/interfaces/interface[name=Ethernet49]/config/mtu", 99)], [])
        assert net.devices["leaf1"].config == before, "a rejected Set changes nothing"

    def test_bindings_must_name_an_acl_that_has_entries(self):
        cfg = SimNetwork(build_lab(), seed=3).devices["leaf1"].config
        with pytest.raises(SetError, match="unknown ACL"):
            apply_set(cfg, [(BIND.format(i="Ethernet1", n="X"), "X")], [])
        with pytest.raises(SetError, match="does not match the key"):
            apply_set(cfg, [(ACL.format(n="X", q=10), {"action": "permit"}), (BIND.format(i="Ethernet1", n="X"), "Y")], [])
        bound = apply_set(cfg, [(ACL.format(n="X", q=10), {"action": "permit"}), (BIND.format(i="Ethernet1", n="X"), "X")], [])
        with pytest.raises(SetError, match="no entries"):
            apply_set(bound, [], [ACL.format(n="X", q=10)])
        unbound = apply_set(bound, [], [ACL.format(n="X", q=10), BIND.format(i="Ethernet1", n="X")])
        assert unbound["acls"] == {} and unbound["acl-bindings"] == {}, "unbind and delete together is valid"

    @pytest.mark.parametrize("path,value", [("/system/config/hostname", "x"),
                                            (IF.format("Ethernet99"), False),
                                            (IF.format("Ethernet1"), "no")])
    def test_unsupported_or_invalid_updates(self, path, value):
        with pytest.raises(SetError):
            apply_set(SimNetwork(build_lab(), seed=3).devices["leaf1"].config, [(path, value)], [])

    def test_an_acl_that_forgets_bgp_takes_the_session_down(self):
        net = SimNetwork(build_lab(), seed=3)
        cfg = apply_set(net.devices["leaf1"].config, [(ACL.format(n="W", q=10), {"action": "permit", "proto": "tcp",
                                                                                  "dport": "443"}),
                                                      (BIND.format(i="Ethernet49", n="W"), "W")], [])
        net.apply_config("leaf1", cfg)
        net.step(1)
        assert net.sessions[("leaf1", "10.255.0.1")].state == "IDLE"
        assert "CONFIG_COMMIT" in " ".join(net.drain_syslog())

    def test_ingress_acl_drops_its_share_of_traffic(self):
        net = SimNetwork(build_lab(), seed=3)
        cfg = apply_set(net.devices["leaf2"].config, [
            (ACL.format(n="D", q=10), {"action": "deny", "proto": "tcp", "dport": "8443"}),
            (ACL.format(n="D", q=20), {"action": "permit"}), (BIND.format(i="Ethernet1", n="D"), "D")], [])
        net.apply_config("leaf2", cfg)
        net.step(1)
        assert net.drops.get("leaf2:acl", 0) > 0
        assert net.devices["leaf2"].interfaces["Ethernet1"].counters.in_discards > 0


class TestChangeSet:
    @pytest.mark.parametrize("data,msg", [({"ops": [{"device": "a", "path": "/x", "value": 1}]}, "intent"),
                                          ({"intent": "x", "ops": []}, "non-empty"),
                                          ({"intent": "x", "ops": [{"device": "a", "path": "x", "value": 1}]}, "path"),
                                          ({"intent": "x", "ops": [{"device": "a", "path": "/x"}]}, "without a"),
                                          ({"intent": "x", "confidence": 2, "ops": [{"device": "a", "path": "/x",
                                                                                     "value": 1}]}, "0..1")])
    def test_validation(self, data, msg):
        with pytest.raises(ChangeError, match=msg):
            ChangeSet.from_dict(data)

    def test_fingerprint_is_what_it_does_not_why(self):
        ops = [{"device": "leaf1", "path": IF.format("Ethernet49"), "value": False}]
        a = ChangeSet.from_dict({"intent": "drain", "ops": ops, "confidence": 0.5})
        b = ChangeSet.from_dict({"intent": "another reason", "ops": ops, "confidence": 0.9})
        assert a.fingerprint == b.fingerprint

    def test_drained_versus_touched(self):
        change = ChangeSet.from_dict({"intent": "x", "ops": [
            {"device": "leaf1", "path": IF.format("Ethernet49"), "value": False},
            {"device": "leaf1", "path": BIND.format(i="Ethernet50", n="W"), "value": "W"}]})
        assert change.drained_ports() == {("leaf1", "Ethernet49")}
        assert change.touched_ports() == {("leaf1", "Ethernet49"), ("leaf1", "Ethernet50")}


class TestVerification:
    def test_a_forwarding_loop_is_found(self):
        twin = DigitalTwin()
        for dev in ("a", "b", "c"):
            twin.ingest(dev, [], 0)
        twin.devices["a"].networks = ["10.9.0.0/24"]
        twin.devices["c"].networks = ["10.8.0.0/24"]
        bgp = {"origin-protocol": "BGP"}
        twin.devices["a"].fib["10.8.0.0/24"] = {**bgp, "next-hops": [{"interface": "e1", "peer": "b"}]}
        twin.devices["b"].fib["10.8.0.0/24"] = {**bgp, "next-hops": [{"interface": "e1", "peer": "a"}]}
        check = check_loops(twin)
        assert not check.passed
        assert check.evidence[0]["outcome"] == "loop" and "a -> b -> a" in check.evidence[0]["why"][0]

    def test_segmentation_names_the_leaking_flows(self):
        s = NetService(warmup_s=10)
        try:
            policy = SegmentationPolicy.bundled()
            before = segmentation_violations(s.twin, policy)
            general = [v for v in before if v["rule"] == "general -> pci"]
            assert general and all(v["leaks"] and v["path"].endswith("leaf1") for v in general)
            assert not any("dport 443" in leak and leak.startswith("tcp") for v in general for leak in v["leaks"]), \
                "HTTPS is allowed by the policy, so it is never reported as a leak"
        finally:
            s.stop()


def _pci_guard(leaves=("leaf2", "leaf3", "leaf4"), ports=range(1, 5)):
    ops = []
    for leaf in leaves:
        ops += [{"device": leaf, "path": ACL.format(n="PCI", q=10),
                 "value": {"action": "permit", "proto": "tcp", "dst": "10.1.0.0/24", "dport": "443"}},
                {"device": leaf, "path": ACL.format(n="PCI", q=20), "value": {"action": "deny", "dst": "10.1.0.0/24"}},
                {"device": leaf, "path": ACL.format(n="PCI", q=30), "value": {"action": "permit"}}]
        ops += [{"device": leaf, "path": BIND.format(i=f"Ethernet{p}", n="PCI"), "value": "PCI"} for p in ports]
    return {"intent": "only HTTPS from general servers into the PCI zone", "confidence": 0.9, "ops": ops}


class TestWhatIf:
    @pytest.fixture(scope="class")
    def lab(self):
        s = NetService(warmup_s=30)
        yield s
        s.stop()

    def _run(self, lab, data):
        return what_if(lab.net, ChangeSet.from_dict(data), 30, lab.segmentation, lab.change_policy).to_dict()

    def _failed(self, report):
        return {c["name"] for c in report["checks"] if not c["passed"]}

    def test_a_drain_verifies_and_its_consequences_are_intended(self, lab):
        r = self._run(lab, {"intent": "drain", "ops": [{"device": "leaf1", "path": IF.format("Ethernet49"),
                                                        "value": False}]})
        assert r["verification_passed"], self._failed(r)
        assert r["new_alerts"] and all(a["intended"] for a in r["new_alerts"])

    def test_collateral_damage_is_caught(self, lab):
        r = self._run(lab, {"intent": "web only on the uplink", "ops": [
            {"device": "leaf1", "path": ACL.format(n="W", q=10), "value": {"action": "permit", "proto": "tcp",
                                                                             "dport": "443"}},
            {"device": "leaf1", "path": BIND.format(i="Ethernet50", n="W"), "value": "W"}]})
        assert not r["verification_passed"]
        assert {"bgp_sessions_kept", "no_new_major_alerts"} <= self._failed(r)

    def test_lost_reachability_fails_unless_it_is_the_intent(self, lab):
        change = {"intent": "stop advertising leaf3", "ops": [
            {"device": "leaf3", "path": EXPORT_DENY.format("10.3.0.0/24"), "value": "10.3.0.0/24"}]}
        r = self._run(lab, change)
        assert "no_new_blackholes" in self._failed(r)
        lost = next(c for c in r["checks"] if c["name"] == "no_new_blackholes")["evidence"]
        change["expected_unreachable"] = [[b["source"], b["prefix"]] for b in lost]
        assert "no_new_blackholes" not in self._failed(self._run(lab, change))

    def test_the_pci_fix_verifies_and_the_policy_limits_its_blast_radius(self, lab):
        r = self._run(lab, _pci_guard())
        assert r["verification_passed"], self._failed(r)
        seg = next(c for c in r["checks"] if c["name"] == "segmentation_no_new_leaks")
        assert not any(v["rule"] == "general -> pci" for v in seg["baseline"]), "the leak it targets is closed"
        assert r["policy"]["verdict"] == "deny"
        assert any("blast radius: 12 ports" in reason for reason in r["policy"]["reasons"])

    def test_acls_along_the_path_are_enforced_hop_by_hop(self, lab):
        """Guard the PCI zone at its own uplinks: the leak is cut at a transit hop, not at the source."""
        def guard():
            ops = [{"device": "leaf1", "path": ACL.format(n="PCI-IN", q=q), "value": v} for q, v in (
                (10, {"action": "permit", "proto": "tcp", "src": "10.255.0.0/16", "dst": "10.255.0.0/16", "dport": "179"}),
                (20, {"action": "permit", "proto": "tcp", "dst": "10.1.0.0/24", "dport": "443"}),
                (30, {"action": "deny", "dst": "10.1.0.0/24"}),
                (40, {"action": "permit"}))]
            ops += [{"device": "leaf1", "path": BIND.format(i=i, n="PCI-IN"), "value": "PCI-IN"}
                    for i in ("Ethernet49", "Ethernet50")]
            return {"intent": "guard the PCI zone at its uplinks", "confidence": 0.9, "ops": ops}

        r = self._run(lab, guard())
        assert r["verification_passed"], self._failed(r)
        seg = next(c for c in r["checks"] if c["name"] == "segmentation_no_new_leaks")
        remaining = {v["rule"] for v in seg["baseline"]}
        assert "general -> pci" not in remaining, "cut at leaf1's uplinks, a hop along every path"
        assert "internet -> pci" in remaining, "HTTPS from the internet still passes rule 20: reported, not hidden"

    def test_the_live_network_is_never_touched(self, lab):
        before = (copy.deepcopy(lab.net.devices["leaf1"].config), lab.net.now,
                  dict(lab.net.devices["leaf1"].rib))
        self._run(lab, {"intent": "drain", "ops": [{"device": "leaf1", "path": IF.format("Ethernet49"),
                                                    "value": False}]})
        assert (lab.net.devices["leaf1"].config, lab.net.now, dict(lab.net.devices["leaf1"].rib)) == before

    def test_an_invalid_change_is_not_run(self, lab):
        r = self._run(lab, {"intent": "bad", "ops": [{"device": "leaf1", "path": "/system/config/hostname",
                                                      "value": "x"}]})
        assert not r["applied"] and r["errors"] and r["policy"]["verdict"] == "deny"


class TestPolicy:
    MONDAY_NOON = datetime(2026, 1, 5, 12, tzinfo=timezone.utc).timestamp()
    SATURDAY = datetime(2026, 1, 10, 12, tzinfo=timezone.utc).timestamp()
    BLACK_FRIDAY_SAT = datetime(2026, 11, 28, 3, tzinfo=timezone.utc).timestamp()

    def _change(self, confidence=0.95, ports=1):
        return ChangeSet.from_dict({"intent": "x", "confidence": confidence, "ops": [
            {"device": "leaf1", "path": IF.format(f"Ethernet{p}"), "value": False} for p in range(1, ports + 1)]})

    def test_windows(self):
        policy = ChangePolicy.bundled()
        assert policy.active_windows(self.MONDAY_NOON) == ["Peak Enterprise Hours"]
        assert policy.active_windows(self.SATURDAY) == []
        assert policy.active_windows(self.BLACK_FRIDAY_SAT) == ["Black Friday / Cyber Week"]
        assert not Window("w", (0,), "09:00", "18:00").contains(datetime(2026, 1, 5, 18, tzinfo=timezone.utc).timestamp())

    def test_verdicts_in_order(self):
        policy = ChangePolicy.bundled()
        assert decide(self._change(), self.SATURDAY, True, policy).verdict == "allow"
        assert decide(self._change(confidence=0.8), self.SATURDAY, True, policy).verdict == "require_approval"
        assert decide(self._change(), self.SATURDAY, None, policy).verdict == "deny"
        assert decide(self._change(), self.SATURDAY, False, policy).verdict == "deny"
        assert decide(self._change(ports=11), self.SATURDAY, True, policy).verdict == "deny"
        assert decide(self._change(), self.MONDAY_NOON, True, policy).verdict == "deny"


def test_what_if_over_mcp_is_read_only_analysis():
    from wisp_net.mcp_server import McpServer

    s = NetService(warmup_s=10)
    try:
        mcp = McpServer(s)
        tools = {t["name"]: t for t in mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        assert {"net_what_if", "net_acl_audit", "net_segmentation_audit", "net_change_policy"} <= set(tools)
        assert tools["net_what_if"]["annotations"]["readOnlyHint"] is True
        reply = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "net_what_if", "arguments": {"change": {"intent": "drain", "ops": [
                {"device": "leaf1", "path": IF.format("Ethernet49"), "value": False}]}}}})
        assert reply["result"]["isError"] is False and '"verification_passed":true' in reply["result"]["content"][0]["text"]
        assert s.net.devices["leaf1"].config["interfaces"]["Ethernet49"]["enabled"] is True
    finally:
        s.stop()
