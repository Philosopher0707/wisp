"""Tests for Q2: TOCTOU-safe file I/O helpers in wisp/tools/_utils.py.

When a file is replaced by a symlink between path resolution and
read/write (classic Time-of-Check/Time-of-Use), the *_safe_* helpers
use O_NOFOLLOW to detect the swap and raise ToolError instead of
silently following the symlink.
"""

import os
import sys
from pathlib import Path

import pytest

from wisp.tools.errors import ToolError
from wisp.tools._utils import (
    _safe_read_text,
    _safe_write_text,
    _safe_open_read,
    _resolve_path,
)


class TestToctouSafeReadWrite:
    """Q2: fd-based O_NOFOLLOW prevents symlink swap attacks."""

    # ── 1. Tracer bullet: basic read/write roundtrip ──────────────────────

    def test_safe_read_write_roundtrip(self, tmp_path):
        """Normal read and write through fd paths work exactly like pathlib."""
        ws = str(tmp_path)
        target = tmp_path / "hello.txt"
        _safe_write_text("hello.txt", ws, "Hello, World!", encoding="utf-8")

        assert target.exists()
        assert target.read_text() == "Hello, World!"

        content = _safe_read_text("hello.txt", ws)
        assert content == "Hello, World!"

    # ── 2. Symlink-swap TOCTOU detection ────────────────────────────────

    @pytest.mark.skipif(sys.platform == "win32", reason="O_NOFOLLOW not reliable on Windows")
    def test_safe_read_blocks_symlink_swap(self, tmp_path):
        """After _resolve_path, if target is replaced by symlink, read raises."""
        ws = tmp_path
        real_file = ws / "data.txt"
        real_file.write_text("original")

        # Attacker replaces the file with a symlink AFTER resolution window
        real_file.unlink()
        real_file.symlink_to("/etc/passwd")

        # safe_read should detect the swap (now a symlink → O_NOFOLLOW raises ELOOP)
        with pytest.raises(ToolError) as exc_info:
            _safe_read_text("data.txt", str(ws))

        assert "TOCTOU" in str(exc_info.value) or "symlink" in str(exc_info.value).lower()

    @pytest.mark.skipif(sys.platform == "win32", reason="O_NOFOLLOW not reliable on Windows")
    def test_safe_write_blocks_symlink_swap(self, tmp_path):
        """After _resolve_path, if target is replaced by symlink, write raises."""
        ws = tmp_path
        real_file = ws / "output.txt"
        real_file.write_text("old")

        # Attacker swaps file for symlink
        real_file.unlink()
        real_file.symlink_to("/etc/passwd")

        with pytest.raises(ToolError) as exc_info:
            _safe_write_text("output.txt", str(ws), "new content")

        assert "TOCTOU" in str(exc_info.value) or "symlink" in str(exc_info.value).lower()

    # ── 3. Concurrent / directory symlink ───────────────────────────────

    def test_safe_read_rejects_directory(self, tmp_path):
        """Reading from a directory via fd raises IsADirectoryError."""
        ws = str(tmp_path)
        (Path(ws) / ".wisp").mkdir(parents=True, exist_ok=True)

        fd, resolved = _safe_open_read(".wisp", ws)
        import os as _os
        try:
            data = _os.read(fd, 65536)
            pytest.fail(f"Expected IsADirectoryError, got: {data}")
        except IsADirectoryError:
            pass  # expected
        finally:
            _os.close(fd)

    # ── 4. Binary content roundtrip ─────────────────────────────────────

    def test_binary_content_roundtrip(self, tmp_path):
        """Non-text bytes survive the fd-based write/read path unchanged."""
        ws = str(tmp_path)
        raw = b"\x00\xff\xfe\x01binary\x80"
        from wisp.tools._utils import _safe_write_bytes, _safe_read_bytes

        _safe_write_bytes("bin.dat", ws, raw)
        result, _ = _safe_read_bytes("bin.dat", ws)
        assert result == raw
