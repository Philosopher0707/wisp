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
import random
from typing import Any, AsyncIterator, Callable

from wisp.core.events import (
    CODE_PROVIDER_STREAM,
    canonical_event,
    error as error_event,
    provider_status as provider_status_event,
)

try:
    from wisp.core.transport import is_transient_error as _is_transport_transient
    from wisp.core.transport import is_transient_status as _is_transient_status
except ImportError:
    _is_transport_transient = None  # type: ignore[assignment]
    _is_transient_status = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class _TransientOpenError(Exception):
    """Internal sentinel for transient errors during open_stream()."""

    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.__cause__ = cause


# Normalized terminal markers by provider dialect (G1B §6). A terminal
# marker ALONE (no content/tool payload before it) is an empty stream, not
# a meaningful response — adapters emit these unconditionally on clean end,
# so counting them as meaningful would bless vacuous success.
#
# This is the ONLY vocabulary the recovery classifier owns (ADR-0043 R2/R3).
# There is deliberately no second list of "bookkeeping" types: a vocabulary
# list participating in a *semantic* decision is what produced F43 (a bare
# typed `StreamComplete` blessed an empty attempt) and its sibling (an
# unrecognised, payload-less event did the same). Meaningfulness is decided
# by `_event_has_payload` for every event type instead.
TERMINAL_TYPES = frozenset({"done", "complete", "stream_complete"})


def _event_has_payload(normalized: dict[str, Any]) -> bool:
    """True if an event carries response content rather than being a bare marker.

    Applies to **every** event type, terminal or not (ADR-0043 R1): whether an
    attempt produced a response is a question about what the provider SENT, not
    about which dialect name it chose. That makes it impossible for a new
    provider spelling to change whether an empty attempt is retried.

    `final_content` is retained deliberately (ADR-0043 R6): a canonical **dict**
    passes through `canonical_event` unchanged, so a dict-shaped provider may
    supply it — dropping the key would make such a provider's payload invisible
    and turn a real response into a silent empty attempt.
    """
    for key in ("text", "content", "final_content", "tool_calls", "calls"):
        val = normalized.get(key)
        if isinstance(val, str) and val:
            return True
        if isinstance(val, (list, tuple, dict)) and len(val) > 0:
            return True
    # A lone tool call arrives flat (`name`/`arguments`), not under `calls`; without
    # this, a turn answered by exactly one tool call was retried as empty and then
    # reported as a provider failure.
    name = normalized.get("name")
    return str(normalized.get("type", "")) == "tool_call" and isinstance(name, str) and bool(name)


def _flatten_event(ev: Any) -> dict[str, Any]:
    """Convert canonical AgentEvent to flat dict for backward compat."""
    if isinstance(ev, dict):
        return dict(ev)
    flat = dict(ev.data)
    flat["type"] = str(ev.type)
    flat["timestamp"] = ev.timestamp
    return flat


def passthrough_if_canonical(event: Any) -> dict[str, Any]:
    """Normalizer for a stream that is ALREADY canonical (ADR-0039 R5).

    The guard obtains its events from the core's normalization-only
    boundary, so each event is already a canonical dict — re-canonicalizing
    it would be a second pass over the same event for no gain, and the
    main loop mutates what it receives, so the boundary's copy is the one
    that must survive.

    It stays **total** anyway: anything that is not a dict is still
    canonicalized through the single authority, so a future caller that
    wires the guard directly to a raw provider stream cannot crash it on a
    typed event. That is the property F40 was about.
    """
    if isinstance(event, dict):
        return event
    return canonical_event(event)


