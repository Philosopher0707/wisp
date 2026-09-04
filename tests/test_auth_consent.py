# tests/test_auth_consent.py
from wisp.auth.consent import (
    check_consent,
    origin_hash,
    quarantined,
    record_consent,
)


def test_consent_round_trip(tmp_path):
    record_consent(tmp_path, server_id="mcp:git",
                   origin="uvx mcp-server-git", scopes=("read",))
    rec = check_consent(tmp_path, server_id="mcp:git",
                        origin="uvx mcp-server-git", scopes=("read",))
    assert rec is not None and rec.server_id == "mcp:git"


def test_origin_change_invalidates(tmp_path):
    record_consent(tmp_path, server_id="s", origin="cmd-a", scopes=())
    assert check_consent(tmp_path, server_id="s", origin="cmd-b",
                         scopes=()) is None


def test_scope_widening_requires_reconsent(tmp_path):
    record_consent(tmp_path, server_id="s", origin="o", scopes=("read",))
    assert check_consent(tmp_path, server_id="s", origin="o",
                         scopes=("read", "write")) is None
    # narrowing is fine
    assert check_consent(tmp_path, server_id="s", origin="o",
                         scopes=("read",)) is not None


def test_quarantined_unsigned(tmp_path):
    assert quarantined(tmp_path, server_id="new-server", signed=False) is True
    record_consent(tmp_path, server_id="new-server", origin="o",
                   scopes=(), signed=True)
    assert quarantined(tmp_path, server_id="new-server", signed=True) is False


def test_origin_hash_stable():
    assert origin_hash("a") == origin_hash("a")
    assert origin_hash("a") != origin_hash("b")
