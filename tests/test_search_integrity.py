"""PHASE 13-I-1 — search integrity (13-H P1 MUST FIX).

Proves an unusable index can never masquerade as a genuine negative.
Deterministic: embeddings stubbed (unit vectors / zeros); no network.
"""
from __future__ import annotations

import pytest

from wisp.semantic_index import SemanticIndex
from wisp.tools.search import tool_search_codebase


def _ws_with_code(tmp_path, name="code.py", body="def providers():\n    pass\n"):
    (tmp_path / name).write_text(body)
    return tmp_path


def _stub_embed(vec):
    return lambda self, texts: [list(vec) for _ in texts]


def _build(idx, monkeypatch, vec=(1.0,) * 8):
    monkeypatch.setattr(SemanticIndex, "_embed", _stub_embed(vec))
    n = idx.index_file(next(idx.workspace.glob("*.py")))
    assert n > 0
    return idx


BARE_NEGATIVE = "No semantically relevant code found"


# 1. populated index + real negative (unmatchable chunks, healthy query)
def test_valid_negative_over_ready_index(tmp_path, monkeypatch):
    _ws_with_code(tmp_path)
    monkeypatch.setattr(SemanticIndex, "_embed", _stub_embed((0.0,) * 8))
    idx = SemanticIndex(str(tmp_path))
    assert idx.index_file(next(idx.workspace.glob("*.py"))) > 0
    idx.close()
    state, _ = SemanticIndex(str(tmp_path)).index_state()
    assert state == SemanticIndex.STATE_READY
    monkeypatch.setattr(SemanticIndex, "_embed", _stub_embed((1.0,) * 8))
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert out.startswith(BARE_NEGATIVE)  # mechanically valid negative


# 2. populated index + positive
def test_positive_returns_hits(tmp_path, monkeypatch):
    _ws_with_code(tmp_path)
    idx = SemanticIndex(str(tmp_path))
    _build(idx, monkeypatch, vec=(1.0,) * 8)
    monkeypatch.setattr(SemanticIndex, "_embed", _stub_embed((1.0,) * 8))
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "Semantic search results" in out and "code.py" in out


# 3. missing index
def test_missing_index_unavailable(tmp_path):
    _ws_with_code(tmp_path)
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "unavailable" in out and "no index found" in out
    assert not out.startswith(BARE_NEGATIVE)


# 4. empty index (db exists, zero chunks)
def test_empty_index_unavailable(tmp_path):
    _ws_with_code(tmp_path)
    idx = SemanticIndex(str(tmp_path))
    idx.conn  # create schema, index nothing
    idx.close()
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "unavailable" in out and "0 chunks" in out
    assert not out.startswith(BARE_NEGATIVE)


# 4b. empty workspace is a genuine negative
def test_no_indexable_files_genuine_negative(tmp_path):
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert out.startswith(BARE_NEGATIVE) and "no indexable files" in out


# 5. corrupt index
def test_corrupt_index_unavailable(tmp_path):
    ws = _ws_with_code(tmp_path)
    (ws / ".wisp").mkdir(exist_ok=True)
    (ws / ".wisp" / "semantic_index.db").write_bytes(b"\x00garbage")
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "unavailable" in out and "corrupt" in out.lower()
    assert not out.startswith(BARE_NEGATIVE)


# 6. embedding backend down at query time
def test_embedding_failure_unavailable(tmp_path, monkeypatch):
    import requests
    _ws_with_code(tmp_path)
    real_embed = SemanticIndex._embed
    monkeypatch.setattr(SemanticIndex, "_embed", _stub_embed((1.0,) * 8))
    idx = SemanticIndex(str(tmp_path))
    assert idx.index_file(next(idx.workspace.glob("*.py"))) > 0
    idx.close()
    monkeypatch.setattr(SemanticIndex, "_embed", real_embed)

    def _boom(*a, **k):
        raise ConnectionError("ollama down")
    monkeypatch.setattr(requests, "post", _boom)
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "unavailable" in out and "embedding" in out.lower()
    assert not out.startswith(BARE_NEGATIVE)


# 7. index-build failure leaves an honest empty state
def test_build_failure_chain(tmp_path, monkeypatch):
    _ws_with_code(tmp_path)
    idx = SemanticIndex(str(tmp_path))

    def _fail(self, fp):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(SemanticIndex, "index_file", _fail)
    with pytest.raises(RuntimeError):
        idx.index_file(tmp_path / "code.py")
    state, _ = SemanticIndex(str(tmp_path)).index_state()
    assert state in (SemanticIndex.STATE_MISSING, SemanticIndex.STATE_EMPTY)
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "unavailable" in out


# 8. numpy missing stays a distinct infra error, never a negative
def test_dependency_missing_is_error_not_negative(tmp_path, monkeypatch):
    import sys
    _ws_with_code(tmp_path)
    idx = SemanticIndex(str(tmp_path))
    _build(idx, monkeypatch, vec=(1.0,) * 8)
    idx.close()
    monkeypatch.setitem(sys.modules, "numpy", None)
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    # surfaces as an infra-unavailable message, never a bare negative
    assert "module not available" in out and not out.startswith(BARE_NEGATIVE)


# 9. stale index exposes itself instead of answering
def test_stale_index_exposes_itself(tmp_path, monkeypatch):
    _ws_with_code(tmp_path)
    idx = SemanticIndex(str(tmp_path))
    _build(idx, monkeypatch, vec=(1.0,) * 8)
    idx.close()
    (tmp_path / "newmod.py").write_text("def fresh():\n    pass\n")
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "stale" in out.lower() and "unavailable" in out
    assert not out.startswith(BARE_NEGATIVE)


# 9b. staleness details name the gap
def test_stale_detail_counts(tmp_path, monkeypatch):
    _ws_with_code(tmp_path)
    idx = SemanticIndex(str(tmp_path))
    _build(idx, monkeypatch, vec=(1.0,) * 8)
    state, detail = idx.index_state()
    assert state == SemanticIndex.STATE_READY
    (tmp_path / "newmod.py").write_text("x = 1\n")
    state2, detail2 = SemanticIndex(str(tmp_path)).index_state()
    assert state2 == SemanticIndex.STATE_STALE and "unindexed" in detail2


# 10. fallback is named, never silent
def test_fallback_named_in_unavailable(tmp_path):
    _ws_with_code(tmp_path)
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "search_symbols" in out and "NOT a finding" in out