async def guarded_provider_stream(
    open_stream: Callable[[], Any],
    normalize_event: Callable[[Any], dict[str, Any]],
    *,
    first_token_deadline_s: float,
    chunk_deadline_s: float,
    max_attempts: int,
) -> AsyncIterator[dict[str, Any]]:
    """Yield events from one provider round-trip with stall recovery.

    open_stream() must return a FRESH stream each attempt (a consumed
    stream cannot be retried). Transient API errors (429/5xx) and empty
    streams retry; permanent errors surface immediately; a mid-stream
    stall after partial output ends WITH a truncation notice — retrying
    then would duplicate what the consumer already saw.

    Completion contract (G1B): the attempt ends MEANINGFUL only when payload
    events (content/tool calls) arrived. A bare terminal marker, a stream
    that ends without its terminal marker (truncated), or a mid-stream
    transport error after partial output all surface an explicit error —
    never silent success. No retry is added here: post-output retry would
    duplicate consumer-visible bytes (retry policy unchanged).
    """
    last_transient_status: int | None = None
    last_transient_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        got_meaningful = False
        saw_terminal = False
        stream_stats: dict[str, Any] | None = None
        api_status: int | None = None
        transient_error: BaseException | None = None
        stream = None
        stalled = False
        try:
            try:
                stream = open_stream()
            except BaseException as exc:
                if isinstance(
                    exc,
                    (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
                ):
                    raise
                # open_stream itself can raise transient socket errors (e.g., WriteTimeout during
                # large payload flush, ConnectionResetError)
                if _is_transport_transient is not None and _is_transport_transient(exc):
                    transient_error = exc
                    last_transient_error = exc
                    # Treat as stalled/empty for retry path
                    stalled = False
                    # Fall through to retry handling below
                    raise _TransientOpenError(exc)  # internal sentinel to trigger retry
                raise
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
                        except BaseException as exc:
                            # Cancellation-first (D2): never classify a cancel
                            # as transient, even if its message contains a
                            # transient substring.
                            if isinstance(
                                exc,
                                (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
                            ):
                                raise
                            if _is_transport_transient is not None and _is_transport_transient(exc):
                                transient_error = exc
                                last_transient_error = exc
                                break
                            raise
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
                        except BaseException as exc:
                            if isinstance(
                                exc,
                                (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
                            ):
                                raise
                            if _is_transport_transient is not None and _is_transport_transient(exc):
                                transient_error = exc
                                last_transient_error = exc
                                break
                            raise
                    normalized = normalize_event(event)
                    ntype = str(normalized.get("type", ""))
                    if ntype == "stream_stats":
                        stream_stats = normalized
                    if ntype == "error":
                        st = normalized.get("status")
                        # Use hardened is_transient_status if available
                        _is_trans = False
                        if _is_transient_status is not None:
                            _is_trans = _is_transient_status(st)
                        else:
                            _is_trans = isinstance(st, int) and (st == 429 or st >= 500)
                        if _is_trans:
                            api_status = st if isinstance(st, int) else 500
                            continue  # transient — hold it for the retry path
                        yield event
                        return  # permanent API error: surface immediately
                    # Classification is SEMANTIC (ADR-0043 R1/R2): whether an
                    # attempt produced a response is decided by what the event
                    # CARRIES, for every event type — never by which provider
                    # spelled it, and never by a vocabulary list. Terminal
                    # detection runs first so `saw_terminal` is recorded
                    # regardless of payload; meaningfulness is then the payload
                    # question, asked once and answered the same way for all
                    # types. No list can drift because there is no list.
                    is_terminal = ntype in TERMINAL_TYPES
                    if is_terminal:
                        saw_terminal = True
                    if _event_has_payload(normalized):
                        got_meaningful = True
                    if is_terminal:
                        # Provider-declared terminal marker (§6): ends the
                        # attempt here. A bare marker carries no payload, so
                        # it never rescues an empty attempt; post-terminal
                        # bytes/timeouts cannot retroactively fail a
                        # completed stream (§10 ordering rule). Markers are
                        # consumed, not forwarded (previous wire behavior:
                        # markers were never yielded).
                        break
                    yield event
            finally:
                if stalled or not got_meaningful or transient_error is not None:
                    if stream is not None:
                        aclose = getattr(stream, "aclose", None)
                        if aclose is not None:
                            try:
                                await aclose()
                            except Exception:
                                pass
        except _TransientOpenError:
            # Already captured transient_error, proceed to retry handling
            pass
        except BaseException as exc:
            # Cancellation-first (D2): propagate without retry bookkeeping.
            if isinstance(
                exc,
                (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit),
            ):
                raise
            # Check if this is a transient error that should be retried
            # This catches exceptions that escaped the inner loop (e.g., from
            # normalize_event or unexpected stream errors)
            if _is_transport_transient is not None and _is_transport_transient(exc):
                transient_error = exc
                last_transient_error = exc
                # Fall through to retry logic
            else:
                # Not transient — re-raise to outer handler (stateless will handle)
                raise

        if got_meaningful:
            if transient_error is not None:
                # Mid-stream transport error after partial output (G1B §8):
                # the bytes already yielded stay (diagnostics), but the
                # attempt is explicitly NON-complete — never silent success.
                # No retry: re-fetching would duplicate consumer-visible
                # output (retry policy unchanged).
                yield _flatten_event(error_event(
                    f"Provider stream failed mid-response "
                    f"({type(transient_error).__name__}: "
                    f"{str(transient_error)[:120]}) — output may be "
                    f"truncated; partial content retained",
                    recoverable=True,
                    code=CODE_PROVIDER_STREAM,
                    hint="transient network error after partial output; "
                         "retry the request (a fresh attempt starts clean)",
                ))
            elif stalled:
                # Mid-stream silence with the stream still open: retrying
                # would duplicate what the consumer already saw, so surface
                # the truncation and end cleanly instead of hanging.
                yield _flatten_event(provider_status_event(
                    "chunk_stall",
                    detail=(
                        f"provider sent no data for "
                        f"{chunk_deadline_s:.0f}s mid-stream — "
                        "output may be truncated"
                    ),
                ))
            elif not saw_terminal:
                # EOF without the provider's terminal marker (G1B §9):
                # the socket closed cleanly but the protocol never
                # completed — truncated, not successful.
                yield _flatten_event(error_event(
                    "Provider stream ended without its terminal marker — "
                    "output may be truncated; partial content retained",
                    recoverable=True,
                    code=CODE_PROVIDER_STREAM,
                    hint="the provider closed the stream before terminal "
                         "completion; retry the request",
                ))
            return

        # NOTE: got_meaningful always returns above, so reaching here means
        # the attempt produced nothing usable — fall through to retry handling.
        if attempt < max_attempts:
            last_transient_status = api_status or last_transient_status
            if transient_error is not None:
                reason = f"transient {type(transient_error).__name__}: {str(transient_error)[:120]}"
                detail = ""
            elif stalled:
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
            # 429/5xx and socket write timeouts get progressively longer
            # waits — the server explicitly told us to slow down or the
            # socket needs time to recover.
            if transient_error is not None:
                base = 1.0 * attempt
            elif api_status is not None:
                base = 1.5 * attempt
            elif not stalled:
                base = 0.75
            else:
                base = 0.0
            # Random jitter over the same 0–1.5s range (replaces the old
            # deprecated loop-clock derivation, which was deterministic-ish).
            jitter = random.uniform(0, 1.5)
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
        elif last_transient_error is not None:
            yield _flatten_event(error_event(
                f"Provider failed with transient socket error ({type(last_transient_error).__name__}: {str(last_transient_error)[:120]}) "
                f"after {max_attempts} attempts — connection unstable. Try again shortly.",
                recoverable=True,
            ))
        else:
            yield _flatten_event(error_event(
                f"Provider returned no usable response after {max_attempts} "
                "attempts — the model endpoint is misbehaving. Try again shortly.",
                recoverable=True,
            ))
        return
