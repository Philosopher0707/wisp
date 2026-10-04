"""The suite must never write to the developer's real workspace-trust file.

`WorkspaceTrustManager.TRUST_FILE` is a class attribute computed from
`Path.home()` at import time, so patching HOME later does not redirect it, and
any test that calls `trust_workspace(tmp_path)` without `trust_file=` appended
its temp directory to `~/.config/wisp/trusted_workspaces.json` on every run
(tests/test_mcp.py alone added three per run; the file reached ~1,500 entries).
The conftest points the attribute at a throwaway file for the whole session.
"""

from __future__ import annotations

import os
import pwd
import tempfile
from pathlib import Path

from wisp.trust import WorkspaceTrustManager


def _real_trust_file() -> Path:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ".config" / "wisp" / "trusted_workspaces.json"


def test_default_trust_file_is_a_throwaway():
    tf = WorkspaceTrustManager.TRUST_FILE.resolve()
    assert tf != _real_trust_file().resolve()
    assert Path(tempfile.gettempdir()).resolve() in tf.parents


def test_trusting_without_an_explicit_file_leaves_the_real_one_alone(tmp_path):
    real = _real_trust_file()
    before = real.read_bytes() if real.exists() else None

    WorkspaceTrustManager.trust_workspace(tmp_path)

    assert WorkspaceTrustManager.is_workspace_trusted(tmp_path, allow_auto=False)
    after = real.read_bytes() if real.exists() else None
    assert after == before
