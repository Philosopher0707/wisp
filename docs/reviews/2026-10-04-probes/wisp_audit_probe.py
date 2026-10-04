# Run from the wisp repo with its venv: python wisp_audit_probe.py (uses temp files only)
import json, tempfile, hashlib
from pathlib import Path
from wisp.infra.audit import AuditTrail

def fresh():
    return Path(tempfile.mkdtemp()) / "audit.jsonl"

p = fresh(); a = AuditTrail(p)
for i in range(4): a.record("act", actor="alice", key="k", new_value=i)
print("W0 baseline verify (None = intact):", a.verify())

# W1: two writers (e.g. CLI + server) appending to the same log
p = fresh(); a, b = AuditTrail(p), AuditTrail(p)
a.record("a1"); b.record("b1"); a.record("a2"); b.record("b2")
print("W1 two writers interleaved -> verify returns first bad entry:", AuditTrail(p).verify())

# W2: tail truncation
p = fresh(); a = AuditTrail(p)
for i in range(4): a.record("act", new_value=i)
L = p.read_text().splitlines(); p.write_text("\n".join(L[:2]) + "\n")
print("W2 tail truncation detected?", AuditTrail(p).verify() is not None)

# W3: attacker rewrites an entry and recomputes the chain, no secret needed
p = fresh(); a = AuditTrail(p)
for i in range(4): a.record("act", actor="alice", new_value=i)
rows = [json.loads(l) for l in p.read_text().splitlines()]
rows[0]["actor"] = "mallory"; prev = ""
for r in rows:
    r.pop("_hash", None); r["_prev_hash"] = prev
    r["_hash"] = hashlib.sha256(json.dumps({k: v for k, v in r.items() if k != "_hash"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(); prev = r["_hash"]
p.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
print("W3 forged-and-rechained log passes verify?", AuditTrail(p).verify() is None)

# W4: a corrupt line then a new writer
p = fresh(); a = AuditTrail(p); a.record("x"); p.write_text(p.read_text() + "garbage\n")
b = AuditTrail(p); b.record("y")
print("W4 after a corrupt line, a new writer's verify:", AuditTrail(p).verify())
