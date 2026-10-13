"""Tests for web_search tool (HTML fallback parser).

Exercises the DuckDuckGo HTML result extraction against a realistic HTML
fixture so the parser can be verified without making live HTTP calls.
"""

import json
import socket

import pytest


def _network_available(host: str = 'duckduckgo.com', port: int = 443, timeout: float = 2.0) -> bool:
    """Quick TCP probe -- True if we can reach the search backend."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

# The _ResultParser is module-local inside tool_web_search; import it by
# executing the fallback path via a private helper.

# We cannot import _ResultParser directly because it is defined inside a
# function.  Instead we exercise tool_web_search with a mock HTTP context.
# For the fixture-based test we re-create the same class locally.

_DDG_HTML_FIXTURE = """<!DOCTYPE html>
<html>
<head><title>DDG Results</title></head>
<body>
<div class="results">
<div class="result results_links results_links_deep web-result">
  <div class="links_main result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffoo">
         Python Data Classes
      </a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=...">
      A comprehensive guide to Python data classes.
    </a>
  </div>
</div>
<div class="result results_links">
  <div class="links_main result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com">
         Real Python Tutorial
      </a>
    </h2>
    <a class="result__snippet">
      Learn Python with hands-on tutorials.
    </a>
  </div>
</div>
</div>
</body>
</html>"""


def _parse_ddg_html(html: str):
    """Reproduce the _ResultParser logic for testing purposes."""
    import urllib.parse
    from html.parser import HTMLParser

    class _ResultParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.results: list[dict] = []
            self._state = "idle"
            self._current: dict = {}
            self._text_buf = ""

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            cls = a.get("class", "")
            if tag == "div" and cls.startswith("result "):
                self._state = "in_result"
                self._current = {"href": "", "title": "", "snippet": ""}
                return
            if self._state != "in_result":
                return
            if tag == "a":
                if "result__a" in cls:
                    raw_href = a.get("href", "")
                    self._current["href"] = raw_href
                    self._text_buf = ""
                elif "result__snippet" in cls or "result__a" not in cls:
                    self._text_buf = ""

        def handle_data(self, data):
            if self._state == "in_result":
                self._text_buf += data

        def handle_endtag(self, tag):
            if self._state != "in_result":
                return
            if tag == "div":
                self._state = "idle"
                if self._current.get("title"):
                    self.results.append(self._current.copy())
                self._text_buf = ""
            elif tag == "a":
                text = self._text_buf.strip()
                href = self._current.get("href", "")
                if text:
                    if "duckduckgo.com/l/" in href and not self._current["title"]:
                        self._current["title"] = text
                        try:
                            qs = urllib.parse.urlparse(href).query
                            qd = urllib.parse.parse_qs(qs)
                            real = qd.get("uddg", [])[0] if "uddg" in qd else ""
                            if real:
                                self._current["href"] = urllib.parse.unquote(real)
                        except Exception:
                            pass
                    elif not self._current["snippet"]:
                        self._current["snippet"] = text
                self._text_buf = ""

    parser = _ResultParser()
    parser.feed(html)
    results = parser.results
    # Same filtering as production code
    return [r for r in results if r.get("title") and r.get("snippet")]


class TestWebSearchHTMLParser:
    def test_parses_two_results_from_fixture(self):
        results = _parse_ddg_html(_DDG_HTML_FIXTURE)
        assert len(results) == 2
        assert results[0]["title"] == "Python Data Classes"
        assert results[0]["href"] == "https://example.com/foo"
        assert results[0]["snippet"] == "A comprehensive guide to Python data classes."
        assert results[1]["title"] == "Real Python Tutorial"
        assert results[1]["href"] == "https://realpython.com"
        assert "Python" in results[1]["snippet"]

    def test_parses_empty_results(self):
        results = _parse_ddg_html("<html><body>No results here</body></html>")
        assert results == []

    def test_parses_skips_ads_without_snippets(self):
        html = '''<html>
<body>
<div class="result result--ad">
  <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fspam.com">Buy stuff</a></h2>
</div>
<div class="result results_links">
  <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgood.com">Good Title</a></h2>
  <a class="result__snippet">Good snippet text.</a>
</div>
</body></html>'''
        results = _parse_ddg_html(html)
        assert len(results) == 1
        assert results[0]["title"] == "Good Title"
        assert "Good snippet" in results[0]["snippet"]


# Errors that mean the search backend could not be reached at all (DNS,
# refused, timeout). Matched narrowly so other failures are reported.
_NETWORK_DOWN_MARKERS = (
    "Failed to resolve",
    "nodename nor servname",
    "Name or service not known",
    "Temporary failure in name resolution",
    "Connection refused",
    "Network is unreachable",
    "timed out",
)


class TestWebSearchLive:
    """Optional live test — skipped if no network or DDG blocks request."""

    @pytest.mark.live
    def test_live_web_search_returns_results(self):
        if not _network_available():
            pytest.skip("Network unavailable in this environment")
        from wisp.tools import tool_web_search

        raw = tool_web_search("python dataclasses tutorial", num_results=3)
        result = json.loads(raw)
        if result["status"] == "error":
            error = str(result.get("error", ""))
            # A TLS verification failure is a defect in the tool or the
            # machine's trust store, never "no network": it used to be
            # skipped here as "Network unavailable" because the old skip
            # matched any "urlopen error".
            if "CERTIFICATE_VERIFY_FAILED" in error or "SSLError" in error:
                pytest.fail(f"web_search failed TLS verification: {error}")
            if any(marker in error for marker in _NETWORK_DOWN_MARKERS):
                pytest.skip(f"Network unavailable in this environment: {error}")
            if "[WEB_SEARCH_BLOCKED]" in error:
                pytest.skip(f"DuckDuckGo refused this client: {error}")
        assert result["status"] == "ok", result.get("error")
        assert result["metadata"]["backend"] in ("html", "ddgs", "duckduckgo_search")
        if result["metadata"].get("num_results", 0) == 0:
            pytest.skip("No search results (DDG may be rate-limiting)")
        results = result["data"]["results"]
        assert len(results) >= 1
        for r in results:
            assert r.get("title")
            assert r.get("url")


# ── TLS trust store (hermetic) ──────────────────────────────────────────────
#
# On a python.org macOS build where "Install Certificates.command" never ran,
# the interpreter's default CA file does not exist, so urllib's default
# context trusted nothing and web_search failed TLS for every host while
# web_fetch (requests + certifi) worked. These tests serve the search page
# from a local HTTPS server whose certificate is signed by a throwaway CA, and
# hand that CA to the tool only through requests' bundle variable. The
# interpreter's default store is pointed at nothing, as on that machine.


def _make_ca(name: str):
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _make_leaf(ca_key, ca_cert):
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _pem(cert) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture
def local_ddg(tmp_path, monkeypatch):
    """A local HTTPS server answering like DuckDuckGo's HTML endpoint.

    Yields the paths of two CA bundles, ``trusted`` (it signed the server's
    certificate) and ``untrusted``, and ``reply``, the status and body the
    server answers with (a page of two results unless a test changes it).
    """
    import ssl
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from cryptography.hazmat.primitives import serialization

    ca_key, ca_cert = _make_ca("wisp-test-ca")
    leaf_key, leaf_cert = _make_leaf(ca_key, ca_cert)
    _, other_ca = _make_ca("wisp-unrelated-ca")

    trusted = tmp_path / "trusted-ca.pem"
    trusted.write_bytes(_pem(ca_cert))
    untrusted = tmp_path / "untrusted-ca.pem"
    untrusted.write_bytes(_pem(other_ca))
    cert_file = tmp_path / "leaf.pem"
    cert_file.write_bytes(_pem(leaf_cert))
    key_file = tmp_path / "leaf.key"
    key_file.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    reply = {"status": 200, "body": _DDG_HTML_FIXTURE}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = reply["body"].encode("utf-8")
            self.send_response(reply["status"])
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_file), str(key_file))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_address[1]
    monkeypatch.setattr("wisp.tools.web._DDG_HTML_URL", f"https://127.0.0.1:{port}/html/")
    # Force the HTML path: no ddgs / duckduckgo_search library.
    monkeypatch.setitem(sys.modules, "ddgs", None)
    monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
    # The interpreter's default store points at nothing, as on the owner's Mac.
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-cert.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing-certs"))
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    try:
        yield {"trusted": str(trusted), "untrusted": str(untrusted), "reply": reply}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestWebSearchTLSTrust:
    def test_verifies_with_the_requests_bundle_not_the_interpreter_default(self, local_ddg, monkeypatch):
        """The owner's Mac: no default CA file, yet web_fetch's trust store works.

        web_search must use the same trust store as web_fetch (requests'
        bundle, certifi unless REQUESTS_CA_BUNDLE overrides it), not the
        interpreter's OpenSSL default, which may not exist.
        """
        from wisp.tools import tool_web_search

        monkeypatch.setenv("REQUESTS_CA_BUNDLE", local_ddg["trusted"])
        result = json.loads(tool_web_search("python dataclasses", num_results=3))

        assert result["status"] == "ok", result.get("error")
        assert result["metadata"]["backend"] == "html"
        titles = [r["title"] for r in result["data"]["results"]]
        assert titles == ["Python Data Classes", "Real Python Tutorial"]

    def test_an_unknown_issuer_is_still_refused(self, local_ddg, monkeypatch):
        """Verification stays on: a certificate from an untrusted CA is an error."""
        from wisp.tools import tool_web_search

        monkeypatch.setenv("REQUESTS_CA_BUNDLE", local_ddg["untrusted"])
        result = json.loads(tool_web_search("python dataclasses", num_results=3))

        assert result["status"] == "error"
        assert result["data"]["results"] == []
        assert "CERTIFICATE_VERIFY_FAILED" in result["error"]

    def test_the_request_keeps_its_timeout_and_verification(self, local_ddg, monkeypatch):
        import requests

        from wisp.tools import tool_web_search

        monkeypatch.setenv("REQUESTS_CA_BUNDLE", local_ddg["trusted"])
        calls = []
        real_get = requests.get

        def _spy(*args, **kwargs):
            calls.append(kwargs)
            return real_get(*args, **kwargs)

        monkeypatch.setattr("wisp.tools.web.requests.get", _spy)
        result = json.loads(tool_web_search("python dataclasses", num_results=3))

        assert result["status"] == "ok", result.get("error")
        assert len(calls) == 1
        assert calls[0].get("timeout") == 10
        assert calls[0].get("verify", True) is True


class TestWebSearchBotChallenge:
    """DuckDuckGo answers a suspected bot with HTTP 202 and a challenge page.

    Seen on the owner's machine on 2026-10-13 once TLS worked: GET, POST, the
    lite endpoint and two user agents all got 202 and no results. The tool
    reported that as ``status: ok`` with zero results, so the agent was told
    nothing exists when it had been refused.
    """

    _CHALLENGE = "<html><body><div class='anomaly-modal'>challenge</div></body></html>"

    def test_a_202_challenge_is_an_error_not_zero_results(self, local_ddg, monkeypatch):
        from wisp.tools import tool_web_search

        monkeypatch.setenv("REQUESTS_CA_BUNDLE", local_ddg["trusted"])
        local_ddg["reply"].update(status=202, body=self._CHALLENGE)
        result = json.loads(tool_web_search("python dataclasses", num_results=3))

        assert result["status"] == "error"
        assert result["data"]["results"] == []
        assert "[WEB_SEARCH_BLOCKED]" in result["error"]
        assert "202" in result["error"]

    def test_a_200_page_without_results_is_still_zero_results(self, local_ddg, monkeypatch):
        from wisp.tools import tool_web_search

        monkeypatch.setenv("REQUESTS_CA_BUNDLE", local_ddg["trusted"])
        local_ddg["reply"].update(status=200, body="<html><body>No results.</body></html>")
        result = json.loads(tool_web_search("an unlikely query", num_results=3))

        assert result["status"] == "ok"
        assert result["metadata"]["num_results"] == 0
