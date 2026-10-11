# ADR: One owner for rate-limit retry, and `Retry-After` honoured

## Status: Implemented 2026-10-06

## Context

A real REPL session against OpenRouter (model `inclusionai/ling-3.1-flash`) answered every request with HTTP 429. The runtime log
shows what wisp did about it, for one user message (timestamps from `.agent/runtime.log`):

```
20:49:40  stream round 1 ended with nothing                     <- outer layer, attempt 1/3
20:49:42  transport: Transient status 429, attempt 1/3, 0.67s   <- inner layer
20:49:44  transport: Transient status 429, attempt 2/3, 1.04s
20:49:46  stream round 2 ended with nothing                     <- outer layer, attempt 2/3
20:49:51  transport: Transient status 429, attempt 1/3, 0.91s
20:49:53  transport: Transient status 429, attempt 2/3, 1.01s
          "Provider kept rejecting requests (HTTP 429) after 3 attempts"
```

Two layers each retried a 429 and they multiplied:

* `hardened_post` (`wisp/core/transport.py`), called by the OpenAI-compatible provider with `max_attempts=3`: three requests, 0.5 s then
  1 s apart (plus up to 0.5 s of jitter).
* `guarded_provider_stream` (`wisp/core/provider_stream.py`): three rounds, each of which called the provider, hence `hardened_post`, again.

That is up to **nine HTTP requests in about twenty seconds** against an endpoint that is already throttling. Rate-limit windows are
usually a minute, so retries a second or two apart cannot succeed; they only add load. Neither layer read the `Retry-After` header, so
a server that said "wait 40 seconds" was asked again after one. And the final message said "after 3 attempts" when nine requests had
been sent.

## Decision

1. **The guarded stream owns status retry.** It knows the turn, honours cancellation and already owns stall recovery. `hardened_post`
   gains `retry_status` (default `True`, so every other caller is unchanged); the provider passes `False`. A 429 or 5xx now comes back
   after one request. Transport ERRORS (write timeout, connection reset) are still retried in `hardened_post` either way, because nothing
   above can tell them from a stall.
2. **`Retry-After` is read and carried.** `parse_retry_after` (delay-seconds or HTTP-date; missing, malformed, negative, non-finite or
   non-string is `None`; a past date is `0`) is pure. The provider adds `retry_after` to its error event for 429 and 5xx only.
3. **The wait follows the server.** With advice, the retry waits that long plus up to a second of jitter (never under one second). Advice
   longer than `MAX_RETRY_AFTER_S` (30 s) is not slept through and not ignored: the stream stops at once with
   "the server asked to wait 600s ... Try again in about 600s", recoverable. Advice equal to the cap is waited for.
4. **Without advice a 429 backs off in seconds**, `3 s * attempt` plus jitter (a rate limit is a window, not a blip); a 5xx keeps
   `1.5 s * attempt`; transport errors keep `1 s * attempt`. With the inner layer no longer retrying statuses, three requests now span
   about ten seconds instead of nine over twenty.
5. **The message is true.** "after N attempts" now equals the requests sent, and carries the server's advised pause when there was one.

## Consequences

* A throttled endpoint sees 3 requests per message, not 9. A 429 that clears within a few seconds is recovered from (one test pins this).
* The total patience for an un-advised 429 is shorter in wall-clock (about ten seconds against twenty) but spread over fewer, later requests.
* A server that advises a long wait now gets an immediate, specific message instead of a hang or a retry it will refuse.
* `hardened_get` (health check, model listing) is untouched: it is not a stream and has no second layer above it.

## Not changed (noted)

* Log lines are written over the spinner line in the REPL (`waiting 3.6s20:49:05 [WARNING] ...`). Cosmetic, separate.
* Ollama's own retry path (`test_ollama_client_retry`) is unaffected.
* `X-RateLimit-Reset` (OpenRouter) is not parsed: its format is not guaranteed here, and `Retry-After` is the standard.
