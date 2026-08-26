"""SSRF guard + spinner single-line contract pins.

Root causes from a live research session:
- arxiv.org (and every IPv4-only site) resolves via NAT64 on some
  networks; CPython marks the well-known translation prefix
  64:ff9b::/96 'reserved', so the guard blocked legitimate public sites
  and the model was pushed into curl workarounds that bypass SSRF
  entirely.
- Multi-line run_bash commands leaked newlines into the spinner's
  single-line \\r redraw, so every frame painted fresh rows and the
  command appeared to repeat.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from wisp.tools.web import ToolError, _assert_public_url


def _resolve(*addrs):
    return [(socket.AF_INET6, None, None, "", (a, 0, 0, 0)) for a in addrs]


def test_nat64_embedded_public_ipv4_is_dialable():
    """arxiv-style answer: 64:ff9b::/96 wraps public Fastly IPv4."""
    with patch("socket.getaddrinfo",
               return_value=_resolve("64:ff9b::9765:32a")):
        pinned = _assert_public_url("https://arxiv.org/abs/2206.13351")
    assert pinned == "64:ff9b::9765:32a"


def test_rfc6052_translation_prefix_judged_by_embedded_v4():
    """64:ff9b:1::/48 embeds the IPv4 in the low 32 bits as well."""
    with patch("socket.getaddrinfo",
               return_value=_resolve("64:ff9b:1::0a01:0203")):
        # 10.1.2.3 embedded → private → must block
        with pytest.raises(ToolError, match="no usable public"):
            _assert_public_url("https://intranet.example.com")


def test_all_internal_answers_still_blocked():
    """The guard must not regress into allowing true internal targets."""
    with patch("socket.getaddrinfo", return_value=_resolve(
            "64:ff9b::0a00:0001")):  # 10.0.0.1 embedded
        with pytest.raises(ToolError, match="no usable public"):
            _assert_public_url("http://10.0.0.1/")
    with patch("socket.getaddrinfo", return_value=_resolve("169.254.169.254")):
        with pytest.raises(ToolError, match="no usable public"):
            _assert_public_url("http://metadata.google.internal/")


def test_mixed_answers_use_the_public_one():
    """One unusable record must not kill an otherwise-public host."""
    with patch("socket.getaddrinfo", return_value=_resolve(
            "10.0.0.5", "64:ff9b::9765:32a")):
        # First DIALABLE answer wins (the private v4 is skipped, not fatal).
        assert _assert_public_url("https://example.com") == "64:ff9b::9765:32a"


# ── Spinner: multi-line labels stay single-line ─────────────────────────

def test_truncate_collapses_newlines():
    """Heredoc commands must not break the \\r redraw contract."""
    from wisp.transport.spinner import truncate_spinner_label

    label = "run_bash curl -sL url -o /tmp/x\npython3 - <<EOF\nimport re\nEOF"
    out = truncate_spinner_label(label, width=60)
    assert "\n" not in out


def test_args_preview_never_returns_newlines():
    """The CLI label builder collapses command previews."""
    from wisp.transport.cli import _args_preview

    out = _args_preview({"command": "curl one\ncurl two\npython3 <<EOF\nx\nEOF"})
    assert "\n" not in out
    assert len(out) <= 60
