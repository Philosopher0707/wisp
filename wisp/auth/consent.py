"""Extension consent + quarantine record (M2 authority layer).

First-use consent for MCP servers/plugins: server id + origin hash + scopes
+ timestamp, stored as JSONL under the workspace `.wisp/` dir. Consent is
invalidated by origin change or scope widening. Unsigned/unlisted
extensions are quarantined until approved (full manager wiring arrives
with bundle allowlists in M4; this module ships the record + check API).
"""
from __future__ import annotations
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CONSENT_FILE = "consents.jsonl"


def origin_hash(origin: str) -> str:
    return hashlib.sha256(origin.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class ConsentRecord:
    server_id: str
    origin_hash: str
    scopes: tuple[str, ...] = ()
    signed: bool = False
    timestamp: float = field(default_factory=time.time)
    version: int = 1

    def to_dict(self) -> dict:
        return {"server_id": self.server_id, "origin_hash": self.origin_hash,
                "scopes": list(self.scopes), "signed": self.signed,
                "timestamp": self.timestamp, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "ConsentRecord":
        return cls(server_id=d["server_id"], origin_hash=d["origin_hash"],
                   scopes=tuple(d.get("scopes") or []),
                   signed=d.get("signed", False),
                   timestamp=d.get("timestamp", 0.0),
                   version=d.get("version", 1))


def _consent_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".wisp" / CONSENT_FILE


def record_consent(workspace: str | Path, *, server_id: str, origin: str,
                   scopes: tuple[str, ...] = (), signed: bool = False) -> ConsentRecord:
    rec = ConsentRecord(server_id=server_id, origin_hash=origin_hash(origin),
                        scopes=tuple(scopes), signed=signed)
    path = _consent_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec.to_dict(), sort_keys=True) + "\n")
    return rec


def check_consent(workspace: str | Path, *, server_id: str, origin: str,
                  scopes: tuple[str, ...] = ()) -> Optional[ConsentRecord]:
    """Return the latest matching consent, or None.

    Matches on server id + origin hash; recorded scopes must cover the
    requested scopes (narrowing OK, widening needs re-consent).
    """
    path = _consent_path(workspace)
    if not path.exists():
        return None
    want = origin_hash(origin)
    best: Optional[ConsentRecord] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = ConsentRecord.from_dict(json.loads(line))
        except (ValueError, KeyError):
            continue
        if rec.server_id != server_id or rec.origin_hash != want:
            continue
        if not set(scopes) <= set(rec.scopes):
            continue
        if best is None or rec.timestamp >= best.timestamp:
            best = rec
    return best


def quarantined(workspace: str | Path, *, server_id: str, signed: bool,
                origin: str = "", scopes: tuple[str, ...] = ()) -> bool:
    """Unsigned extensions are quarantined until explicit consent.

    When an origin is given, matching recorded consent is always required
    (signed or not); without an origin only the signed bit speaks.
    """
    if origin:
        return check_consent(workspace, server_id=server_id,
                             origin=origin, scopes=scopes) is None
    return not signed
