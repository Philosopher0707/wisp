"""Semantic codebase index — embedding-based code search for agent context.

Chunks files by function/class boundaries, generates embeddings via a local
model (Ollama's nomic-embed-text), and stores in sqlite-vec for cosine
similarity retrieval.

Design: local-first, incremental updates, zero external API calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default embedding model (runs locally via Ollama)
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_CHUNK_SIZE = 2000  # chars per chunk
DEFAULT_CHUNK_OVERLAP = 200  # char overlap between chunks
MAX_FILES_TO_INDEX = 5_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", "target", ".next", ".wisp", ".mypy_cache", ".pytest_cache"}
SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
                   ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".webm", ".ogg",
                   ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
                   ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                   ".pyc", ".pyo", ".class", ".o", ".so", ".dylib", ".dll",
                   ".exe", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
                   ".lock", ".min.js", ".min.css", ".map", ".chunk.js",
                   ".pb.go", ".gen.go", ".generated.ts", ".d.ts"}


@dataclass
class CodeChunk:
    file_path: str
    start_line: int
    end_line: int
    content: str
    symbol_name: str = ""
    hash: str = ""


@dataclass
class SearchResult:
    file_path: str
    start_line: int
    end_line: int
    content: str
    symbol_name: str
    score: float


class SemanticIndex:
    """Embedding-backed semantic code search."""

    def __init__(self, workspace: str, db_path: Optional[str] = None,
                 embed_model: str = DEFAULT_EMBED_MODEL,
                 ollama_url: str = "http://localhost:11434"):
        self.workspace = Path(workspace).resolve()
        self.embed_model = embed_model
        self.ollama_url = ollama_url
        self._db_path = db_path or str(self.workspace / ".wisp" / "semantic_index.db")
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_tables()
        return self._conn

    def _init_tables(self):
        """Create tables if they don't exist."""
        c = self.conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                file_hash TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                content TEXT NOT NULL,
                symbol_name TEXT DEFAULT '',
                content_hash TEXT NOT NULL,
                UNIQUE(file_path, start_line, end_line)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                chunk_id INTEGER PRIMARY KEY,
                embedding BLOB NOT NULL,
                FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)
        """)
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── File discovery ───────────────────────────────────────────────

    def discover_files(self) -> list[Path]:
        """Find all indexable files in the workspace."""
        files: list[Path] = []
        for root, dirs, filenames in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                       and not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext in SKIP_EXTENSIONS:
                    continue
                if any(fname.endswith(s) for s in SKIP_EXTENSIONS
                       if s.startswith(".") and s in fname):
                    continue
                # Size limit: 1MB per file
                try:
                    if fpath.stat().st_size > 1_000_000:
                        continue
                except OSError:
                    continue
                files.append(fpath)
                if len(files) >= MAX_FILES_TO_INDEX:
                    break
            if len(files) >= MAX_FILES_TO_INDEX:
                break
        return files

    # ── Chunking ─────────────────────────────────────────────────────

    def chunk_file(self, file_path: Path) -> list[CodeChunk]:
        """Split a file into chunks at function/class boundaries."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

        if not content.strip():
            return []

        lines = content.split("\n")
        ext = file_path.suffix.lower()
        rel_path = str(file_path.relative_to(self.workspace))

        # Detect symbol boundaries via regex patterns
        symbol_patterns = self._symbol_patterns(ext)
        symbol_lines: list[tuple[int, str]] = []  # (line_idx, symbol_name)
        for i, line in enumerate(lines):
            for pat, name_group in symbol_patterns:
                m = pat.match(line) if hasattr(pat, 'match') else pat.search(line)
                if m:
                    name = m.group(name_group) if isinstance(name_group, int) else ""
                    symbol_lines.append((i, name))
                    break

        chunks: list[CodeChunk] = []

        if not symbol_lines:
            # No symbols detected — chunk by size
            chunks = self._chunk_by_size(lines, rel_path, 0, len(lines))
        else:
            # Chunk between symbol boundaries
            prev_line = 0
            for i, (sym_line, sym_name) in enumerate(symbol_lines):
                if sym_line > prev_line:
                    sub = self._chunk_by_size(lines, rel_path, prev_line, sym_line)
                    chunks.extend(sub)
                prev_line = sym_line
            # Tail after last symbol
            if prev_line < len(lines):
                sub = self._chunk_by_size(lines, rel_path, prev_line, len(lines))
                # Tag with last symbol name
                if symbol_lines and sub:
                    sub[0].symbol_name = symbol_lines[-1][1]
                chunks.extend(sub)

        # Hash each chunk
        for c in chunks:
            c.hash = hashlib.sha256(c.content.encode()).hexdigest()[:16]

        return chunks

    @staticmethod
    def _symbol_patterns(ext: str) -> list:
        """Get language-specific symbol boundary patterns."""
        import re

        # Common patterns
        py_patterns = [
            (re.compile(r"^\s*def\s+(\w+)"), 1),
            (re.compile(r"^\s*class\s+(\w+)"), 1),
            (re.compile(r"^\s*async\s+def\s+(\w+)"), 1),
        ]
        ts_js_patterns = [
            (re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"), 1),
            (re.compile(r"(?:export\s+)?class\s+(\w+)"), 1),
            (re.compile(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(?[^)]*\)?\s*=>"), 1),
            (re.compile(r"^\s*(?:public|private|protected|static|async)?\s*(\w+)\s*\([^)]*\)\s*[{:]"), 1),
        ]
        rs_patterns = [
            (re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)"), 1),
            (re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)"), 1),
            (re.compile(r"^\s*(?:pub\s+)?impl\s+(\w+)"), 1),
            (re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)"), 1),
        ]
        go_patterns = [
            (re.compile(r"^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)"), 1),
            (re.compile(r"^\s*type\s+(\w+)\s+struct"), 1),
        ]
        java_patterns = [
            (re.compile(r"^\s*(?:public|private|protected|static|\s)+[\w<>[\]]+\s+(\w+)\s*\("), 1),
            (re.compile(r"^\s*(?:public|private|protected|static|\s)+class\s+(\w+)"), 1),
        ]

        if ext in (".py", ".pyi", ".pyx"):
            return py_patterns
        elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            return ts_js_patterns
        elif ext == ".rs":
            return rs_patterns
        elif ext == ".go":
            return go_patterns
        elif ext in (".java", ".kt", ".scala"):
            return java_patterns
        elif ext in (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx"):
            return [
                (re.compile(r"^\s*(?:static|inline|virtual|const|\s)+[\w:*&<>\s]+\s+(\w+)\s*\("), 1),
                (re.compile(r"^\s*class\s+(\w+)"), 1),
            ]
        else:
            # Generic: try to find any definition-like patterns
            return py_patterns + ts_js_patterns

    @staticmethod
    def _chunk_by_size(lines: list[str], rel_path: str,
                       start: int, end: int) -> list[CodeChunk]:
        """Split line range into size-bounded chunks with overlap."""
        chunks: list[CodeChunk] = []
        pos = start
        while pos < end:
            chunk_end = pos
            char_count = 0
            while chunk_end < end and char_count < DEFAULT_CHUNK_SIZE:
                char_count += len(lines[chunk_end]) + 1
                chunk_end += 1

            content = "\n".join(lines[pos:chunk_end])
            if content.strip():
                chunks.append(CodeChunk(
                    file_path=rel_path,
                    start_line=pos + 1,  # 1-based
                    end_line=chunk_end,   # 1-based
                    content=content,
                ))

            # Advance with overlap
            if chunk_end >= end:
                break
            # Back up for overlap
            overlap_start = max(pos, chunk_end - max(1, DEFAULT_CHUNK_OVERLAP // 60))
            if overlap_start <= pos:
                pos = chunk_end
            else:
                pos = overlap_start

        return chunks

    # ── Embedding ────────────────────────────────────────────────────

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Ollama embedding API."""
        import requests
        embeddings: list[list[float]] = []

        for text in texts:
            try:
                resp = requests.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.embed_model, "prompt": text},
                    timeout=30,
                )
                if resp.ok:
                    data = resp.json()
                    emb = data.get("embedding", [])
                    if emb:
                        embeddings.append(emb)
                        continue
            except Exception as e:
                logger.warning("Embedding request failed: %s", e)

            # Fallback: zero vector (dim 768 for nomic-embed-text)
            embeddings.append([0.0] * 768)

        return embeddings

    # ── Indexing ─────────────────────────────────────────────────────

    def index_file(self, file_path: Path) -> int:
        """Index a single file: chunk, embed, store. Returns chunk count."""
        rel_path = str(file_path.relative_to(self.workspace))

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            return 0

        # Check if file needs re-indexing
        row = self.conn.execute(
            "SELECT mtime, file_hash FROM files WHERE path = ?", (rel_path,)
        ).fetchone()
        if row:
            try:
                # Quick check via mtime
                if abs(row[0] - mtime) < 0.1:
                    current = self.conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE file_path = ?", (rel_path,)
                    ).fetchone()
                    return current[0] if current else 0
            except Exception:
                pass

        # Delete old chunks
        self.conn.execute("DELETE FROM embeddings WHERE chunk_id IN "
                          "(SELECT id FROM chunks WHERE file_path = ?)", (rel_path,))
        self.conn.execute("DELETE FROM chunks WHERE file_path = ?", (rel_path,))
        self.conn.execute("DELETE FROM files WHERE path = ?", (rel_path,))

        # Chunk
        chunks = self.chunk_file(file_path)
        if not chunks:
            self.conn.commit()
            return 0

        # Embed
        contents = [c.content for c in chunks]
        embeddings = self._embed(contents)

        # Store chunks and embeddings
        for chunk, emb in zip(chunks, embeddings):
            c = self.conn.execute(
                """INSERT INTO chunks (file_path, start_line, end_line, content, symbol_name, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chunk.file_path, chunk.start_line, chunk.end_line,
                 chunk.content, chunk.symbol_name, chunk.hash),
            )
            chunk_id = c.lastrowid
            # pack embedding as little-endian 8-byte floats
            import struct
            emb_bytes = struct.pack(f"<{len(emb)}d", *emb)
            self.conn.execute(
                "INSERT INTO embeddings (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, emb_bytes),
            )

        # Record file
        file_hash = hashlib.sha256(
            "\n".join(contents).encode()
        ).hexdigest()[:16]
        self.conn.execute(
            "INSERT OR REPLACE INTO files (path, mtime, file_hash, chunk_count) VALUES (?, ?, ?, ?)",
            (rel_path, mtime, file_hash, len(chunks)),
        )
        self.conn.commit()
        return len(chunks)

    def index_all(self) -> dict:
        """Index all discoverable files. Returns stats dict."""
        files = self.discover_files()
        total = 0
        indexed = 0
        skipped = 0
        start = time.time()

        for fp in files:
            try:
                n = self.index_file(fp)
                if n > 0:
                    indexed += 1
                    total += n
                else:
                    skipped += 1
            except Exception as e:
                logger.debug("Index error for %s: %s", fp, e)
                skipped += 1

        elapsed = time.time() - start
        logger.info("Indexed %d chunks from %d files in %.1fs (skipped %d)",
                     total, indexed, elapsed, skipped)
        return {"files_indexed": indexed, "chunks": total, "skipped": skipped,
                "elapsed_ms": round(elapsed * 1000), "total_files": len(files)}

    # ── Search ───────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Semantic search: embed query, compute cosine similarity, return top-k chunks."""
        embeddings = self._embed([query])
        if not embeddings or not embeddings[0]:
            return []

        query_vec = embeddings[0]
        if all(v == 0.0 for v in query_vec):
            return []

        import struct
        results: list[tuple[float, int]] = []

        # Compute cosine similarity against all stored embeddings
        rows = self.conn.execute(
            "SELECT e.chunk_id, e.embedding FROM embeddings e"
        ).fetchall()

        for chunk_id, emb_bytes in rows:
            dim = len(query_vec)
            stored = struct.unpack(f"<{dim}d", emb_bytes[:dim * 8]) if len(emb_bytes) >= dim * 8 else None
            if stored is None:
                continue

            # Cosine similarity
            dot = sum(a * b for a, b in zip(query_vec, stored))
            norm_q = sum(a * a for a in query_vec) ** 0.5
            norm_s = sum(b * b for b in stored) ** 0.5
            if norm_q == 0 or norm_s == 0:
                continue
            score = dot / (norm_q * norm_s)
            results.append((score, chunk_id))

        # Sort by score descending, take top_k
        results.sort(key=lambda x: x[0], reverse=True)
        top = results[:top_k]

        # Fetch chunk data
        output: list[SearchResult] = []
        for score, chunk_id in top:
            row = self.conn.execute(
                "SELECT file_path, start_line, end_line, content, symbol_name FROM chunks WHERE id = ?",
                (chunk_id,),
            ).fetchone()
            if row:
                output.append(SearchResult(
                    file_path=row[0],
                    start_line=row[1],
                    end_line=row[2],
                    content=row[3],
                    symbol_name=row[4],
                    score=round(score, 4),
                ))

        return output

    def get_stats(self) -> dict:
        """Return index statistics."""
        files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()
        chunks = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        emb = self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        return {
            "files": files[0] if files else 0,
            "chunks": chunks[0] if chunks else 0,
            "embeddings": emb[0] if emb else 0,
            "model": self.embed_model,
        }
