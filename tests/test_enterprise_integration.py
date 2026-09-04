# tests/test_enterprise_integration.py — cross-module flows over real
# components (tmp SQLite, real Ed25519, real CLI entry points). Proves the
# milestones compose; unit suites prove the pieces.
import asyncio
import io
import json
import time

from wisp.infra.store import UnifiedStore


def _db(tmp_path, name="w.db"):
    return UnifiedStore(tmp_path / name)


def test_task_lifecycle_end_to_end(tmp_path):
    """M6+M3: start → plan → approve → pause/resume/cancel, all durable."""
    from wisp.runs.store import SQLiteRunStore
    from wisp.task.manager import TaskManager
    from wisp.task.review import approve_scope

    tm = TaskManager(SQLiteRunStore(_db(tmp_path)))
    tid = tm.start("ship login", workspace=str(tmp_path))
    plan = {"goal": "ship login", "files": ["a.py"],
            "actions": [{"tool": "read_file", "args": {"path": "a.py"}}]}
    tm.attach_plan(tid, plan)
    decision = approve_scope(tm.inspect(tid)["plan"], scope="all", approver="e2e")
    assert decision["approved"] is True
    assert tm.pause(tid)["status"] == "paused"
    assert tm.resume(tid)["status"] == "running"
    # Fresh manager over the same DB sees everything (durability).
    tm2 = TaskManager(SQLiteRunStore(_db(tmp_path)))
    insp = tm2.inspect(tid)
    assert insp["status"] == "running" and insp["plan"] == plan
    assert [t["to"] for t in insp["transitions"]] == ["running", "paused", "running"]
    assert tm2.cancel(tid)["status"] == "cancelled"


def test_background_run_crash_recovery_end_to_end(tmp_path):
    """M3: launch persists, process death parks, evidence survives."""
    from wisp.multi_agent.background import BackgroundAgentManager
    from wisp.multi_agent.task import SubagentContract, SubagentResult
    from wisp.runs.record import RunState
    from wisp.runs.store import SQLiteRunStore

    class _Orch:
        async def _run_with_retry(self, contract):
            await asyncio.sleep(0.02)
            return SubagentResult(task_id=contract.name, success=True,
                                  output="ok", files_changed=[],
                                  elapsed_seconds=0.02, error=None,
                                  session_id="s1")

    async def scenario():
        mgr = BackgroundAgentManager(_Orch(),
                                     run_store=SQLiteRunStore(_db(tmp_path)))
        out = await mgr.launch(SubagentContract(name="bg-e2e", task="t"))
        await mgr.result(out["agent_id"], wait_seconds=5)
        return out["agent_id"]

    agent_id = asyncio.run(scenario())
    # "New process": fresh store handle + manager recovers (nothing stale).
    from wisp.multi_agent.background import BackgroundAgentManager as BAM
    store = SQLiteRunStore(_db(tmp_path))
    assert store.get(agent_id).status == RunState.SUCCEEDED
    assert BAM(_Orch(), run_store=store).recover() == {
        "paused": 0, "cancelled": 0, "left": 0}


def test_trace_evidence_replay_end_to_end(tmp_path):
    """M5: spans → query → redacted evidence → dry-run replay plan."""
    from wisp.trace.export import export_evidence, replay_plan
    from wisp.trace.span import Span
    from wisp.trace.store import SQLiteTraceStore

    store = SQLiteTraceStore(_db(tmp_path))
    store.append(Span(trace_id="t9", span_id="s1", kind="turn", name="t",
                      started_at=1.0, finished_at=4.0,
                      attrs={"run_id": "task-x"}))
    store.append(Span(trace_id="t9", span_id="s2", kind="tool_call",
                      name="write_file", parent_span_id="s1",
                      started_at=2.0, finished_at=3.0,
                      attrs={"run_id": "task-x", "path": "a.py",
                             "token": "ghp_abcdefghijklmnopqrstuvwxyZ1234567890"}))
    # Separate reader handle, same DB.
    reader = SQLiteTraceStore(_db(tmp_path))
    assert len(reader.query_run("task-x")) == 2
    ev = export_evidence(reader, "t9")
    assert ev["redacted"] is True and "ghp_" not in json.dumps(ev)
    plan = replay_plan(reader, "t9")
    assert plan[0]["tool"] == "write_file" and "ghp_" not in json.dumps(plan)


