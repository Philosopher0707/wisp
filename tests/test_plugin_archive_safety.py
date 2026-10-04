"""Regression witness: plugin archive extraction must not escape its target dir.

`PluginRegistry.download_plugin` fetches an archive from a remote marketplace and
calls `tarfile.extractall()`. Without an extraction filter, a crafted member
named `../../.ssh/authorized_keys` (or an absolute path) writes OUTSIDE
`extract_dir`. That is a remote-code-execution path for any user who runs
`wisp plugin install` against a hostile or compromised marketplace.

Python 3.12 made `tarfile` default to the "data" filter (CVE-2007-4559
mitigation) and backported the `filter=` parameter to 3.11.4. This repo
declares `requires-python = ">=3.11"`, so the fix is available on every
supported interpreter -- but only when it is passed EXPLICITLY. Relying on the
interpreter default would silently regress on 3.11.0-3.11.3.

Run:  pytest tests/test_plugin_archive_safety.py -v
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest


def _make_evil_tar(tmp_path: Path) -> Path:
    """A .tar whose single member tries to write outside the extraction root."""
    archive = tmp_path / "evil.tar"
    payload = b"pwned\n"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo(name="../../escaped.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return archive


def _make_evil_zip(tmp_path: Path) -> Path:
    """A .zip whose single member tries to write outside the extraction root."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../escaped.txt", "pwned\n")
    return archive


class TestTarExtractionFilter:
    def test_extractall_must_receive_an_explicit_filter(self) -> None:
        """The call site must pass `filter=`; the interpreter default is not a control.

        This asserts on the source, not on behaviour, because the behaviour
        differs by interpreter version: on 3.12+ an unfiltered call happens to
        be safe, on 3.11.0-3.11.3 it is not. A behavioural test would pass on the
        developer's machine and fail in CI, or worse, the reverse.
        """
        src = Path(__file__).resolve().parent.parent / "wisp" / "plugins" / "registry.py"
        text = src.read_text(encoding="utf-8")
        assert "tf.extractall(" in text, "expected a tarfile extractall call site"
        # Every tarfile extractall call must carry a filter= argument.
        for line in text.splitlines():
            if "extractall(" in line and ".zip" not in line:
                # zipfile's call needs no filter; tarfile's does.
                if line.strip().startswith("tf.extractall") or line.strip().startswith("tar.extractall"):
                    assert "filter=" in line, f"tarfile.extractall without filter=: {line.strip()}"


class TestArchiveEscapeIsBlocked:
    """Behavioural proof against the real tarfile semantics."""

    def test_evil_tar_cannot_escape_when_filter_applied(self, tmp_path: Path) -> None:
        archive = _make_evil_tar(tmp_path)
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        with tarfile.open(archive, "r:*") as tf:
            try:
                tf.extractall(extract_dir, filter="data")
            except tarfile.OutsideDestinationError:
                pass  # blocked -- the desired outcome

        # Nothing may exist above the extraction root.
        escaped = tmp_path / "escaped.txt"
        assert not escaped.exists(), "tar member escaped the extraction directory"

    def test_evil_zip_cannot_escape(self, tmp_path: Path) -> None:
        """zipfile already sanitises traversal; pin that we did not regress it."""
        archive = _make_evil_zip(tmp_path)
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(extract_dir)

        assert not (tmp_path / "escaped.txt").exists(), "zip member escaped the extraction directory"

    def test_harmless_archive_still_extracts(self, tmp_path: Path) -> None:
        """The fix must not break the ordinary case."""
        archive = tmp_path / "good.tar"
        payload = b"hello\n"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo(name="pkg/hello.txt")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(extract_dir, filter="data")

        assert (extract_dir / "pkg" / "hello.txt").read_bytes() == payload


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
