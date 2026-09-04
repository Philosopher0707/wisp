"""Hardened HTTP transport — resilient timeouts, keep-alive, and retry.

Replaces default/rigid socket timeouts that collapse under large
multi-kilobyte prompt + tool payloads (30+ tool calls in a single turn).

Design:
  - Explicit granular timeouts: connect 15s, write 60s, read 120s, pool 30s
  - TCP keep-alive + pooled connections to avoid silent drops over
    multi-minute turns
  - Transient error classification: TimeoutError, ConnectionResetError,
    httpcore.WriteTimeout, httpcore.ReadTimeout, h11 RemoteProtocolError,
    plus requests/httpx equivalents, with exponential backoff + jitter
    (3 attempts)
  - Dual backend: httpx (preferred, full timeout granularity) with
    requests fallback (connect/read only) — both hardened

Usage:
  from wisp.core.transport import (
      HARDENED_TIMEOUT,
      get_hardened_session,          # requests.Session
      get_hardened_httpx_client,     # httpx.Client (if httpx installed)
      is_transient_error,
      with_retry,                     # decorator / wrapper
      hardened_post,                  # requests-like POST with retry
  )

  # Provider example (OpenAI):
  session = get_hardened_session()
  resp = hardened_post(session, "https://api.openai.com/v1/chat/completions",
                       json=payload, stream=True)

  # httpx example:
  client = get_hardened_httpx_client()
  resp = client.post(url, json=payload)

Providers should import from here instead of constructing raw
requests/httpx clients with default timeouts.
"""

from __future__ import annotations

import logging
import random
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "HardenedTimeout",
    "HARDENED_TIMEOUT",
    "POOL_LIMITS",
    "KEEPALIVE_CONFIG",
    "get_hardened_session",
    "get_hardened_httpx_client",
    "get_hardened_async_httpx_client",
    "is_transient_error",
    "is_transient_status",
    "with_retry",
    "hardened_post",
    "hardened_get",
    "retry_with_backoff",
]

# ── Timeout & Pool Configuration (spec: connect 15, write 60, read 120, pool 30) ──


@dataclass(frozen=True)
class HardenedTimeout:
    """Granular timeout bundle — mirrors httpx.Timeout semantics.

    For requests (which only supports connect/read), write is folded
    into read, and pool is handled via adapter pool settings.
    """

    connect: float = 15.0
    write: float = 60.0
    read: float = 120.0
    pool: float = 30.0

    def as_requests_tuple(self) -> tuple[float, float]:
        """Return (connect, read) for requests — write folded into read.

        requests timeout is (connect, read) where read is time between
        bytes. We use max(write, read) for the read slot to allow large
        payload flushes (write) and long server thinking (read).
        """
        return (self.connect, max(self.write, self.read))

    def as_httpx_timeout(self) -> Any:
        """Return httpx.Timeout if httpx is available, else self."""
        try:
            import httpx

            return httpx.Timeout(
                connect=self.connect,
                write=self.write,
                read=self.read,
                pool=self.pool,
            )
        except ImportError:
            return self

    def as_aiohttp_timeout(self) -> Any:
        """Return aiohttp.ClientTimeout if aiohttp is available."""
        try:
            import aiohttp

            return aiohttp.ClientTimeout(
                connect=self.connect,
                sock_connect=self.connect,
                sock_read=self.read,
            )
        except ImportError:
            return None


HARDENED_TIMEOUT = HardenedTimeout(
    connect=15.0,
    write=60.0,
    read=120.0,
    pool=30.0,
)

# Pool limits — prevent silent connection drops over multi-minute turns
POOL_LIMITS = {
    "max_keepalive_connections": 20,
    "max_connections": 100,
    "keepalive_expiry": 30.0,
    "pool_connections": 20,
    "pool_maxsize": 20,
}

KEEPALIVE_CONFIG = {
    "keepalive": True,
    "keepalive_expiry": 30.0,
    # TCP keepalive probes — OS-level, via socket_options
    "tcp_keepalive": {
        "enabled": True,
        "idle": 60,      # seconds before first probe
        "interval": 30,  # seconds between probes
        "count": 3,      # probes before drop
    },
}

