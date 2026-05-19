"""TDD for FileTransport.

Tests that FileTransport implements Transport ABC and correctly
writes events to a JSON Lines file.
"""

import json
import os
import pytest
import tempfile
from pathlib import Path

from wisp.transport.file import FileTransport
from wisp.transport.base import Transport


class TestFileTransport:
    """FileTransport writes events to JSON Lines file."""

    def test_implements_transport(self):
        assert issubclass(FileTransport, Transport)

    def test_start_creates_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
            path = f.name
        try:
            transport = FileTransport(path, mode="w")
            transport.start()
            assert Path(path).exists()
            transport.stop()
            with open(path) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            assert lines[0]["type"] == "file_transport_start"
        finally:
            os.unlink(path)

    def test_stop_writes_footer(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
            path = f.name
        try:
            transport = FileTransport(path, mode="w")
            transport.start()
            transport.stop()
            with open(path) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            assert lines[-1]["type"] == "file_transport_stop"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_send_writes_event(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
            path = f.name
        try:
            transport = FileTransport(path, mode="w")
            transport.start()
            await transport.send({"type": "content", "text": "hello"})
            transport.stop()
            with open(path) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            event_lines = [l for l in lines if l["type"] not in ("file_transport_start", "file_transport_stop")]
            assert len(event_lines) == 1
            assert event_lines[0]["type"] == "content"
            assert event_lines[0]["text"] == "hello"
            assert "_logged_at" in event_lines[0]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_recv_returns_none(self):
        transport = FileTransport("/tmp/test.jsonl")
        result = await transport.recv()
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_returns_true(self):
        transport = FileTransport("/tmp/test.jsonl")
        result = await transport.approve({"name": "run_bash"})
        assert result is True

    @pytest.mark.asyncio
    async def test_read_events_excludes_control(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
            path = f.name
        try:
            transport = FileTransport(path, mode="w")
            transport.start()
            await transport.send({"type": "content", "text": "hello"})
            await transport.send({"type": "done"})
            transport.stop()
            events = transport.read_events()
            assert len(events) == 2
            assert all(e["type"] not in ("file_transport_start", "file_transport_stop") for e in events)
        finally:
            os.unlink(path)

    def test_read_events_missing_file(self):
        transport = FileTransport("/tmp/nonexistent_file_12345.jsonl")
        events = transport.read_events()
        assert events == []
