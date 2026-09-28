"""Domain knowledge base: runbooks and references, searched for retrieval-augmented diagnosis.

Documents are Markdown, chunked at `##` headings and ranked with Okapi BM25. Lexical
ranking needs no embedding model download and is exact about the vocabulary operators
search with (`FCS`, `Idle`, `dBm`). Milvus/Qdrant with domain embeddings can replace it
behind `search`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent.parent / "knowledge"
_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-_/]*[a-z0-9]|[a-z0-9]")
_STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was "
                  "were will with when what which if then than not no can do does".split())


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP:
            continue
        tokens.append(tok)
        if "-" in tok or "/" in tok:
            tokens.extend(t for t in re.split(r"[-/]", tok) if t and t not in _STOP)
    return tokens


@dataclass(frozen=True)
class Chunk:
    doc: str
    title: str
    section: str
    text: str


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class KnowledgeBase:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.chunks: list[Chunk] = []
        self._tf: list[dict[str, int]] = []
        self._len: list[int] = []
        self._df: dict[str, int] = {}

    @classmethod
    def bundled(cls) -> "KnowledgeBase":
        kb = cls()
        for path in sorted(BUNDLED.glob("*.md")):
            kb.add_markdown(path.stem, path.read_text(encoding="utf-8"))
        return kb

    def add_markdown(self, doc: str, text: str) -> None:
        title = doc
        section, body = "", list[str]()
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                self._add(doc, title, section, "\n".join(body))
                section, body = line[3:].strip(), []
            else:
                body.append(line)
        self._add(doc, title, section, "\n".join(body))

    def _add(self, doc: str, title: str, section: str, body: str) -> None:
        body = body.strip()
        if not body:
            return
        chunk = Chunk(doc, title, section, body)
        tokens = tokenize(f"{title} {section} {body}")
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for t in tf:
            self._df[t] = self._df.get(t, 0) + 1
        self.chunks.append(chunk)
        self._tf.append(tf)
        self._len.append(len(tokens))

    def search(self, query: str, k: int = 5) -> list[Hit]:
        terms = tokenize(query)
        if not terms or not self.chunks:
            return []
        n = len(self.chunks)
        avg = sum(self._len) / n
        scores: list[tuple[float, int]] = []
        for i, tf in enumerate(self._tf):
            score = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                score += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self._len[i] / avg))
            if score > 0:
                scores.append((score, i))
        scores.sort(key=lambda s: (-s[0], s[1]))
        return [Hit(self.chunks[i], round(s, 3)) for s, i in scores[:k]]