def test_policy_bundle_to_cli_end_to_end(tmp_path, monkeypatch):
    """M4: keypair → signed file → verify + inspect + explain via CLI."""
    from wisp.policy.bundle import PolicyBundle, generate_keypair, sign_bundle
    from wisp.policy.cli import main as policy_main

    priv, pub = generate_keypair()
    now = time.time()
    payload = {"bundle_version": 1, "org_id": "e2e-org", "issued_at": now,
               "expires_at": now + 3600, "revocation_seq": 1,
               "approval_matrix": {"run_bash": "deny"}}
    bundle = PolicyBundle.from_dict(payload)
    f = tmp_path / "bundle.json"
    f.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    f.with_suffix(".json.sig").write_text(sign_bundle(bundle, priv),
                                           encoding="utf-8")
    monkeypatch.setenv("WISP_POLICY_BUNDLE", str(f))
    monkeypatch.setenv("WISP_POLICY_PUBKEY", pub)

    out = io.StringIO()
    assert policy_main(["verify"], out=out) == 0
    out = io.StringIO()
    assert policy_main(["inspect"], out=out) == 0
    assert "e2e-org" in out.getvalue()
    out = io.StringIO()
    assert policy_main(["explain", "run_bash"], out=out) == 0
    assert "deny" in out.getvalue()


def test_authority_denies_quarantined_task_workspace(tmp_path):
    """M2+M6: a task rooted in a quarantined checkout cannot execute writes."""
    from wisp.auth import authorize, classify_workspace, local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    ws = tmp_path / "evil-checkout"
    ws.mkdir()
    (ws / ".wisp-quarantine").write_text("untrusted")
    assert classify_workspace(ws, frozenset()) == WorkspaceTrust.QUARANTINED
    p = local_principal(workspace=str(ws), profile="personal")
    d = authorize(p, "write_file", {"path": str(ws / "x.py")},
                  WorkspaceTrust.QUARANTINED, permission_mode="full")
    assert d.allowed is False and d.controlling_layer == "workspace"


def test_task_cli_and_trace_cli_share_store(tmp_path, monkeypatch):
    """M5+M6: task CLI writes, trace CLI reads the same WISP_DB."""
    from wisp.task.cli import main as task_main
    from wisp.trace.cli import main as trace_main
    from wisp.trace.span import Span
    from wisp.trace.store import SQLiteTraceStore

    db = tmp_path / "shared.db"
    monkeypatch.setenv("WISP_DB", str(db))
    out = io.StringIO()
    assert task_main(["start", "shared job"], out=out) == 0
    tid = out.getvalue().strip()

    store = SQLiteTraceStore(_db(tmp_path, "shared.db"))
    store.append(Span(trace_id="tt", span_id="s1", kind="turn", name="t",
                      started_at=1.0, finished_at=2.0,
                      attrs={"run_id": tid}))
    out = io.StringIO()
    assert trace_main(["trace", tid], out=out) == 0
    assert "turn" in out.getvalue()


def test_release_smoke_end_to_end(tmp_path, monkeypatch):
    """M7: lock verify runs, SBOM builds, health passes, bundle redacted."""
    from wisp.release.diagnostics import support_bundle
    from wisp.release.health import health_check
    from wisp.release.lock import verify_lock
    from wisp.release.sbom import generate_sbom

    monkeypatch.setenv("WISP_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("WISP_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    assert isinstance(verify_lock(), list)  # runs against the real env
    sbom = generate_sbom("0.1.0")
    assert sbom["bomFormat"] == "CycloneDX" and sbom["components"]
    assert all(r["ok"] for r in health_check())
    bundle = support_bundle(config={"k": "AKIAIOSFODNN7EXAMPLE"})
    assert bundle["redacted"] is True and "AKIA" not in json.dumps(bundle)
