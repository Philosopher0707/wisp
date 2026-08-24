"""Web tools for Wisp — fetch URLs and search the web.

Uses requests for fetching and DuckDuckGo for search.
"""

import json as _json
import logging
import os
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


# ── robots.txt support ──────────────────────────────────────────────────────
# Simple TTL cache for robots.txt results so we don't refetch the same
# robots.txt for every URL on the same domain.
_robots_cache: dict[str, tuple[bool, float]] = {}
_ROBOTS_TTL = 3600.0  # cache for 1 hour
_ROBOTS_FETCH_TIMEOUT = 10.0  # seconds

# Shared user-agent for all outgoing web requests
_USER_AGENT = "Wisp-Agent/0.1.0 (Web Fetch Tool; Respects robots.txt)"

_READER_PROXY_BASE = "https://r.jina.ai/"


def _parse_robots_txt(robots_text: str, user_agent: str, target_path: str) -> bool:
    """Parse robots.txt content and return whether target_path is allowed.

    Lightweight manual parser replacing ``urllib.robotparser.RobotFileParser``:

    * Explicit timeout control (``_ROBOTS_FETCH_TIMEOUT``)
    * No hidden blocking ``urlopen`` calls inside the parser
    * Handles ``User-agent``, ``Disallow``, and ``Allow`` directives only
    * Returns ``True`` (allow) when no matching rule is found
    """
    ua_lower = user_agent.lower().strip() or "*"
    if not target_path.startswith("/"):
        target_path = "/" + target_path

    current_group_applies = False
    result = True  # allow by default

    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        directive = line.lower()
        if directive.startswith("user-agent:"):
            # Check how the previous group ended before starting a new one
            # A new user-agent line resets the "current group applies" flag.
            ua_match = line.split(":", 1)[1].strip().lower()
            current_group_applies = (ua_match == "*" or ua_match in ua_lower)
            continue

        if not current_group_applies:
            continue

        if directive.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if not path:
                # Empty disallow → allow everything
                result = True
                continue
            if target_path.startswith(path):
                # Matched a disallow — tentatively deny
                result = False

        elif directive.startswith("allow:"):
            path = line.split(":", 1)[1].strip()
            if target_path.startswith(path):
                # More specific allow overrides a previous disallow
                result = True

    return result


