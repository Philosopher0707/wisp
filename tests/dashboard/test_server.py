from __future__ import annotations

import http.client
import json
import re
import threading

import pytest

from tests.dashboard.conftest import SECRET_KEY, SECRET_PROMPT, make_workspace
from tests.dashboard.test_collect import TestBench
from wisp.dashboard import server as V


@pytest.fixture
def running(home, tmp_path):
    ws = make_workspace(home, "p")
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir()
    TestBench().seed(bench_dir)
    srv = V.make_server(0, bench_dir, [ws], root=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def req(srv, method="GET", path="/", host=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host if host is not None else f"127.0.0.1:{srv.server_port}")
    c.endheaders(body)
    r = c.getresponse()
    data = r.read()
    headers = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, headers, data


class TestSecurity:
    def test_it_listens_on_loopback_only(self, running):
        assert running.server_address[0] == "127.0.0.1"

    @pytest.mark.parametrize("host", ["evil.example", "127.0.0.1", "127.0.0.1:1", "localhost", "attacker.com:80", ""])
    def test_a_foreign_host_header_is_refused_dns_rebinding(self, running, host):
        status, _, body = req(running, host=host)
        assert status == 403 and b"forbidden" in body
        assert req(running, path="/api/overview", host=host)[0] == 403

    def test_localhost_is_accepted(self, running):
        assert req(running, host=f"localhost:{running.server_port}")[0] == 200

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def test_nothing_but_get_and_head_is_served(self, running, method):
        status, headers, _ = req(running, method, "/api/overview", body=b"")
        assert status == 405 and headers.get("allow") == "GET, HEAD"

    def test_security_headers_and_a_nonce_only_csp(self, running):
        status, h, body = req(running)
        assert status == 200 and h["cache-control"] == "no-store" and h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
        nonce = re.search(r"script-src 'nonce-([\w-]+)'", h["content-security-policy"]).group(1)
        assert "default-src 'none'" in h["content-security-policy"] and "connect-src 'self'" in h["content-security-policy"] and "unsafe-inline" not in h["content-security-policy"]
        assert body.decode().count(f'nonce="{nonce}"') == 2 and "__NONCE__" not in body.decode()
        assert req(running)[1]["content-security-policy"] != h["content-security-policy"]  # a fresh nonce per response

    def test_the_page_has_no_inline_handlers_and_no_innerhtml(self, running):
        page = req(running)[2].decode()
        assert not re.search(r"\son[a-z]+\s*=", page) and "innerHTML" not in page and "eval(" not in page and "http://" not in page.replace("http://www.w3.org/2000/svg", "")

    @pytest.mark.parametrize("path", ["/api/", "/api/../etc/passwd", "/api/nope", "/static/x", "/api/overview/../../x", "//etc/passwd", "/favicon.ico", "/api/overview%00"])
    def test_unknown_paths_are_404_and_never_touch_the_filesystem(self, running, path):
        status, h, body = req(running, path=path)
        assert status == 404 and json.loads(body) == {"error": "not found"}

    def test_head_has_no_body(self, running):
        status, h, body = req(running, "HEAD", "/api/overview")
        assert status == 200 and body == b""


class TestApi:
    @pytest.mark.parametrize("name", V.SECTIONS)
    def test_every_section_is_json(self, running, name):
        status, h, body = req(running, path=f"/api/{name}")
        assert status == 200 and h["content-type"].startswith("application/json")
        assert isinstance(json.loads(body), dict)

    def test_the_overview_leads_with_the_missing_or_present_number(self, running):
        o = json.loads(req(running, path="/api/overview")[2])
        assert o["headline"] and o["headline"][0]["summary"]["pass_rate"] == 0.75
        assert all({"level", "text", "where"} <= set(a) for a in o["alerts"])

    def test_no_response_ever_contains_prompt_text_or_a_key(self, running):
        everything = b"".join(req(running, path=f"/api/{n}")[2] for n in V.SECTIONS) + req(running)[2]
        assert SECRET_PROMPT.encode() not in everything and SECRET_KEY.encode() not in everything

    def test_a_broken_section_does_not_blank_the_page(self, home, tmp_path, monkeypatch):
        ws = make_workspace(home, "p")
        monkeypatch.setattr("wisp.dashboard.collect.usage_data", lambda w: (_ for _ in ()).throw(RuntimeError("boom")))
        srv = V.make_server(0, tmp_path / "b", [ws], root=tmp_path)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            assert json.loads(req(srv, path="/api/usage")[2])["error"].startswith("RuntimeError")
            o = json.loads(req(srv, path="/api/overview")[2])
            assert "alerts" in o and req(srv, path="/api/bench")[0] == 200
        finally:
            srv.shutdown()
            srv.server_close()

    def test_port_in_use_is_reported_not_raised(self, running, capsys):
        assert V.serve(port=running.server_port) == 1
        assert "cannot listen" in capsys.readouterr().out


class TestAlerts:
    def sections(self, **over):
        base = {"bench": {"groups": [], "comparisons": [], "runs": []}, "usage": {"graph_runs": {}, "turn_completion": {}, "background_runs": {}},
                "harness": {"dirty": False}, "learning": {"facts": 5, "backups": [], "remember": {"calls": 0, "ok": 0, "error": 0}, "session_summaries": 3}}
        base.update(over)
        return base

    def test_no_measurement_is_the_loudest_alert(self):
        a = V.alerts(self.sections())
        assert a[0]["level"] == "critical" and "no measured accuracy" in a[0]["text"]

    def test_evidence_based_alerts(self):
        s = self.sections(learning={"facts": 0, "backups": [{"facts": 72}], "remember": {"calls": 82, "ok": 30, "error": 52}, "session_summaries": 100},
                          usage={"graph_runs": {"running": 1}, "turn_completion": {"ratio": 0.7}, "background_runs": {"failed": 11, "succeeded": 25}}, harness={"dirty": True})
        text = " | ".join(a["text"] for a in V.alerts(s))
        for needle in ("backup holds 72 facts", "`remember` failed 52 of 82", "100-row cap", "1 graph run", "70%", "11 of 36 background", "uncommitted"):
            assert needle in text, needle

    @pytest.mark.parametrize("free,level", [(0.1, "critical"), (3.0, "warn")])
    def test_a_nearly_full_disk_is_called_out(self, free, level):
        a = V.alerts(self.sections(harness={"dirty": False, "disk": {"free_gb": free, "used_pct": 99.9}}))
        hit = [x for x in a if "disk" in x["text"].lower()]
        assert hit and hit[0]["level"] == level and hit[0]["where"] == "harness"

    def test_alerts_are_ordered_by_severity(self):
        s = self.sections(learning={"facts": 5, "backups": [], "remember": {"calls": 0, "ok": 0, "error": 0}, "session_summaries": 100}, harness={"dirty": True, "disk": {"free_gb": 0.2, "used_pct": 99.9}})
        levels = [a["level"] for a in V.alerts(s)]
        assert levels == sorted(levels, key={"critical": 0, "warn": 1, "info": 2}.get) and levels[0] == "critical"

    def test_plenty_of_disk_is_silent(self):
        assert not [x for x in V.alerts(self.sections(harness={"dirty": False, "disk": {"free_gb": 80.0, "used_pct": 40}})) if "disk" in x["text"].lower()]

    def test_a_biased_sample_and_overclaims_are_called_out(self):
        summary = {"scored": 12, "infra_rate": 0.5, "ci_low": 0.4, "ci_high": 0.8, "overclaims": 2}
        s = self.sections(bench={"groups": [{"group": "m | default", "summary": summary}], "comparisons": [], "runs": []})
        text = " | ".join(a["text"] for a in V.alerts(s))
        assert "50% of attempts hit infrastructure" in text and "reported success on work that was not solved" in text
