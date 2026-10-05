# Run from the plateform repo root: python3 plateform_probe.py (uses temp files only)
import asyncio, json, hashlib, tempfile, sys
from pathlib import Path
sys.path.insert(0, ".")
from core.gateway.audit import AuditLogger, AuditLogEntry
from core.orchestrator.hitl import HITLManager, ApprovalStatus

async def audit():
    d = Path(tempfile.mkdtemp()); p = d / "audit.jsonl"
    a = AuditLogger(log_path=p)
    for i in range(4):
        await a.log("u1", "alice", "t1", "TOOL_EXECUTE", "agent_run", f"r{i}", {"n": i})
    print("A0 baseline verify:", a.verify_integrity()["valid"])
    # A1: change actor_name (not in hash input)
    lines = p.read_text().splitlines(); o = json.loads(lines[1]); o["actor_name"] = "mallory"; lines[1] = json.dumps(o)
    p.write_text("\n".join(lines) + "\n"); print("A1 forged actor_name still verifies:", a.verify_integrity()["valid"])
    # A2: truncate the last two entries
    p.write_text("\n".join(lines[:2]) + "\n"); print("A2 tail truncation still verifies:", a.verify_integrity()["valid"], "entries:", a.verify_integrity()["entries_verified"])
    # A3: rewrite an entry's details and recompute the whole chain with no key
    p.write_text("\n".join(lines) + "\n"); rows = [AuditLogEntry(**json.loads(l)) for l in p.read_text().splitlines()]
    rows[0].details = {"n": "forged"}; prev = "0"*64
    for r in rows: r.prev_hash = prev; r.entry_hash = r.calculate_hash(); prev = r.entry_hash
    p.write_text("\n".join(r.model_dump_json() for r in rows) + "\n"); print("A3 attacker-recomputed chain verifies (no key needed):", a.verify_integrity()["valid"])
    # A4: a garbage line
    p.write_text(p.read_text() + "not json\n")
    try: print("A4 verify with a corrupt line:", a.verify_integrity()["valid"])
    except Exception as e: print("A4 verify with a corrupt line RAISES:", type(e).__name__)

async def hitl():
    h = HITLManager(db_path=str(Path(tempfile.mkdtemp()) / "h.db"))
    r = await h.create_request("run1", "cp1", "node", "TOOL_INVOCATION", "issue_refund", {"amount": 5})
    await h.resolve_request(r.request_id, ApprovalStatus.REJECTED, "alice", "no")
    again = await h.resolve_request(r.request_id, ApprovalStatus.APPROVED, "mallory", "yes")
    print("H1 rejected request re-resolved to:", again.status.value, "by", again.reviewer_id)
    r2 = await h.create_request("run1", "cp1", "node", "TOOL_INVOCATION", "issue_refund", {"amount": 5})
    p = await h.resolve_request(r2.request_id, ApprovalStatus.PENDING, "x")
    print("H2 'resolving' to PENDING accepted:", p.status.value)
    m = await h.resolve_request(r2.request_id, ApprovalStatus.APPROVED, "alice", None, {"amount": 5000})
    print("H3 approved with a different payload, original kept alongside:", m.modified_payload, "| original", m.action_payload)
asyncio.run(audit()); asyncio.run(hitl())