# ── Socket options for TCP keepalive ─────────────────────────────────


def _get_keepalive_socket_options() -> list[tuple[int, int, int]]:
    """Build socket_options for TCP keepalive — best-effort per platform."""
    opts: list[tuple[int, int, int]] = []
    try:
        opts.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
        # TCP_KEEPIDLE / TCP_KEEPALIVE platform variants
        if hasattr(socket, "TCP_KEEPIDLE"):
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_CONFIG["tcp_keepalive"]["idle"]))  # type: ignore[attr-defined]
        elif hasattr(socket, "TCP_KEEPALIVE"):
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, KEEPALIVE_CONFIG["tcp_keepalive"]["idle"]))  # type: ignore[attr-defined]
        if hasattr(socket, "TCP_KEEPINTVL"):
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_CONFIG["tcp_keepalive"]["interval"]))  # type: ignore[attr-defined]
        if hasattr(socket, "TCP_KEEPCNT"):
            opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KEEPALIVE_CONFIG["tcp_keepalive"]["count"]))  # type: ignore[attr-defined]
        # Disable Nagle for low-latency streaming
        opts.append((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1))
    except Exception:
        # Best-effort — if platform lacks these, keep at least SO_KEEPALIVE
        pass
    return opts


# ── _KeepAliveAdapter: proper socket option injection ────────────────


class _KeepAliveAdapter:
    """HTTPAdapter subclass that reliably injects TCP keepalive socket options.

    The standard HTTPAdapter does not expose a clean way to pass
    socket_options through to urllib3's PoolManager.  Previous code
    attempted to monkey-patch ``poolmanager_kwargs`` after construction,
    but ``init_poolmanager()`` is called during ``__init__`` (or lazily
    on first request) *without* consulting that attribute — so the
    options were silently dropped.

    This subclass overrides ``init_poolmanager()`` to forward
    ``socket_options`` as a keyword argument (urllib3 ≥ 2.0) and wraps
    each option with a per-option guard so platform-specific ``OSError``
    on macOS vs Linux never aborts adapter creation.
    """

    def __new__(cls, *, socket_options: list | None = None, **adapter_kwargs):
        """Dynamically create an HTTPAdapter subclass with socket_options.

        We use __new__ to defer the ``from requests.adapters import HTTPAdapter``
        import so callers that never touch requests don't pay the import cost.
        """
        from requests.adapters import HTTPAdapter as _BaseAdapter

        safe_opts = _safe_socket_options(socket_options or [])

        class _Adapter(_BaseAdapter):
            def init_poolmanager(self, num_pools, maxsize, block=False, **kw):
                if safe_opts:
                    kw.setdefault("socket_options", safe_opts)
                super().init_poolmanager(num_pools, maxsize, block=block, **kw)

        return _Adapter(**adapter_kwargs)


def _safe_socket_options(
    opts: list[tuple[int, int, int]],
) -> list[tuple[int, int, int]]:
    """Filter socket options to those supported on the current platform.

    Each option is tested via a throwaway socket; options that raise
    ``OSError`` (e.g. TCP_KEEPIDLE on macOS) are silently dropped.
    """
    safe: list[tuple[int, int, int]] = []
    for level, optname, value in opts:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(level, optname, value)
                safe.append((level, optname, value))
            except OSError:
                logger.debug(
                    "Dropping unsupported socket option (%s, %s, %s)",
                    level, optname, value,
                )
            finally:
                s.close()
        except Exception:
            pass
    return safe


# ── Hardened Session / Client Factories ────────────────────────────────


