"""Event stream parser — handles NDJSON and SSE (text/event-stream) from LLM APIs.

Ollama currently returns NDJSON (one JSON object per line).
When requested with Accept: text/event-stream, Ollama may return SSE format.
OpenAI-compatible APIs always use SSE format.

This parser auto-detects the format and yields parsed JSON dicts for both.
"""

import json
import logging
from typing import Iterator

logger = logging.getLogger(__name__)


class EventStreamError(Exception):
    """Raised when event stream parsing fails."""
    pass


class EventStreamParser:
    """Parse a streaming HTTP response body as NDJSON or SSE.

    Auto-detects format on the first chunk:
    - If the line starts with 'data:' → SSE mode
    - Otherwise → NDJSON mode

    Yields parsed JSON dicts one at a time.
    """

    def __init__(self):
        self._mode: str | None = None  # 'sse' or 'ndjson'
        self._sse_buffer: list[str] = []  # Accumulate multi-line data fields
        self._sse_event_type: str = "message"  # Current SSE event type
        self._bytes_buffer: bytes = b""  # For split-line handling

    def _detect_mode(self, line: str) -> str:
        """Detect stream format from a line (called once on first non-empty line)."""
        stripped = line.lstrip()
        if stripped.startswith("data:"):
            return "sse"
        # NDJSON lines start with '{' or '[' or a digit (for JSON numbers)
        return "ndjson"

    def _parse_ndjson(self, raw_bytes: bytes) -> Iterator[dict]:
        """NDJSON mode: each line is a self-contained JSON object."""
        # Append new bytes to our partial-line buffer
        self._bytes_buffer += raw_bytes

        # Split on newlines; the last element may be an incomplete line
        *lines, self._bytes_buffer = self._bytes_buffer.split(b"\n")

        for raw_line in lines:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("NDJSON parse error on line: %.80s — %s", line, e)
                continue

    def _parse_sse(self, raw_bytes: bytes) -> Iterator[dict]:
        """SSE mode: lines are event fields; empty lines separate events.

        Format:
            data: {"key": "value"}
            data: continuation
            \n
        Comments starting with ':' are ignored.
        An event may have multiple 'data:' lines → joined with newlines.
        """
        # Append new bytes to our partial-line buffer
        self._bytes_buffer += raw_bytes

        # Split on newlines
        *lines, self._bytes_buffer = self._bytes_buffer.split(b"\n")

        for raw_line in lines:
            line = raw_line.decode("utf-8", errors="replace")
            # SSE spec: empty line terminates the current event
            if line == "" or line == "\r":
                if self._sse_buffer:
                    data = "\n".join(self._sse_buffer)
                    self._sse_buffer = []
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError as e:
                        logger.warning("SSE JSON parse error: %.80s — %s", data, e)
                        continue
                continue

            # Comments (lines starting with ':')
            if line.startswith(":"):
                continue

            # Event type
            if line.startswith("event:"):
                self._sse_event_type = line[len("event:"):].strip()
                continue

            # Data field
            if line.startswith("data:"):
                self._sse_buffer.append(line[len("data:"):].strip())
                continue

            # Unknown field — log and ignore
            logger.debug("Unknown SSE field: %.80s", line)

    def feed(self, raw_bytes: bytes) -> Iterator[dict]:
        """Feed raw HTTP response bytes and yield parsed JSON events.

        Called repeatedly with chunks from the HTTP stream.
        """
        # On first call, detect mode from the first non-empty line
        if self._mode is None:
            # Try to find the first non-empty line in this chunk
            decoded = raw_bytes.decode("utf-8", errors="replace")
            for line in decoded.split("\n"):
                stripped = line.strip()
                if stripped:
                    self._mode = self._detect_mode(stripped)
                    logger.debug("Detected stream mode: %s", self._mode)
                    break
            # If we still don't know the mode (empty first chunk), buffer it
            if self._mode is None:
                self._bytes_buffer += raw_bytes
                return
                yield  # Make this a generator
            # Reset buffer since we consumed it for detection
            self._bytes_buffer = b""

        if self._mode == "sse":
            yield from self._parse_sse(raw_bytes)
        else:
            yield from self._parse_ndjson(raw_bytes)

    def finalize(self) -> Iterator[dict]:
        """Process any remaining buffered bytes after the stream ends.

        Call this when the HTTP stream is exhausted.
        """
        # For NDJSON: any remaining bytes_buffer is an unterminated last line
        if self._mode == "ndjson" and self._bytes_buffer:
            line = self._bytes_buffer.decode("utf-8", errors="replace").strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("Final NDJSON parse error on line: %.80s — %s", line, e)
            self._bytes_buffer = b""

        # For SSE: any remaining data in the buffer
        elif self._mode == "sse" and self._bytes_buffer:
            decoded = self._bytes_buffer.decode("utf-8", errors="replace")
            for raw_line in decoded.split("\n"):
                line = raw_line.strip()
                if line.startswith("data:"):
                    self._sse_buffer.append(line[len("data:"):].strip())

            if self._sse_buffer:
                data = "\n".join(self._sse_buffer)
                self._sse_buffer = []
                try:
                    yield json.loads(data)
                except json.JSONDecodeError as e:
                    logger.warning("Final SSE JSON parse error: %.80s — %s", data, e)
            self._bytes_buffer = b""


def parse_stream(response) -> Iterator[dict]:
    """Convenience: parse an HTTP stream response (iter_content) into JSON events.

    Args:
        response: A requests Response object with stream=True

    Yields:
        Parsed JSON dicts from each event
    """
    parser = EventStreamParser()
    for chunk in response.iter_content(chunk_size=None):
        if chunk:
            try:
                yield from parser.feed(chunk)
            except UnicodeDecodeError as e:
                logger.warning("Decode error in stream chunk: %s", e)
                continue
    # Flush remaining buffered data
    yield from parser.finalize()
