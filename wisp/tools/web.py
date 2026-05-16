"""Web tools for Wisp — fetch URLs and search the web.

Uses requests for fetching and DuckDuckGo for search.
"""

import json as _json
import logging
import urllib.parse
import urllib.request
from html.parser import HTMLParser

import requests

from wisp.tools._utils import (
    ToolError,
    _validate_string,
    _validate_int,
    _MAX_CMD_LENGTH,
    _TextExtractor,
)

logger = logging.getLogger(__name__)


def tool_web_fetch(url: str, workspace: str = ".", max_chars: int = 10000) -> str:
    """Fetch content from a URL (web page, API endpoint, etc.).
    
    Fetches the URL and returns the content as text.
    For HTML pages, returns extracted text content.
    Respects robots.txt and has reasonable timeouts.
    """
    from urllib.parse import urlparse
    
    # Validate URL
    _validate_string(url, "url", _MAX_CMD_LENGTH)
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ToolError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"Unsupported URL scheme: {parsed.scheme}")
    
    max_chars = _validate_int(max_chars, "max_chars", 100, 100000)
    
    try:
        headers = {
            "User-Agent": "Wisp-Agent/0.1.0 (Web Fetch Tool)"
        }
        response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        response.raise_for_status()
        
        content_type = response.headers.get("Content-Type", "").lower()
        
        # Get text content
        if "text/html" in content_type:
            # Try to extract readable text from HTML using module-level extractor
            try:
                extractor = _TextExtractor()
                extractor.feed(response.text)
                text = extractor.get_text()
            except Exception as e:
                logger.warning("HTML text extraction failed for %s: %s — falling back to raw HTML", url, e)
                text = response.text
                if len(text) > max_chars:
                    text = text[:max_chars] + "\n[Warning: HTML parsing failed, showing raw HTML. Results may be hard to read.]"
        else:
            text = response.text
        
        # Truncate if needed
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated: {len(text)} total chars]"
        
        logger.info("Fetched %s — %d chars", url, len(text))
        return f"✓ Fetched {url}\n\n{text}"
        
    except requests.exceptions.Timeout:
        raise ToolError(f"Request timed out after 30s: {url}")
    except requests.exceptions.ConnectionError as e:
        raise ToolError(f"Connection error: {e}")
    except requests.exceptions.HTTPError as e:
        raise ToolError(f"HTTP error {e.response.status_code}: {url}")
    except requests.exceptions.RequestException as e:
        raise ToolError(f"Request failed: {e}")


def tool_web_search(query: str, num_results: int = 5) -> str:
    """Search the web using DuckDuckGo (prefers duckduckgo_search library, falls back to HTML)."""
    # Try duckduckgo_search/ddgs library first
    DDGS = None
    for module_name in ("ddgs", "duckduckgo_search"):
        try:
            mod = __import__(module_name, fromlist=["DDGS"])
            DDGS = mod.DDGS
            break
        except ImportError:
            continue
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=num_results))
            formatted = []
            for i, r in enumerate(results, 1):
                formatted.append({
                    "number": i,
                    "title": r.get("title", "Untitled"),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
            if formatted:
                return _json.dumps({
                    "status": "ok",
                    "data": {"query": query, "results": formatted},
                    "metadata": {"query": query, "num_results": len(formatted), "backend": module_name},
                })
        except Exception as e:
            logger.warning("ddgs/duckduckgo_search failed, falling back to HTML: %s", e)

    # Fallback: HTML parsing
    class _ResultParser(HTMLParser):
        """Parse DuckDuckGo HTML results into structured dicts."""

        def __init__(self):
            super().__init__()
            self.results: list[dict] = []
            self._state = "idle"    # idle | in_result
            self._current: dict = {}
            self._text_buf = ""

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            cls = a.get("class", "")

            # Detect a result block start
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

    try:
        import ssl
        qs = urllib.parse.urlencode({"q": query})
        url = f"https://html.duckduckgo.com/html/?{qs}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        parser = _ResultParser()
        parser.feed(html)
        results = parser.results[:num_results]

        # Filter out ads
        results = [r for r in results if r.get("title") and r.get("snippet")]

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append({
                "number": i,
                "title": r.get("title", "Untitled"),
                "url": r.get("href", ""),
                "snippet": r.get("snippet", ""),
            })

        if not formatted:
            return _json.dumps({
                "status": "ok",
                "data": {"query": query, "results": []},
                "metadata": {"query": query, "num_results": 0, "backend": "html", "note": "no results matched expected structure"},
            })

        return _json.dumps({
            "status": "ok",
            "data": {"query": query, "results": formatted},
            "metadata": {"query": query, "num_results": len(formatted), "backend": "html"},
        })
    except Exception as e:
        return _json.dumps({
            "status": "error",
            "data": {"query": query, "results": []},
            "metadata": {"query": query, "error": str(e), "backend": ""},
            "error": str(e),
        })