def get_hardened_session(
    timeout: HardenedTimeout | None = None,
    pool_connections: int = 20,
    pool_maxsize: int = 20,
) -> Any:  # requests.Session
    """Create a hardened requests.Session.

    Features:
      - Explicit connect/read timeouts (write folded into read)
      - Connection pooling (pool_connections/pool_maxsize)
      - TCP keepalive via socket_options (best-effort)
      - Retry disabled at adapter level — we handle retries with
        backoff+jitter in with_retry/hardened_post to cover write timeouts
        which urllib3 Retry does not classify as retryable by default
      - Keep-Alive header

    Args:
        timeout: Timeout bundle (defaults to HARDENED_TIMEOUT)
        pool_connections: Number of connection pools to cache
        pool_maxsize: Max connections per pool

    Returns:
        requests.Session instance
    """
    import requests
    from requests.adapters import HTTPAdapter

    try:
        from urllib3.util.retry import Retry
    except ImportError:
        Retry = None  # type: ignore

    timeout = timeout or HARDENED_TIMEOUT
    session = requests.Session()
    session.headers.update({"Connection": "keep-alive"})

    # Keepalive socket options — must be set on the poolmanager
    socket_options = _get_keepalive_socket_options()

    # Configure retry — we disable retries here because write timeouts and
    # RemoteProtocolError are not retried by urllib3 by default; we handle
    # retries at the call site with is_transient_error + backoff
    retry = None
    if Retry is not None:
        retry = Retry(
            total=0,  # no retries at adapter level — we do it ourselves
            connect=0,
            read=0,
            status=0,
            redirect=False,
        )

    # Use a single adapter for both http and https with keepalive
    # Note: pool_block=False to avoid hanging when pool is exhausted
    adapter_kwargs: dict[str, Any] = {
        "pool_connections": pool_connections,
        "pool_maxsize": pool_maxsize,
        "max_retries": retry if retry is not None else 0,
        "pool_block": False,
    }

    # Proper HTTPAdapter subclass that injects socket_options via
    # init_poolmanager — the only reliable way across urllib3 versions.
    # The previous approach of setting poolmanager_kwargs after construction
    # was silently dropped because init_poolmanager had already been called.
    try:
        adapter = _KeepAliveAdapter(socket_options=socket_options, **adapter_kwargs)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    except Exception as e:
        logger.debug("Failed to configure keepalive adapter: %s", e, exc_info=True)
        # Fallback: basic adapter without keepalive
        adapter = HTTPAdapter(pool_connections=pool_connections, pool_maxsize=pool_maxsize)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

    # Store timeout for later use by hardened_post
    session._wisp_hardened_timeout = timeout  # type: ignore[attr-defined]
    return session


def get_hardened_httpx_client(
    timeout: HardenedTimeout | None = None,
    limits: dict[str, Any] | None = None,
    follow_redirects: bool = True,
) -> Any:  # httpx.Client
    """Create a hardened httpx.Client.

    Requires httpx to be installed (dev dependency). Falls back to
    get_hardened_session() if httpx is not available.

    Features:
      - Granular connect/write/read/pool timeouts
      - Connection pooling with keepalive
      - TCP keepalive via transport socket_options (if supported)
      - http2 disabled (more stable for streaming)

    Returns:
        httpx.Client or requests.Session fallback
    """
    timeout = timeout or HARDENED_TIMEOUT
    limits = limits or POOL_LIMITS

    try:
        import httpx
    except ImportError:
        logger.debug("httpx not installed, falling back to requests Session")
        return get_hardened_session(timeout=timeout)

    # httpx.Timeout with all four granular timeouts
    httpx_timeout = httpx.Timeout(
        connect=timeout.connect,
        write=timeout.write,
        read=timeout.read,
        pool=timeout.pool,
    )

    # httpx.Limits for pooling
    httpx_limits = httpx.Limits(
        max_keepalive_connections=limits.get("max_keepalive_connections", 20),
        max_connections=limits.get("max_connections", 100),
        keepalive_expiry=limits.get("keepalive_expiry", 30.0),
    )

    # Socket options for keepalive — httpx 0.24+ supports via transport.
    # CRITICAL: When passing a custom `transport` to httpx.Client, the
    # top-level `limits` kwarg is IGNORED — httpx uses the transport's
    # own limits. We must pass `limits` to HTTPTransport directly.
    transport = None
    try:
        socket_options = _get_keepalive_socket_options()
        transport_kwargs: dict[str, Any] = {
            "retries": 0,  # we handle retries ourselves
            "limits": httpx_limits,  # enforce pool bounds on the transport
        }
        # httpx 0.27+ supports socket_options via HTTPTransport
        if socket_options:
            transport_kwargs["socket_options"] = socket_options
        try:
            transport = httpx.HTTPTransport(**transport_kwargs)
        except TypeError:
            # Older httpx versions may not support socket_options kwarg
            transport_kwargs.pop("socket_options", None)
            transport = httpx.HTTPTransport(**transport_kwargs)
    except Exception:
        transport = None

    client_kwargs: dict[str, Any] = {
        "timeout": httpx_timeout,
        "follow_redirects": follow_redirects,
        "http2": False,
    }
    if transport is not None:
        client_kwargs["transport"] = transport
        # Don't pass top-level limits when transport is set — httpx ignores it
    else:
        client_kwargs["limits"] = httpx_limits

    try:
        client = httpx.Client(**client_kwargs)
        return client
    except Exception as e:
        logger.debug("Failed to create httpx client with limits/transport: %s", e, exc_info=True)
        # Fallback to minimal httpx client
        return httpx.Client(timeout=httpx_timeout, follow_redirects=follow_redirects)


