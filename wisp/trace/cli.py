"""Trace/evidence CLI (M5 T5): trace, replay --dry-run, audit-verify,
task export-evidence. Thin adapters over wisp.trace (unit-tested).

Environment:
  WISP_DB        trace/store SQLite path (default <cwd>/.wisp/wisp.db)
  WISP_AUDIT_LOG audit JSONL path (default per ImmutableAuditTrail)
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import TextIO


def _db_path(args: list[str]) -> Path:
    if "--db" in args:
        return Path(args[args.index("--db") + 1])
    env = os.environ.get("WISP_DB")
    if env:
        return Path(env)
    if "--workspace" in args:
        return Path(args[args.index("--workspace") + 1]) / ".wisp" / "wisp.db"
    return Path.cwd() / ".wisp" / "wisp.db"


def _trace_store(db: Path):
    from wisp.infra.store import UnifiedStore
    from wisp.trace.store import SQLiteTraceStore
    return SQLiteTraceStore(UnifiedStore(db))


def _cmd_trace(args: list[str], out: TextIO) -> int:
    if not args:
        print("usage: wisp trace <trace-id|run-id> [--db PATH]", file=out)
        return 2
    store = _trace_store(_db_path(args))
    spans = store.query(args[0])
    if not spans:
        spans = store.query_run(args[0])
    if not spans:
        print(f"no spans for {args[0]!r}", file=out)
        return 1
    print(f"trace {spans[0].trace_id}: {len(spans)} spans", file=out)
    for s in spans:
        indent = "  " if s.parent_span_id else ""
        print(f"{indent}{s.kind:15s} {s.name or s.span_id} "
              f"{s.duration_ms:.0f}ms [{s.status.value}]", file=out)
    return 0


def _cmd_replay(args: list[str], out: TextIO) -> int:
    if "--dry-run" not in args:
        print("replay executes nothing: re-run with --dry-run", file=out)
        return 2
    rest = [a for a in args if a != "--dry-run"]
    if not rest:
        print("usage: wisp replay --dry-run <trace-id>", file=out)
        return 2
    from wisp.trace.export import replay_plan
    store = _trace_store(_db_path(args))
    plan = replay_plan(store, rest[0])
    print(f"dry-run replay of {rest[0]}: {len(plan)} tool steps (no execution)", file=out)
    for step in plan:
        print(f"  {step['seq']}: {step['tool']} {json.dumps(step['args'], sort_keys=True)}",
              file=out)
    return 0


def _cmd_audit_verify(args: list[str], out: TextIO) -> int:
    from wisp.infra.audit import AuditTrail
    trail = AuditTrail()
    bad = trail.verify()
    if bad is None:
        print("audit chain intact", file=out)
        return 0
    print(f"TAMPERED: first bad entry at line {bad}", file=out)
    return 1


def _cmd_export_evidence(args: list[str], out: TextIO) -> int:
    if not args:
        print("usage: wisp task export-evidence <trace-id|run-id> [--out FILE]", file=out)
        return 2
    from wisp.trace.export import export_evidence
    store = _trace_store(_db_path(args))
    target = args[0]
    spans = store.query(target) or store.query_run(target)
    if not spans:
        print(f"no spans for {target!r}", file=out)
        return 1
    ev = export_evidence(store, spans[0].trace_id)
    if "--out" in args:
        dest = Path(args[args.index("--out") + 1])
        dest.write_text(json.dumps(ev, indent=2, sort_keys=True), encoding="utf-8")
        print(f"evidence for {ev['trace_id']}: {ev['span_count']} spans -> {dest}", file=out)
    else:
        print(json.dumps(ev, indent=2, sort_keys=True), file=out)
    return 0


def main(argv: list[str], out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    if not argv:
        print("usage: wisp trace <id> | replay --dry-run <id> | audit-verify | task ...",
              file=out)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "trace":
        return _cmd_trace(rest, out)
    if cmd == "replay":
        return _cmd_replay(rest, out)
    if cmd == "audit-verify":
        return _cmd_audit_verify(rest, out)
    print(f"unknown trace command {cmd!r}", file=out)
    return 2


def task_main(argv: list[str], out: TextIO | None = None) -> int:
    """`wisp task ...` — M6 umbrella; export-evidence ships first (M5)."""
    out = out if out is not None else sys.stdout
    if len(argv) >= 2 and argv[0] == "export-evidence":
        return _cmd_export_evidence(argv[1:], out)
    print("usage: wisp task export-evidence <id> [--out FILE] "
          "(start/list/inspect/resume/cancel land in M6)", file=out)
    return 2
