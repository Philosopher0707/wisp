"""Append-only, hash-chained change ledger.

Every record commits to the previous record's hash (SHA-256 over canonical JSON), so an
edited, deleted, reordered or inserted record breaks the chain at a point `verify()`
names. Records are JSON lines on disk (or kept in memory). The ledger holds what
happened to the network and why: verifications, approvals, applies, confirmations,
rollbacks, kill-switch changes, refusals.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def record_hash(prev: str, seq: int, ts: float, kind: str, data: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({"prev": prev, "seq": seq, "ts": ts, "kind": kind, "data": data}).encode()).hexdigest()


@dataclass(frozen=True)
class Record:
    seq: int
    ts: float
    kind: str
    data: dict[str, Any]
    prev: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "kind": self.kind, "data": self.data, "prev": self.prev,
                "hash": self.hash}


class LedgerCorrupt(ValueError):
    pass


class Ledger:
    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._path = Path(path) if path else None
        self._records: list[Record] = []
        if self._path and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    self._records.append(Record(d["seq"], d["ts"], d["kind"], d["data"], d["prev"], d["hash"]))
            problems = self.verify()
            if problems:
                raise LedgerCorrupt(f"{self._path}: {problems[0]}")

    @property
    def head(self) -> str:
        return self._records[-1].hash if self._records else GENESIS

    def append(self, kind: str, data: dict[str, Any], ts: float) -> Record:
        with self._lock:
            seq = len(self._records) + 1
            data = json.loads(_canonical(data))
            rec = Record(seq, ts, kind, data, self.head, record_hash(self.head, seq, ts, kind, data))
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(_canonical(rec.to_dict()) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            self._records.append(rec)
            return rec

    def records(self, since_seq: int = 0, kind: str | None = None, fingerprint: str | None = None,
                limit: int = 200) -> list[Record]:
        with self._lock:
            out = [r for r in self._records if r.seq > since_seq and (kind is None or r.kind == kind)
                   and (fingerprint is None or r.data.get("fingerprint") == fingerprint)]
        return out[-limit:]

    def verify(self) -> list[str]:
        """Every break in the chain, first one first; empty means intact."""
        problems: list[str] = []
        prev = GENESIS
        for i, r in enumerate(self._records, start=1):
            if r.seq != i:
                problems.append(f"record {i} carries seq {r.seq}: a record was removed or reordered")
            if r.prev != prev:
                problems.append(f"record {r.seq} does not follow its predecessor's hash")
            if record_hash(r.prev, r.seq, r.ts, r.kind, r.data) != r.hash:
                problems.append(f"record {r.seq} ({r.kind}) was modified after it was written")
            prev = r.hash
        return problems

    def __len__(self) -> int:
        return len(self._records)