def get_hardened_async_httpx_client(
    timeout: HardenedTimeout | None = None,
    limits: dict[str, Any] | None = None,
) -> Any:  # httpx.AsyncClient
    """Create a hardened httpx.AsyncClient for async contexts."""
    timeout = timeout or HARDENED_TIMEOUT
    limits = limits or POOL_LIMITS

    try:
        import httpx
    except ImportError:
        return None

    httpx_timeout = httpx.Timeout(
        connect=timeout.connect,
        write=timeout.write,
        read=timeout.read,
        pool=timeout.pool,
    )
    httpx_limits = httpx.Limits(
        max_keepalive_connections=limits.get("max_keepalive_connections", 20),
        max_connections=limits.get("max_connections", 100),
        keepalive_expiry=limits.get("keepalive_expiry", 30.0),
    )

    try:
        return httpx.AsyncClient(timeout=httpx_timeout, limits=httpx_limits, http2=False)
    except Exception as e:
        logger.debug("Failed to create async httpx client: %s", e, exc_info=True)
        return None


# ── Transient Error Detection ────────────────────────────────────────


def is_transient_status(status_code: Optional[int]) -> bool:
    """Check if HTTP status code is transient and retryable."""
    if status_code is None:
        return False
    # 429 Too Many Requests, 500-599 Server Errors (500 is transient for our use)
    return status_code == 429 or 500 <= status_code <= 599


