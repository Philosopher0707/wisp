"""Read a streamed HTTP body line by line, as its bytes arrive."""

from __future__ import annotations

from typing import Any, Iterator


def split_lines(reads: Iterator[bytes]) -> Iterator[bytes]:
    """Lines (without CR/LF) from a sequence of byte reads, however the reads cut them."""
    buf = b""
    for chunk in reads:
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            yield line.rstrip(b"\r")
    if buf:
        yield buf.rstrip(b"\r")


def lines_as_they_arrive(resp: Any) -> Iterator[bytes]:
    """The response's lines as their bytes arrive.

    `iter_lines()` reads in 512-byte blocks and waits for each block to fill, so a stream of
    small deltas (SSE from OpenAI-compatible endpoints, NDJSON from Ollama) reached the stall guard in bursts, with silences longer than its limit, and a
    first chunk smaller than a block could miss the first-token deadline. `read1` returns what
    has arrived. Errors are mapped as `requests` maps them in `iter_content`, so the callers'
    handling is unchanged.
    """
    import requests
    from urllib3.exceptions import DecodeError, ProtocolError, ReadTimeoutError, SSLError
    from urllib3.response import BaseHTTPResponse

    raw = getattr(resp, "raw", None)
    if not isinstance(raw, BaseHTTPResponse):
        yield from resp.iter_lines()
        return

    def reads() -> Iterator[bytes]:
        while True:
            try:
                chunk = raw.read1(65536, decode_content=True)
            except ProtocolError as e:
                raise requests.exceptions.ChunkedEncodingError(e)
            except DecodeError as e:
                raise requests.exceptions.ContentDecodingError(e)
            except ReadTimeoutError as e:
                raise requests.exceptions.ConnectionError(e)
            except SSLError as e:
                raise requests.exceptions.SSLError(e)
            if not chunk:
                return
            yield chunk

    yield from split_lines(reads())