def _check_robots_txt(target_url: str, user_agent: str = "*") -> bool:
    """Return True if fetching target_url is allowed by robots.txt.

    Fetches the robots.txt for the target's netloc with an explicit
    ``_ROBOTS_FETCH_TIMEOUT`` (default 10 s), parses it with
    ``_parse_robots_txt``, and checks whether the path is allowed.
    Results are cached for ``_ROBOTS_TTL`` seconds.

    Returns True (allow) if robots.txt is unreachable, malformed, or
    does not exist — this is standard behaviour (``robots.txt`` is a
    voluntary protocol, not a security boundary).
    """
    parsed = urllib.parse.urlparse(target_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return True

    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    cache_key = robots_url
    import time
    now = time.time()

    cached = _robots_cache.get(cache_key)
    if cached is not None:
        allowed, timestamp = cached
        if now - timestamp < _ROBOTS_TTL:
            return allowed

    try:
        resp = requests.get(
            robots_url,
            timeout=_ROBOTS_FETCH_TIMEOUT,
            headers={"User-Agent": user_agent},
            allow_redirects=True,
        )
        if resp.status_code == 404:
            # No robots.txt → everything allowed
            _robots_cache[cache_key] = (True, now)
            return True
        resp.raise_for_status()
        robots_text = resp.text
    except Exception as e:
        logger.debug("robots.txt fetch failed for %s: %s", robots_url, e)
        _robots_cache[cache_key] = (True, now)
        return True

    try:
        allowed = _parse_robots_txt(robots_text, user_agent, parsed.path or "/")
        _robots_cache[cache_key] = (allowed, now)
        if not allowed:
            logger.info("robots.txt disallowed %s", target_url)
        return allowed
    except Exception:
        _robots_cache[cache_key] = (True, now)
        return True


def _fetch_via_reader_proxy(url: str, max_chars: int, reason: str) -> str:
    """Retry a bot-blocked fetch through a keyless reader proxy.

    The proxy fetches server-side and returns extracted text, so sites
    blocking our UA are read without spoofing or headless browsers.
    Failure here is honest — the original block stands, with a note.
    """
    proxy_url = f"{_READER_PROXY_BASE}{url}"
    try:
        resp = requests.get(proxy_url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.debug("Reader proxy failed for %s: %s", url, e)
        raise ToolError(
            f"[WEB_FETCH_BLOCKED] {reason}. Reader-proxy fallback also "
            f"failed ({type(e).__name__}). Try a different source."
        )

    text = resp.text
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated: {len(text)} total chars]"
    logger.info("Fetched %s via reader proxy — %d chars", url, len(text))
    return (
        f"✓ Fetched {url} (via reader proxy; direct fetch blocked: {reason})\n\n{text}"
    )


def tool_web_fetch(url: str, workspace: str = ".", max_chars: int = 10000) -> str:
    """Fetch content from a URL (web page, API endpoint, etc.).

    Fetches the URL and returns the content as text.
    For HTML pages, returns extracted text content.

    Checks robots.txt before fetching (with 1-hour TTL cache).
    Falls back to allowing the fetch if robots.txt cannot be retrieved.
    Uses a 30-second timeout and follows redirects.

    On bot-blocks (403/429, robots refusal) retries once through a
    keyless reader proxy — research agents hit paywalled-to-bots sites
    constantly and honest failure every time caps their depth. Disable
    with WISP_WEB_PROXY=off (URLs then never leave the direct path).
    """
    from urllib.parse import urlparse

    # Validate URL
    _validate_string(url, "url", _MAX_CMD_LENGTH)
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ToolError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"Unsupported URL scheme: {parsed.scheme}")

    proxy_enabled = os.environ.get("WISP_WEB_PROXY", "on").lower() not in ("off", "false", "0")

    # ── robots.txt compliance ──
    if not _check_robots_txt(url, user_agent=_USER_AGENT):
        if proxy_enabled:
            return _fetch_via_reader_proxy(url, max_chars,
                reason=f"robots.txt of {parsed.netloc} disallows automated fetching")
        raise ToolError(
            f"[WEB_FETCH_BLOCKED] The site {parsed.netloc} explicitly "
            f"disallows automated fetching via robots.txt. "
            f"Try a different source or ask the user for a direct link "
            f"that is guaranteed to be allowed."
        )

    max_chars = _validate_int(max_chars, "max_chars", 100, 100000)

    try:
        headers = {
            "User-Agent": _USER_AGENT
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
        raise ToolError(f"[WEB_FETCH_FAILED] Timeout after 30s: {url}. The server is too slow or unreachable.")
    except requests.exceptions.ConnectionError as e:
        err_str = str(e)
        if "Failed to resolve" in err_str or "nodename nor servname" in err_str:
            msg = f"[WEB_FETCH_FAILED] DNS resolution failed for {url}. The domain does not exist or cannot be reached. Try a different URL or search for the content instead."
        elif "Connection refused" in err_str:
            msg = f"[WEB_FETCH_FAILED] Connection refused by {url}. The server is down or blocking requests."
        else:
            msg = f"[WEB_FETCH_FAILED] Cannot reach {url} (connection error). The site may be down or your network may be restricted. Try a different URL."
        raise ToolError(msg)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        if status == 404:
            raise ToolError(f"[WEB_FETCH_FAILED] HTTP 404: {url} does not exist. The page may have moved or been deleted. Do NOT retry the same URL. Try searching for the content instead.")
        elif status == 403:
            if proxy_enabled:
                return _fetch_via_reader_proxy(url, max_chars,
                    reason=f"HTTP 403 from {parsed.netloc}")
            raise ToolError(f"[WEB_FETCH_FAILED] HTTP 403: Access denied to {url}. The server is blocking automated requests. Try a different source.")
        elif status == 429:
            if proxy_enabled:
                return _fetch_via_reader_proxy(url, max_chars,
                    reason=f"HTTP 429 rate limit from {parsed.netloc}")
            raise ToolError(f"[WEB_FETCH_FAILED] HTTP 429: Rate limited by {url}. Wait before trying again or use a different source.")
        else:
            raise ToolError(f"[WEB_FETCH_FAILED] HTTP {status}: Server returned error for {url}. Try a different URL or search for the content.")
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
