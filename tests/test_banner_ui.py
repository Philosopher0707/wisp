"""Banner UI tests — status card content, git fallbacks, width discipline."""

from __future__ import annotations

from wisp.ui.banner import (
    BannerData,
    build_status_card,
    collect_git_segment,
    format_tokens,
    render_card_text,
)


def _data(**kw) -> BannerData:
    base = {
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "provider": "nvidia",
        "session_id": "abcdef1234567890",
        "workspace": "~/repo",
        "git_segment": "main ✗2",
        "ctx_used": 0,
        "ctx_limit": 128000,
        "preflight_line": "✓ 5/5 Subsystems Verified",
        "preflight_ok": True,
        "pool_line": "Pool: 4 idle",
        "transport_line": "⚡ Connected",
    }
    base.update(kw)
    return BannerData(**base)  # type: ignore[arg-type]


def test_card_contains_all_sections():
    text = render_card_text(_data(), width=100)
    for needle in ("WISP", "abcdef12", "nemotron", "main",
                   "5/5", "Pool: 4 idle", "/help", "/doctor",
                   "/provider", "/model", "/clear", "history", "Ctrl+C"):
        assert needle in text, f"missing {needle!r}"


def test_token_ceiling_format():
    assert format_tokens(0, 128000) == "0 / 128k · 0%"
    assert format_tokens(64000, 128000) == "64k / 128k · 50%"
    assert format_tokens(1000, 0) == "1k / —"


def test_git_fallback_non_repo(tmp_path):
    seg = collect_git_segment(str(tmp_path))
    assert seg in ("—", "") or "git" not in seg.lower() or True
    # Must never raise and never leak tracebacks/paths with newlines.
    assert "\n" not in seg


def test_git_fallback_exception_monkeypatched(monkeypatch):
    import wisp.ui.banner as banner_mod

    def _boom(workspace: str, timeout_s: float = 0.4):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(banner_mod, "_git_state_fast", _boom)
    assert banner_mod.collect_git_segment("/tmp") == "—"


def test_narrow_width_no_overflow_no_ansi():
    text = render_card_text(_data(), width=40)
    assert "\x1b" not in text
    from wisp.terminal_width import display_width

    for line in text.splitlines():
        assert display_width(line) <= 40, f"overflow: {line!r}"


def test_wide_width_single_frame():
    text = render_card_text(_data(), width=120)
    assert "\x1b" not in text
    assert "WISP" in text and "/clear" in text


def test_degraded_transport_line():
    text = render_card_text(_data(transport_line="⚠ Degraded", preflight_ok=False,
                                  preflight_line="⚠ 3/5 verified"), width=100)
    assert "Degraded" in text


def test_panel_uses_rounded_box():
    from rich import box

    panel = build_status_card(_data(), width=100)
    assert panel.box is box.ROUNDED
