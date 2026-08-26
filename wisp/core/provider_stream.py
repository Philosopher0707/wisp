"""Guarded provider streaming — stall detection, retries, honest failure.

Extracted from WispAgentCore (stateless.py) as part of the monolith
extraction program. Everything the guard needs arrives as parameters:
open_stream, normalize_event, deadline values, attempt count. That makes
this module testable without constructing a core, and lets the core keep
its env-tunable class attributes as the single place tests patch.

History this encodes (do not simplify away):
- NVIDIA's endpoint closes ~1-in-5 identical requests with zero deltas,
  and some hold requests with no first byte indefinitely.
- A mid-stream gap means a dead connection: one unguarded read used to
  hold the per-session lock until the 30-minute turn watchdog.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Callable, Iterable

from wisp.core.events import (
    error as error_event,
    provider_status as provider_status_event,
)

logger = logging.getLogger(__name__)


def _flatten_event(ev: Any) -> dict[str, Any]:
    """Convert canonical AgentEvent to flat dict for backward compat."""
    if isinstance(ev, dict):
        return dict(ev)
    flat = dict(ev.data)
    flat["type"] = str(ev.type)
    flat["timestamp"] = ev.timestamp
    return flat


async def guarded_provider_stream(
    open_stream: Callable[[], Any],
    normalize_event: Callable[[Any], dict[str, Any]],
    bookkeeping_types: Iterable[str],
    *,
    first_token_deadline_s: float,
    chunk_deadline_s: float,
    max_attempts: int,
) -> AsyncIterator[dict[str, Any]]:
    """Yield events from one provider round-trip with stall recovery.

    open_stream() must return a FRESH stream each attempt (a consumed
    stream cannot be retried). Transient API errors (429/5xx) and empty
    streams retry; permanent errors surface immediately; a mid-stream
    stall after partial output ends cleanly WITH a truncation notice —
    retrying then would duplicate what the consumer already saw.
    """
    bookkeeping = set(bookkeeping_types)
    last_transient_status: int | None = None
    for attempt in range(1, max_attempts + 1):
        got_meaningful = False
        stream_stats: dict[str, Any] | None = None
        api_status: int | None = None
        stream = open_stream()
        stalled = False
        try:
            while True:
                if not got_meaningful:
                    try:
                        event = await asyncio.wait_for(
                            stream.__anext__(),
                            timeout=first_token_deadline_s,
                        )
                    except StopAsyncIteration:
                        break  # clean end, no meaningful events
                    except asyncio.TimeoutError:
                        stalled = True
                        break
                else:
                    try:
                        event = await asyncio.wait_for(
                            stream.__anext__(),
                            timeout=chunk_deadline_s,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        stalled = True
                        break
                normalized = normalize_event(event)
                ntype = str(normalized.get("type", ""))
                if ntype == "stream_stats":
                    stream_stats = normalized
                if ntype == "error":
                    st = normalized.get("status")
                    if isinstance(st, int) and (st == 429 or st >= 500):
                        api_status = st
                        continue  # transient — hold it for the retry path
                    yield event
                    return  # permanent API error: surface immediately
                if ntype not in bookkeeping:
                    got_meaningful = True
                yield event
        finally:
            if stalled or not got_meaningful:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()

        if got_meaningful:
            if stalled:
                # Mid-stream death after partial output: retrying would
                # duplicate what the consumer already saw, so surface
                # the truncation and end cleanly instead of hanging.
                yield _flatten_event(provider_status_event(
                    "chunk_stall",
                    detail=(
                        f"provider sent no data for "
                        f"{chunk_deadline_s:.0f}s mid-stream — "
                        "output may be truncated"
                    ),
                ))
            return

        if attempt < max_attempts and (stalled or api_status is not None or not got_meaningful) is not False:
            pass  # fall through to shared retry handling below
        if got_meaningful and api_status is None:
            return

        if attempt < max_attempts:
            last_transient_status = api_status or last_transient_status
            if stalled:
                reason = f"no data for {first_token_deadline_s:.0f}s"
                detail = ""
            else:
                reason = "closed without any content"
                # HTTP-200-with-zero-deltas under parallel load is the
                # throttle signature; the counters separate "server sent
                # literally nothing" from "sent chunks we couldn't use".
                detail = ""
                if stream_stats:
                    detail = (
                        f" [sse_lines={stream_stats.get('sse_lines')} "
                        f"usable={stream_stats.get('usable_deltas')} "
                        f"empty_choice_chunks={stream_stats.get('empty_choice_chunks')} "
                        f"finish={stream_stats.get('finish_reason')}]"
                    )
            logger.warning(
                "Provider stream %s%s (attempt %d/%d) — retrying",
                reason, detail, attempt, max_attempts,
            )
            # Immediate retry into a throttling endpoint reproduces the
            # failure; a short jittered pause gives the window a chance
            # to reopen without meaningfully delaying healthy streams.
            # 429/5xx get progressively longer waits than silent-empty
            # closes — the server explicitly told us to slow down.
            if api_status is not None:
                base = 1.5 * attempt
            elif not stalled:
                base = 0.75
            else:
                base = 0.0
            jitter = (asyncio.get_event_loop().time() * 1000 % 1.5)
            wait_s = base + jitter
            if wait_s > 0:
                logger.info(
                    "Provider retry backoff %.1fs (status=%s)",
                    wait_s, api_status or "-",
                )
                await asyncio.sleep(wait_s)
            continue

        if last_transient_status is not None:
            yield _flatten_event(error_event(
                f"Provider kept rejecting requests (HTTP {last_transient_status}) "
                f"after {max_attempts} attempts — rate limited or degraded. "
                "Try again shortly.",
                recoverable=True,
            ))
        else:
            yield _flatten_event(error_event(
                f"Provider returned no usable response after {max_attempts} "
                "attempts — the model endpoint is misbehaving. Try again shortly.",
                recoverable=True,
            ))
        return
