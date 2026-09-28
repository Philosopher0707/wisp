"""wisp_net foundations: gNMI paths, syslog, the telemetry bus, the metric store, the knowledge base."""

from __future__ import annotations

import pytest

from wisp_net.paths import PathError, format_path, matches, parse_path
from wisp_net.state.knowledge import KnowledgeBase
from wisp_net.state.tsdb import DAY, MetricStore
from wisp_net.telemetry import syslog
from wisp_net.telemetry.bus import Consumer, TelemetryBus


class TestPaths:
    def test_key_values_may_contain_slashes(self):
        path = parse_path("/network-instances/network-instance[name=default]/afts/ipv4-unicast"
                          "/ipv4-entry[prefix=10.1.0.0/24]/state/next-hops")
        assert path[4].key("prefix") == "10.1.0.0/24"
        assert path[-1].name == "next-hops"
        assert format_path(path).endswith("ipv4-entry[prefix=10.1.0.0/24]/state/next-hops")

    def test_multiple_keys_are_order_independent(self):
        a = parse_path("/p/protocol[identifier=BGP][name=BGP]")
        b = parse_path("/p/protocol[name=BGP][identifier=BGP]")
        assert a == b

    def test_wildcards_and_subtree_matching(self):
        leaf = parse_path("/interfaces/interface[name=Ethernet49]/state/counters/in-octets")
        assert matches(parse_path("/interfaces/interface[name=*]/state"), leaf)
        assert matches(parse_path("/interfaces/*"), leaf)
        assert not matches(parse_path("/interfaces/interface[name=Ethernet50]"), leaf)
        assert not matches(parse_path("/interfaces/interface[name=Ethernet49]/config"), leaf)
        assert not matches(parse_path("/interfaces/interface[mtu=*]"), leaf), "a missing key never matches"

    @pytest.mark.parametrize("bad", ["interfaces/interface", "/a//b", "/a[b]", "/a[=x]", "/a[b=c"])
    def test_malformed_paths_are_rejected(self, bad):
        with pytest.raises(PathError):
            parse_path(bad)


class TestSyslog:
    def test_full_rfc5424_message(self):
        line = ('<188>1 2026-01-05T12:00:03.250Z leaf1 bgpd 7 BGP_ADJCHANGE '
                '[bgp@32473 peer="10.255.0.1" note="a \\"quoted\\" \\] value"][meta@32473 seq="4"] '
                '﻿BGP neighbor 10.255.0.1 (AS 65100) changed state from Established to Idle')
        m = syslog.parse(line)
        assert (m.facility, m.severity, m.severity_name) == (23, 4, "warning")
        assert (m.hostname, m.app_name, m.procid, m.msgid) == ("leaf1", "bgpd", "7", "BGP_ADJCHANGE")
        assert m.structured_data["bgp@32473"] == {"peer": "10.255.0.1", "note": 'a "quoted" ] value'}
        assert m.structured_data["meta@32473"] == {"seq": "4"}
        assert m.msg.startswith("BGP neighbor"), "the UTF-8 BOM is not part of the message"
        assert m.timestamp == pytest.approx(1767614403.25)

    def test_nil_fields_and_no_message(self):
        m = syslog.parse("<13>1 - - - - - -")
        assert (m.timestamp, m.hostname, m.app_name, m.msg) == (None, None, None, "")
        assert m.structured_data == {}

    @pytest.mark.parametrize("bad", ["hello", "<999>1 - - - - - -", "<13>1 - - - - - [x a=b]",
                                     '<13>1 - - - - - [x a="b"', "<13>1 notatime h a p m -"])
    def test_malformed_messages_raise(self, bad):
        with pytest.raises(syslog.SyslogParseError):
            syslog.parse(bad)

    @pytest.mark.parametrize("msgid,msg,kind,subject", [
        ("LINK_DOWN", "Interface Ethernet49 changed state to down", "interface_down", "Ethernet49"),
        ("LINK_UP", "Interface Ethernet49 changed state to up", "interface_up", "Ethernet49"),
        ("BGP_ADJCHANGE", "BGP neighbor 10.255.0.1 (AS 65100) changed state from Established to Idle",
         "bgp_session_change", "10.255.0.1"),
        ("FCS_ERRORS", "Interface Ethernet50: 1532 FCS errors in last 60s", "fcs_errors", "Ethernet50"),
        ("RX_POWER_LOW", "Rx power low alarm on Ethernet50: -12.40 dBm", "rx_power_low", "Ethernet50"),
        ("SOMETHING", "fan tray 2 speed high", "unclassified", "app"),
    ])
    def test_normalization(self, msgid, msg, kind, subject):
        event = syslog.normalize(syslog.parse(f"<187>1 2026-01-05T12:00:00Z leaf1 app - {msgid} - {msg}"))
        assert (event.device, event.kind, event.subject) == ("leaf1", kind, subject)
        if kind == "bgp_session_change":
            assert event.attrs["established"] is False and event.attrs["peer_as"] == "65100"

    def test_dedup_suppresses_repeats_but_not_state_changes(self):
        dedup = syslog.Deduplicator(window_s=30)

        def ev(ts, new):
            return syslog.NetEvent(ts, "leaf1", "bgp_session_change", "warning", "10.255.0.1", {"new": new})

        assert dedup.admit(ev(0, "Idle")) is not None
        assert dedup.admit(ev(5, "Idle")) is None
        assert dedup.admit(ev(6, "Established")) is not None, "a different state is news"
        assert dedup.admit(ev(40, "Idle")) is not None, "outside the window it is news again"
        assert dedup.suppressed == 1


