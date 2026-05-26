"""Tests for web_search tool (HTML fallback parser).

Exercises the DuckDuckGo HTML result extraction against a realistic HTML
fixture so the parser can be verified without making live HTTP calls.
"""

import json

import pytest

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


class TestWebSearchLive:
    """Optional live test — skipped if no network or DDG blocks request."""

    @pytest.mark.skipif(
        pytest.importorskip("urllib.request") is None,
        reason="urllib not available",
    )
    def test_live_web_search_returns_results(self):
        from wisp.tools import tool_web_search

        raw = tool_web_search("python dataclasses tutorial", num_results=3)
        result = json.loads(raw)
        if result["status"] == "error" and ("urlopen error" in str(result.get("error", "")) or "nodename" in str(result.get("error", ""))):
            pytest.skip("Network unavailable in this environment")
        assert result["status"] == "ok"
        assert result["metadata"]["backend"] in ("html", "ddgs", "duckduckgo_search")
        if result["metadata"].get("num_results", 0) == 0:
            pytest.skip("No search results (DDG may be rate-limiting)")
        results = result["data"]["results"]
        assert len(results) >= 1
        for r in results:
            assert r.get("title")
            assert r.get("url")