def is_transient_error(exc: BaseException) -> bool:
    """Check if exception is transient and retryable.

    Covers:
      - TimeoutError, ConnectionResetError (stdlib)
      - httpcore.WriteTimeout, httpcore.ReadTimeout, httpcore.ConnectTimeout
      - h11/h2 RemoteProtocolError
      - httpx.WriteTimeout, httpx.ReadTimeout, httpx.ConnectTimeout,
        httpx.RemoteProtocolError
      - requests.exceptions.Timeout, ConnectionError
      - aiohttp.ClientError variants
      - Generic string matching for wrapped errors

    Cancellation signals (CancelledError, KeyboardInterrupt, GeneratorExit,
    SystemExit) are NEVER transient — they must propagate immediately
    without retry (Phase 2.2, D2). Checked first so message-substring
    fallback cannot misclassify e.g. CancelledError("connection reset").
    """
    # Cancellation-first: never retry, even if the message contains a
    # transient substring. Local import keeps this module importable even
    # if contracts gains dependencies later (contracts itself is stdlib-only).
    try:
        from wisp.core.contracts import is_cancellation as _is_cancelled

        if _is_cancelled(exc):
            return False
    except ImportError:
        import asyncio as _asyncio

        if isinstance(exc, (_asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit)):
            return False
    # Direct type checks for stdlib
    if isinstance(exc, (TimeoutError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True

    # Check exception type name for httpcore/h11/httpx without importing
    # (avoids hard dependency, works with wrapped errors)
    exc_type_name = type(exc).__name__
    exc_module = getattr(type(exc), "__module__", "")

    transient_names = {
        "WriteTimeout",
        "ReadTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "TimeoutException",
        "RemoteProtocolError",
        "ConnectionResetError",
        "HTTPException",  # h11
    }

    if exc_type_name in transient_names:
        return True

    # Check module-qualified names
    if "httpcore" in exc_module and "Timeout" in exc_type_name:
        return True
    if "httpx" in exc_module and "Timeout" in exc_type_name:
        return True
    if "h11" in exc_module or "h2" in exc_module:
        if "ProtocolError" in exc_type_name or "RemoteProtocolError" in exc_type_name:
            return True
    if "aiohttp" in exc_module and "Client" in exc_type_name:
        # aiohttp ClientConnectorError, ServerDisconnectedError, etc.
        if any(x in exc_type_name for x in ("Timeout", "Connection", "ClientConnector", "ServerDisconnected")):
            return True

    # requests exceptions
    try:
        import requests.exceptions

        if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError)):
            return True
        # requests wraps many as RequestException with string
        if isinstance(exc, requests.exceptions.RequestException):
            # Check if it's a timeout/connection variant via string
            msg = str(exc).lower()
            if any(k in msg for k in ("timeout", "write operation timed out", "connection reset", "connection aborted", "broken pipe", "remoteprotocolerror")):
                return True
    except ImportError:
        pass

    # Fallback: string matching for wrapped/composed errors
    # This catches cases like "TimeoutError: The write operation timed out"
    # which is the exact error from the bug report
    msg_lower = str(exc).lower()
    transient_substrings = [
        "write operation timed out",
        "write timeout",
        "read timeout",
        "connect timeout",
        "pool timeout",
        "timeouterror",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "remoteprotocolerror",
        "remote protocol error",
        "connection closed",
        "server disconnected",
        "httpcore.writetimeout",
        "httpcore.readtimeout",
        "httpx.writetimeout",
        "httpx.readtimeout",
        "httpx.connecttimeout",
        "aiohttp.writetimeout",
    ]
    if any(s in msg_lower for s in transient_substrings):
        return True

    # Check __cause__ and __context__ for wrapped transient errors
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        if is_transient_error(cause):
            return True
    context = getattr(exc, "__context__", None)
    if context is not None and context is not exc and context is not cause:
        if is_transient_error(context):
            return True

    return False


