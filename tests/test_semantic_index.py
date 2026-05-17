"""Regression tests for SemanticIndex.search() — numpy vectorised cosine similarity.

Verifies that:
1. search() computes cosine similarity correctly (not keyword matching)
2. Top-k ordering is correct by score descending
3. Zero-vector queries return empty list (fail-safe)
4. Performance is reasonable for moderate dataset sizes
"""

import json
import struct

import numpy as np
import pytest

from wisp.semantic_index import SemanticIndex, SearchResult


def _make_embedding(text_seed: str, dim: int = 768) -> list[float]:
    """Deterministic pseudo-random embedding from a seed string."""
    rng = np.random.default_rng(hash(text_seed) % (2**32))
    vec = rng.standard_normal(dim).astype(np.float64)
    vec = vec / (np.linalg.norm(vec) + 1e-12)  # Normalise
    return vec.tolist()


def _pack(emb: list[float]) -> bytes:
    return struct.pack(f"<{len(emb)}d", *emb)


def _insert_chunks(index: SemanticIndex, chunks):
    """Insert chunks with embeddings directly into the DB."""
    conn = index.conn
    for i, (path, start, end, content, emb) in enumerate(chunks):
        c = conn.execute(
            "INSERT INTO chunks (file_path, start_line, end_line, content, symbol_name, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (path, start, end, content, "", f"hash{i}"),
        )
        chunk_id = c.lastrowid
        conn.execute(
            "INSERT INTO embeddings (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, _pack(emb)),
        )
    conn.commit()


@pytest.fixture
def tmp_index(tmp_path):
    idx = SemanticIndex(str(tmp_path))
    yield idx
    idx.close()


class TestSearchCorrectness:
    """Verify cosine similarity ordering is correct, not keyword matching."""

    def test_identical_query_returns_max_score(self, tmp_index):
        """A chunk that embeds to the exact same vector as the query must score ~1.0."""
        # Create three chunks with known embeddings
        chunks = [
            ("a.py", 1, 5, "def alpha():", _make_embedding("alpha")),
            ("b.py", 6, 10, "def beta():", _make_embedding("beta")),
            ("c.py", 11, 15, "def gamma():", _make_embedding("gamma")),
        ]
        _insert_chunks(tmp_index, chunks)

        # Query with exactly the "alpha" vector
        alpha_vec = _make_embedding("alpha")
        tmp_index._embed = lambda texts: [alpha_vec]  # type: ignore[method-assign]

        results = tmp_index.search("anything", top_k=3)
        assert len(results) == 3
        # First result must be "a.py" (identical embedding)
        assert results[0].file_path == "a.py"
        # Score should be ~1.0 (cosine of identical vectors)
        assert results[0].score > 0.999
        # Scores should descend
        assert results[0].score >= results[1].score >= results[2].score

    def test_top_k_limits_count(self, tmp_index):
        """top_k=2 should return at most 2 results."""
        chunks = []
        for i in range(10):
            chunks.append((f"f{i}.py", 1, 5, f"content{i}", _make_embedding(f"seed{i}")))
        _insert_chunks(tmp_index, chunks)

        tmp_index._embed = lambda texts: [_make_embedding("seed5")]  # type: ignore[method-assign]
        results = tmp_index.search("any", top_k=2)
        assert len(results) == 2

    def test_no_embeddings_returns_empty(self, tmp_index):
        """On an empty database, search should return empty list."""
        tmp_index._embed = lambda texts: [_make_embedding("x")]  # type: ignore[method-assign]
        results = tmp_index.search("hello", top_k=5)
        assert results == []

    def test_zero_vector_query_returns_empty(self, tmp_index):
        """A zero-vector query (embed failure fallback) should return empty list."""
        _insert_chunks(tmp_index, [("a.py", 1, 5, "x", _make_embedding("s"))])
        tmp_index._embed = lambda texts: [[0.0] * 768]  # zero vector fallback
        results = tmp_index.search("anything", top_k=5)
        assert results == []

    def test_cosine_not_keyword_matching(self, tmp_index):
        """A chunk whose content contains the query words but whose embedding is
        orthogonal should NOT rank higher than a chunk with matching embedding."""
        # Orthogonal vectors (dot product ≈ 0)
        emb_a = [1.0] + [0.0] * 767  # aligned with first axis
        emb_b = [0.0] * 768
        emb_b[1] = 1.0               # aligned with second axis

        # Give chunk_a a keyword that matches the query text but wrong embedding
        chunks = [
            # "hello" appears in content but embedding is orthogonal to query
            ("keyword_match.py", 1, 5, "hello world", emb_a),
            # "other" doesn't appear but embedding matches query
            ("semantic_match.py", 6, 10, "other stuff", emb_b),
        ]
        _insert_chunks(tmp_index, chunks)

        # Query aligned with emb_b (second axis)
        query = [0.0] * 768
        query[1] = 1.0
        tmp_index._embed = lambda texts: [query]  # type: ignore[method-assign]

        results = tmp_index.search("hello", top_k=2)
        # semantic_match.py should win because embedding aligns, not
        # because of keyword "hello" in the other chunk
        assert results[0].file_path == "semantic_match.py"
        assert results[0].score > 0.99  # almost perfect alignment


class TestSearchPerformance:
    """Verify O(n) numpy is fast enough to not require sqlite-vec."""

    def test_thousand_chunks_fast(self, tmp_index):
        """Search through 1,000 chunks should be well under 1 second."""
        dim = 768
        n = 1000
        chunks = []
        for i in range(n):
            emb = _make_embedding(f"chunk{i}", dim=dim)
            chunks.append((f"f{i}.py", 1, 5, f"c{i}", emb))
        _insert_chunks(tmp_index, chunks)

        # Query matches chunk 500
        query_vec = _make_embedding("chunk500", dim=dim)
        tmp_index._embed = lambda texts: [query_vec]  # type: ignore[method-assign]

        import time
        t0 = time.monotonic()
        results = tmp_index.search("query", top_k=5)
        elapsed = time.monotonic() - t0

        assert len(results) == 5
        # Should be fast — numpy vectorised.
        assert elapsed < 0.5, f"Search took {elapsed:.2f}s — numpy should be much faster"
        # Highest score should be chunk500
        assert results[0].file_path == "f500.py"

    def test_ten_thousand_chunks(self, tmp_index):
        dim = 768
        n = 10000
        chunks = []
        for i in range(n):
            emb = np.random.default_rng(i).standard_normal(dim).astype(np.float64)
            emb = emb / (np.linalg.norm(emb) + 1e-12)
            chunks.append((f"f{i}.py", 1, 5, f"c{i}", emb.tolist()))
        _insert_chunks(tmp_index, chunks)

        tmp_index._embed = lambda texts: [chunks[5000][4]]  # type: ignore[method-assign]

        import time
        t0 = time.monotonic()
        results = tmp_index.search("anything", top_k=5)
        elapsed = time.monotonic() - t0

        assert len(results) == 5
        # Even with 10k vectors, numpy @ should be ~5-20ms on modern CPUs
        assert elapsed < 1.0, f"Search took {elapsed:.2f}s — numpy should handle 10k vectors quickly"
