"""`wisp --print` honours `--provider`, `--workspace` and `WISP_PERMISSION_MODE`.

`main()` parsed `--provider` and `--workspace` and then never gave them to `cmd_print`, which also
hard-coded `permission_mode="full"` with `auto_approve=True`. So `wisp --print 'x' --provider openrouter`
quietly ran on the default provider, and `WISP_PERMISSION_MODE=read_only wisp --print ...` quietly ran
with full permissions: a caller who asked for a read-only headless run got an unrestricted one.

The default is unchanged (`full`, as before, so existing headless users are unaffected). An explicit
mode is honoured; an invalid one is refused rather than treated as `full`. When any override is set the
local-server shortcut is skipped, because a server on :8000 would ignore it and answer with its own
provider, workspace and permissions.
"""

from __future__ import annotations

import sys

import pytest
import requests

import wisp.__main__ as cli


@pytest.fixture()
def headless(monkeypatch, tmp_path):
    """Capture what reaches run_headless, and record whether a local server was contacted."""
    seen: dict = {"posted": False}

    async def fake_run_headless(**kwargs):
        seen["kwargs"] = kwargs
        return {"ok": True, "content": "done", "tool_calls": [], "errors": [], "iterations": 1}

    def fake_post(*a, **k):
        seen["posted"] = True
        raise requests.ConnectionError("no server")

    import wisp.headless as headless_mod

    monkeypatch.setattr(headless_mod, "run_headless", fake_run_headless)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.delenv("WISP_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return seen


def _print(**kw):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_print(prompt="hi", quiet=True, **kw)
    return exc.value.code


def test_the_default_is_unchanged(headless):
    assert _print() == 0
    k = headless["kwargs"]
    assert k["permission_mode"] == "full" and k["provider"] is None and headless["posted"] is True


def test_provider_and_workspace_reach_the_run(headless, tmp_path):
    assert _print(provider="openrouter", workspace=str(tmp_path)) == 0
    k = headless["kwargs"]
    assert k["provider"] == "openrouter" and k["workspace"] == str(tmp_path)


@pytest.mark.parametrize("mode", ["read_only", "auto_edit", "ask_all", "full", "READ_ONLY"])
def test_an_explicit_permission_mode_is_honoured(headless, monkeypatch, mode):
    monkeypatch.setenv("WISP_PERMISSION_MODE", mode)
    assert _print() == 0
    assert headless["kwargs"]["permission_mode"] == mode.lower()


@pytest.mark.parametrize("bad", ["readonly", "yolo", "none", "0"])
def test_an_invalid_mode_is_refused_not_treated_as_full(headless, monkeypatch, capsys, bad):
    monkeypatch.setenv("WISP_PERMISSION_MODE", bad)
    assert _print() == 2
    assert "kwargs" not in headless, "nothing may run under a mode we could not understand"
    assert "WISP_PERMISSION_MODE" in capsys.readouterr().err


@pytest.mark.parametrize("override", [{"provider": "openrouter"}, {"workspace": "/w"}])
def test_an_override_skips_the_local_server(headless, override):
    _print(**override)
    assert headless["posted"] is False


def test_a_permission_mode_skips_the_local_server(headless, monkeypatch):
    monkeypatch.setenv("WISP_PERMISSION_MODE", "read_only")
    _print()
    assert headless["posted"] is False


def test_main_hands_the_flags_to_cmd_print(monkeypatch, tmp_path):
    got: dict = {}
    monkeypatch.setattr(cli, "cmd_print", lambda **kw: got.update(kw))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["wisp", "--print", "hello", "--provider", "openrouter",
                                      "--workspace", str(tmp_path), "--model", "m1"])
    cli.main()
    assert got["prompt"] == "hello" and got["model"] == "m1"
    assert got["provider"] == "openrouter" and got["workspace"] == str(tmp_path)