# ── Retry with Backoff ───────────────────────────────────────────────


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
) -> Callable:
    """Decorator for retrying transient errors with exponential backoff + jitter.

    Args:
        max_attempts: Maximum attempts (including first, so 3 = 2 retries)
        base_delay: Base delay in seconds (exponential: base * 2^(attempt-1))
        max_delay: Cap for delay
        jitter: Add random jitter (0-0.5s) to avoid thundering herd

    Example:
        @retry_with_backoff(max_attempts=3)
        def fetch():
            return requests.get(url, timeout=...)
    """
    import functools

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Optional[BaseException] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except BaseException as exc:
                    import asyncio as _asyncio_cancel_guard_retry

                    if isinstance(
                        exc,
                        (_asyncio_cancel_guard_retry.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
                    ):
                        raise
                    last_exc = exc
                    if not is_transient_error(exc) and not is_transient_status(getattr(exc, "status_code", None)):
                        # Check for status code in response
                        resp = getattr(exc, "response", None)
                        status = getattr(resp, "status_code", None) if resp is not None else None
                        if not is_transient_status(status):
                            raise  # not retryable
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay += random.uniform(0, 0.5)
                    logger.warning(
                        "Transient %s on attempt %d/%d, retrying in %.2fs: %s",
                        type(exc).__name__,
                        attempt,
                        max_attempts,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
            # Should not reach here, but if max_attempts is 0
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("retry_with_backoff: no attempts made")

        return wrapper

    return decorator


async def async_retry_with_backoff(
    coro_func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    **kwargs,
) -> Any:
    """Async retry helper for coroutines — same backoff as retry_with_backoff."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_func(*args, **kwargs)
        except BaseException as exc:
            import asyncio as _asyncio_cancel_guard_aretry

            if isinstance(
                exc,
                (_asyncio_cancel_guard_aretry.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
            ):
                raise
            last_exc = exc
            if not is_transient_error(exc):
                resp = getattr(exc, "response", None)
                status = getattr(resp, "status_code", None) if resp is not None else None
                if not is_transient_status(status) and not is_transient_status(getattr(exc, "status_code", None)):
                    raise
            if attempt == max_attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay += random.uniform(0, 0.5)
            logger.warning(
                "Transient %s on attempt %d/%d, retrying in %.2fs: %s",
                type(exc).__name__,
                attempt,
                max_attempts,
                delay,
                exc,
            )
            await _async_sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("async_retry_with_backoff: no attempts made")


async def _async_sleep(delay: float) -> None:
    """Async-only sleep — never blocks the event loop.

    Previous version caught RuntimeError and fell back to blocking
    time.sleep(), which would stall the entire event loop thread.
    Since this is an async function, a running loop is guaranteed.
    """
    import asyncio

    await asyncio.sleep(delay)


# ── Hardened Request Wrappers ────────────────────────────────────────


def _httpx_stream_post(client: Any, url: str, call_kwargs: dict[str, Any]) -> Any:
    """Execute a streaming POST via httpx's context-managed ``client.stream()``.

    httpx does NOT accept ``stream=True`` on ``client.post()`` — it raises
    ``TypeError``.  The correct API is::

        with client.stream("POST", url, ...) as resp:
            ...

    We enter the context manager here and return the response object.
    The caller (or eventual consumer) is responsible for closing the
    response when iteration is complete.  Keeping the CM alive is safe
    because httpx's Response holds a reference to the stream and closing
    the response also exits the underlying transport stream.
    """
    # Remove 'stream' if it leaked into kwargs from the generic path
    call_kwargs.pop("stream", None)

    # Enter the context manager and hold it open
    cm = client.stream("POST", url, **call_kwargs)
    resp = cm.__enter__()
    # Attach the context manager so callers can clean up
    resp._wisp_stream_cm = cm  # type: ignore[attr-defined]
    return resp


def hardened_post(
    session: Any,
    url: str,
    *,
    json: Any | None = None,
    data: Any | None = None,
    headers: dict[str, str] | None = None,
    stream: bool = False,
    timeout: HardenedTimeout | tuple[float, float] | None = None,
    max_attempts: int = 3,
    **kwargs: Any,
) -> Any:
    """POST with hardened timeouts and transient retry.

    Wraps session.post (requests.Session or httpx.Client) with:
      - Granular timeouts (connect 15, write 60, read 120, pool 30)
      - Retry for write timeouts, connection resets, RemoteProtocolError
      - Exponential backoff with jitter

    Args:
        session: requests.Session, httpx.Client, or similar with .post
        url: Target URL
        json: JSON payload
        data: Raw data payload
        headers: Extra headers
        stream: Whether to stream response
        timeout: Override timeout (HardenedTimeout or (connect, read) tuple)
        max_attempts: Max attempts (3 = 2 retries)
        **kwargs: Additional args passed to post

    Returns:
        Response object

    Raises:
        Last exception if all retries exhausted, or non-transient error
    """
    # Resolve timeout
    if timeout is None:
        # Try to use session's hardened timeout, else default
        timeout = getattr(session, "_wisp_hardened_timeout", HARDENED_TIMEOUT)
    # Backend flag must exist on every path: a tuple timeout (or a session
    # without a stored bundle) skips the branch below that assigns it.
    is_httpx = False
    if isinstance(timeout, HardenedTimeout):
        # For requests, we need (connect, read) tuple
        # Check if session is httpx (has .post with timeout as httpx.Timeout)
        try:
            import httpx

            is_httpx = isinstance(session, httpx.Client) or isinstance(session, httpx.AsyncClient)
        except ImportError:
            pass
        if is_httpx:
            # httpx handles HardenedTimeout via its own Timeout object
            # We pass the HardenedTimeout's httpx equivalent
            request_timeout = timeout.as_httpx_timeout()
        else:
            request_timeout = timeout.as_requests_tuple()
    else:
        request_timeout = timeout

    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            # Merge headers if provided
            call_kwargs: dict[str, Any] = dict(kwargs)
            if headers is not None:
                call_kwargs["headers"] = headers
            if json is not None:
                call_kwargs["json"] = json
            if data is not None:
                call_kwargs["data"] = data
            call_kwargs["timeout"] = request_timeout

            # Branch streaming by backend: httpx uses context-managed
            # client.stream(), requests uses post(stream=True).
            # Passing stream=True to httpx.Client.post() raises TypeError.
            if stream and is_httpx:
                resp = _httpx_stream_post(session, url, call_kwargs)
            else:
                if stream:
                    call_kwargs["stream"] = True
                resp = session.post(url, **call_kwargs)

            # Check for transient status codes - retry before returning
            status = getattr(resp, "status_code", None)
            if is_transient_status(status) and attempt < max_attempts:
                # Close the response body to return the socket to the pool
                # before sleeping — prevents pool starvation under 429 bursts.
                try:
                    resp.close()
                except Exception:
                    pass
                delay = min(0.5 * (2 ** (attempt - 1)), 8.0) + random.uniform(0, 0.5)
                logger.warning(
                    "Transient status %s on attempt %d/%d, retrying in %.2fs",
                    status,
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            return resp
        except BaseException as exc:
            # Cancellation-first (D2): never log-and-retry a cancel.
            # Inline isinstance (mirrors contracts.is_cancellation) to avoid
            # an import edge from the transport hot path to contracts.
            import asyncio as _asyncio_cancel_guard

            if isinstance(
                exc,
                (_asyncio_cancel_guard.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
            ):
                raise
            last_exc = exc
            if not is_transient_error(exc):
                raise
            if attempt == max_attempts:
                raise
            delay = min(0.5 * (2 ** (attempt - 1)), 8.0) + random.uniform(0, 0.5)
            logger.warning(
                "Transient %s on attempt %d/%d, retrying in %.2fs: %s",
                type(exc).__name__,
                attempt,
                max_attempts,
                delay,
                exc,
            )
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("hardened_post: no attempts made")


def hardened_get(
    session: Any,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: HardenedTimeout | tuple[float, float] | None = None,
    max_attempts: int = 3,
    **kwargs: Any,
) -> Any:
    """GET with hardened timeouts and retry (same as hardened_post)."""
    if timeout is None:
        timeout = getattr(session, "_wisp_hardened_timeout", HARDENED_TIMEOUT)
    if isinstance(timeout, HardenedTimeout):
        try:
            import httpx

            is_httpx = isinstance(session, httpx.Client)
        except ImportError:
            is_httpx = False
        request_timeout = timeout.as_httpx_timeout() if is_httpx else timeout.as_requests_tuple()
    else:
        request_timeout = timeout

    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            call_kwargs: dict[str, Any] = dict(kwargs)
            if headers is not None:
                call_kwargs["headers"] = headers
            call_kwargs["timeout"] = request_timeout
            resp = session.get(url, **call_kwargs)
            status = getattr(resp, "status_code", None)
            if is_transient_status(status) and attempt < max_attempts:
                # Close the response body to return the socket to the pool
                # before sleeping — prevents pool starvation under 429 bursts.
                try:
                    resp.close()
                except Exception:
                    pass
                delay = min(0.5 * (2 ** (attempt - 1)), 8.0) + random.uniform(0, 0.5)
                logger.warning(
                    "Transient status %s on attempt %d/%d, retrying in %.2fs",
                    status,
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            return resp
        except BaseException as exc:
            import asyncio as _asyncio_cancel_guard_get

            if isinstance(
                exc,
                (_asyncio_cancel_guard_get.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
            ):
                raise
            last_exc = exc
            if not is_transient_error(exc):
                raise
            if attempt == max_attempts:
                raise
            delay = min(0.5 * (2 ** (attempt - 1)), 8.0) + random.uniform(0, 0.5)
            logger.warning(
                "Transient %s on attempt %d/%d, retrying in %.2fs: %s",
                type(exc).__name__,
                attempt,
                max_attempts,
                delay,
                exc,
            )
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("hardened_get: no attempts made")


# Alias for decorator usage
with_retry = retry_with_backoff
