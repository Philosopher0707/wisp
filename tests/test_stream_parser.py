"""Tests for stream_parser.py — NDJSON, SSE, auto-detect, split-line buffering."""

import json
import pytest
from wisp.stream_parser import EventStreamParser, parse_stream


class MockResponse:
    """Minimal mock of requests.Response.iter_content."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def iter_content(self, chunk_size=None):
        yield from self._chunks


class TestEventStreamParser:

    def test_ndjson_single_line(self):
        parser = EventStreamParser()
        data = json.dumps({"a": 1})
        result = list(parser.feed((data + "\n").encode()))
        assert result == [{"a": 1}]

    def test_ndjson_multiple_lines(self):
        parser = EventStreamParser()
        data = "\n".join([json.dumps({"i": i}) for i in range(3)]) + "\n"
        result = list(parser.feed(data.encode()))
        assert result == [{"i": 0}, {"i": 1}, {"i": 2}]

    def test_ndjson_split_across_chunks(self):
        parser = EventStreamParser()
        chunk1 = b'{"a": 1'
        chunk2 = b'}\n{"b": 2}\n'
        result = list(parser.feed(chunk1)) + list(parser.feed(chunk2))
        result += list(parser.finalize())
        assert result == [{"a": 1}, {"b": 2}]

    def test_sse_single_event(self):
        parser = EventStreamParser()
        chunk = b"data: {\"a\": 1}\n\n"
        result = list(parser.feed(chunk))
        assert result == [{"a": 1}]

    def test_sse_multi_data_lines(self):
        parser = EventStreamParser()
        chunk = b"data: {\"a\":\ndata: 1}\n\n"
        result = list(parser.feed(chunk))
        assert result == [{"a": 1}]

    def test_sse_event_type(self):
        parser = EventStreamParser()
        chunk = b"event: thinking\ndata: {\"b\": 2}\n\n"
        result = list(parser.feed(chunk))
        assert result == [{"b": 2}]

    def test_sse_comment_ignored(self):
        parser = EventStreamParser()
        chunk = b": comment\ndata: {\"c\": 3}\n\n"
        result = list(parser.feed(chunk))
        assert result == [{"c": 3}]

    def test_sse_unknown_field_ignored(self):
        parser = EventStreamParser()
        chunk = b"retry: 1000\ndata: {\"d\": 4}\n\n"
        result = list(parser.feed(chunk))
        assert result == [{"d": 4}]

    def test_auto_detect_ndjson_first(self):
        parser = EventStreamParser()
        chunk = b'{"msg": "hello"}\n'
        result = list(parser.feed(chunk))
        assert result == [{"msg": "hello"}]

    def test_auto_detect_sse_first(self):
        parser = EventStreamParser()
        chunk = b"data: {\"msg\": \"hello\"}\n\n"
        result = list(parser.feed(chunk))
        assert result == [{"msg": "hello"}]

    def test_empty_chunks_skipped(self):
        parser = EventStreamParser()
        assert list(parser.feed(b"")) == []

    def test_finalize_ndjson_unterminated(self):
        parser = EventStreamParser()
        list(parser.feed(b'{"a": 1}\n{"b": 2}'))
        result = list(parser.finalize())
        assert result == [{"b": 2}]

    def test_finalize_sse_unterminated(self):
        parser = EventStreamParser()
        list(parser.feed(b"data: {\"a\": 1}\n"))
        result = list(parser.finalize())
        assert result == [{"a": 1}]

    def test_ndjson_parse_error_skipped(self):
        parser = EventStreamParser()
        chunk = b"not-json\n{\"valid\": true}\n"
        result = list(parser.feed(chunk))
        assert result == [{"valid": True}]

    def test_sse_parse_error_skipped(self):
        parser = EventStreamParser()
        chunk = b"data: not-json\n\n"
        result = list(parser.feed(chunk))
        assert result == []


class TestParseStreamFunction:

    def test_parse_stream_happy_path(self):
        resp = MockResponse([b'{"x": 1}\n', b'{"x": 2}\n'])
        result = list(parse_stream(resp))
        assert result == [{"x": 1}, {"x": 2}]

    def test_parse_stream_unexpected_encoding(self):
        resp = MockResponse([b'\xff\xfe\x00{"x": 1}\n'])
        result = list(parse_stream(resp))
        assert result == []