class TestBus:
    def test_offsets_and_consumer_progress(self):
        bus = TelemetryBus()
        for i in range(5):
            assert bus.publish("net.telemetry.alerts.v1", "k", i, float(i)) == i
        c = Consumer(bus, "net.telemetry.alerts.v1")
        assert [r.value for r in c.poll(3)] == [0, 1, 2]
        assert [r.value for r in c.poll()] == [3, 4]
        assert c.poll() == ()

    def test_retention_reports_lost_records(self):
        bus = TelemetryBus(max_records=3)
        c = Consumer(bus, "net.telemetry.metrics.v1")
        for i in range(10):
            bus.publish("net.telemetry.metrics.v1", "k", i, float(i))
        assert [r.value for r in c.poll()] == [7, 8, 9]
        assert c.lost == 7

    def test_age_retention(self):
        bus = TelemetryBus(max_age_s=10)
        bus.publish("net.telemetry.flows.v1", "k", "old", 0.0)
        bus.publish("net.telemetry.flows.v1", "k", "new", 20.0)
        assert [r.value for r in bus.poll("net.telemetry.flows.v1", 0).records] == ["new"]

    def test_unknown_topic(self):
        with pytest.raises(KeyError):
            TelemetryBus().publish("nope", "k", 1, 0.0)


class TestMetricStore:
    def test_downsampling_is_exact(self):
        db = MetricStore()
        db.write_many([("leaf1|if.util|E1", float(t), float(t % 60)) for t in range(0, 180)])
        m1 = db.query("leaf1|if.util|E1", 0, 180, step=60)["leaf1|if.util|E1"]
        assert [(p.ts, p.value, p.min, p.max) for p in m1] == [(0, 29.5, 0, 59), (60, 29.5, 0, 59),
                                                               (120, 29.5, 0, 59)]
        raw = db.query("leaf1|*", 100, 110)["leaf1|if.util|E1"]
        assert [p.value for p in raw] == [float(t % 60) for t in range(100, 111)]

    def test_tier_follows_age_and_step(self):
        db = MetricStore()
        db.write("s|m|x", 100 * DAY, 1.0)
        assert db.tier_for(100 * DAY - 3600, 0) == "raw"
        assert db.tier_for(100 * DAY - 3600, 60) == "m1"
        assert db.tier_for(100 * DAY - 30 * DAY, 0) == "m1", "older than raw retention"
        assert db.tier_for(100 * DAY - 200 * DAY, 0) == "h1"

    def test_retention_prunes_each_tier(self):
        db = MetricStore()
        db.write("s|m|x", 0.0, 1.0)
        db.write("s|m|x", 400 * DAY, 2.0)
        removed = db.prune()
        assert removed == {"raw": 1, "m1": 1, "h1": 1}
        assert db.latest("s|*")["s|m|x"].value == 2.0

    def test_series_glob(self):
        db = MetricStore()
        db.write_many([("leaf1|if.util|E1", 1, 1), ("leaf2|if.util|E1", 1, 1), ("leaf1|sys.cpu_pct|leaf1", 1, 1)])
        assert db.series("leaf1|*") == ["leaf1|if.util|E1", "leaf1|sys.cpu_pct|leaf1"]
        assert db.series("*|if.util|*") == ["leaf1|if.util|E1", "leaf2|if.util|E1"]


class TestKnowledge:
    def test_bundled_runbooks_rank_the_matching_section_first(self):
        kb = KnowledgeBase.bundled()
        assert {c.doc for c in kb.chunks} >= {"bgp-session-down", "fcs-crc-errors", "optics-degradation",
                                              "link-congestion", "change-management"}
        top = kb.search("interface receiving FCS errors which side is at fault", k=1)[0]
        assert top.chunk.doc == "fcs-crc-errors"
        top = kb.search("BGP neighbor stuck in ACTIVE state", k=1)[0]
        assert top.chunk.doc == "bgp-session-down"

    def test_no_terms_no_hits(self):
        assert KnowledgeBase.bundled().search("the of and") == []
