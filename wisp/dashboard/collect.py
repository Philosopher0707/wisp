"""Read-only collectors for the dashboard. Every function returns plain JSON-able data and never raises: a source that is missing, unreadable or malformed
becomes an empty or partial result, with an `error` note where it helps, because a dashboard that dies on one bad file shows nothing.

PRIVACY: aggregates only. No prompt, message, file content, fact, skill body or key is ever returned; counts, names of tools and models, dates and statuses are.
SQLite files are opened read-only and nothing is written anywhere.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sqlite3
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import wisp.dashboard.bench as _bench
import wisp.dashboard.stats as S

_SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode", "Library", ".Trash", "site-packages", "_scratch", "_archive"})
_TEST_DOUBLE = re.compile(r"^(?:test|mock|fake|stub)|^unknown$", re.I)
_MAX_WORKSPACES = 40


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def tilde(path: str | Path) -> str:
    s = str(path)
    h = str(_home())
    return "~" + s[len(h):] if s.startswith(h) else s


# ── discovery ──────────────────────────────────────────────────────────────

def find_workspaces(roots: Iterable[Path] | None = None, max_depth: int = 4) -> list[Path]:
    """Directories that contain Wisp's own data (`.wisp/wisp.db` or `.wisp/audit.jsonl`). A bounded walk of a few roots, never the whole disk."""
    home = _home()
    roots = list(roots) if roots is not None else [home / "active", home / "workspace", Path.cwd()]  # not ~/dev: the checkouts there hold the artifacts of test runs
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            root = root.resolve()
            if not root.is_dir():
                continue
            base_depth = len(root.parts)
            for dirpath, dirnames, _files in os.walk(root, onerror=lambda e: None):
                depth = len(Path(dirpath).parts) - base_depth
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and (d == ".wisp" or not d.startswith("."))] if depth < max_depth else []
                p = Path(dirpath)
                if p.name == ".wisp":
                    ws = p.parent
                    if ws not in seen and ((p / "wisp.db").exists() or (p / "audit.jsonl").exists()):
                        seen.add(ws)
                        found.append(ws)
                    dirnames[:] = []
                if len(found) >= _MAX_WORKSPACES:
                    return found
        except OSError:
            continue
    return found


def _connect(path: Path) -> sqlite3.Connection | None:
    for uri in (f"file:{path}?mode=ro", f"file:{path}?mode=ro&immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2)
            con.execute("select 1 from sqlite_master limit 1")
            return con
        except sqlite3.Error:
            continue
    return None


def _iso(ts: Any) -> str | None:
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")
        return str(ts)[:19] if ts else None
    except (OverflowError, OSError, ValueError):
        return None


def _day(ts: Any) -> str | None:
    iso = _iso(ts)
    return iso[:10] if iso else None


# ── benchmark ──────────────────────────────────────────────────────────────

def bench_data(bench_dir: Path) -> dict[str, Any]:
    """Runs, and the numbers they support: per (model, configuration) with an interval, a per-task matrix, differences between configurations of one model."""
    runs: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    try:
        meta_files = sorted(bench_dir.glob("*.meta.json")) if bench_dir.is_dir() else []
    except OSError:
        meta_files = []
    for mf in meta_files:
        try:
            meta = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = _bench.read_rows(mf.with_name(mf.name.replace(".meta.json", ".jsonl")))
        done = sum(1 for r in rows if r.get("final"))
        spec = meta.get("spec", {})
        runs.append({"run_id": meta.get("run_id"), "model": spec.get("model"), "label": spec.get("label"), "provider": spec.get("provider") or "default",
                     "harness": meta.get("harness", {}), "started": _iso(meta.get("started")), "finished": _iso(meta.get("finished")),
                     "expected": meta.get("expected_outcomes"), "done": done, "attempts": len(rows),
                     "in_progress": meta.get("finished") is None, "env": spec.get("env", {})})
        for r in rows:
            r = dict(r)
            r["harness_sha"] = r.get("harness_sha") or meta.get("harness", {}).get("sha")
            all_rows.append(r)
    key = lambda r: f"{r.get('model')} | {r.get('label')}"  # noqa: E731
    groups = S.group_by(all_rows, key)
    summaries = []
    for name, rows in sorted(groups.items()):
        s = S.summarize(rows)
        model, label = name.split(" | ", 1)
        shas = sorted({str(r.get("harness_sha")) for r in rows if r.get("harness_sha")})
        summaries.append({"group": name, "model": model, "label": label, "harness_shas": shas, "summary": s, "trust": S.verdict_label(s)})
    per_task = S.task_matrix(all_rows, key)
    comparisons = []
    by_model = S.group_by([x for x in summaries], lambda x: x["model"])
    for model, items in by_model.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i]["summary"], items[j]["summary"]
                d = S.newcombe_difference(a["solved"], a["scored"], b["solved"], b["scored"])
                if d is not None:
                    comparisons.append({"model": model, "a": items[i]["label"], "b": items[j]["label"], "diff": d[0], "low": d[1], "high": d[2],
                                        "significant": d[1] > 0 or d[2] < 0})
    daily: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"solved": 0, "scored": 0, "infra": 0})
    for r in all_rows:
        if not r.get("final"):
            continue
        d = daily[str(r.get("ts", ""))[:10]]
        if r.get("verdict") in S.UNSCORED:
            d["infra"] += 1
        else:
            d["scored"] += 1
            d["solved"] += 1 if r.get("verdict") == "SOLVED" else 0
    return {"dir": tilde(bench_dir), "runs": runs, "groups": summaries, "per_task": per_task, "comparisons": comparisons,
            "daily": dict(sorted(daily.items())), "tasks": sorted(per_task),
            "scope_note": "13 small, self-contained tasks (a few are traps that reward hard-coding): this measures basic competence and honesty, not ability on a real repository."}


# ── real use: models, tools, runs ──────────────────────────────────────────

def _audit_rows(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d
    except OSError:
        return


def usage_data(workspaces: list[Path]) -> dict[str, Any]:
    """What the agent did in real sessions: models, tool volume and error rates, outcomes. Per workspace and in total."""
    models: dict[str, dict[str, Any]] = {}
    tools: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    per_day: collections.Counter[str] = collections.Counter()
    decisions: collections.Counter[str] = collections.Counter()
    events: collections.Counter[str] = collections.Counter()
    runs: collections.Counter[str] = collections.Counter()
    graphs: collections.Counter[str] = collections.Counter()
    sessions = 0
    per_ws = []
    for ws in workspaces:
        info: dict[str, Any] = {"workspace": tilde(ws), "tool_calls": 0, "sessions": 0, "error": None}
        audit = ws / ".wisp" / "audit.jsonl"
        for d in _audit_rows(audit):
            if "tool" not in d:
                continue
            tools[str(d["tool"])][str(d.get("result_status"))] += 1
            decisions[str(d.get("decision"))] += 1
            day = _day(d.get("timestamp"))
            if day:
                per_day[day] += 1
            info["tool_calls"] += 1
        con = _connect(ws / ".wisp" / "wisp.db")
        if con is not None:
            try:
                cur = con.cursor()
                for model, n, created, updated in cur.execute("select model, coalesce(msg_count,0), created_at, updated_at from sessions"):
                    m = models.setdefault(str(model or "unknown"), {"sessions": 0, "messages": 0, "first": None, "last": None, "test_double": bool(_TEST_DOUBLE.search(str(model or "unknown")))})
                    m["sessions"] += 1
                    m["messages"] += int(n or 0)
                    for stamp in (created, updated):
                        iso = _iso(stamp)
                        if iso:
                            m["first"] = iso if not m["first"] or iso < m["first"] else m["first"]
                            m["last"] = iso if not m["last"] or iso > m["last"] else m["last"]
                    info["sessions"] += 1
                    sessions += 1
                for et, n in cur.execute("select event_type, count(*) from session_events group by event_type"):
                    events[str(et)] += int(n)
                for st, n in cur.execute("select status, count(*) from background_runs group by status"):
                    runs[str(st)] += int(n)
                for st, n in cur.execute("select status, count(*) from graph_runs group by status"):
                    graphs[str(st)] += int(n)
            except sqlite3.Error as exc:
                info["error"] = f"{type(exc).__name__}: {exc}"[:120]
            finally:
                con.close()
        per_ws.append(info)
    tool_rows = []
    for name, c in tools.items():
        total = sum(c.values())
        bad = total - c.get("ok", 0)
        tool_rows.append({"tool": name, "calls": total, "not_ok": bad, "error_rate": bad / total if total else None})
    tool_rows.sort(key=lambda r: -r["calls"])
    user_msgs, dones = events.get("user_message", 0), events.get("done", 0)
    return {"workspaces": per_ws, "sessions": sessions,
            "models": sorted(({"model": k, **v} for k, v in models.items()), key=lambda r: -r["sessions"]),
            "tools": tool_rows, "tool_calls": sum(r["calls"] for r in tool_rows), "per_day": dict(sorted(per_day.items())),
            "decisions": dict(decisions), "event_types": dict(events.most_common()),
            "turn_completion": {"user_messages": user_msgs, "done": dones, "ratio": (dones / user_msgs) if user_msgs else None,
                                "note": "a user message without a `done` ended another way: an error, an interruption, a crash"},
            "background_runs": dict(runs), "graph_runs": dict(graphs),
            "tool_calls_per_user_message": (sum(r["calls"] for r in tool_rows) / user_msgs) if user_msgs else None}


_LOG_PATTERNS = (
    ("429 retries", r"Transient status 429"), ("provider gave up (429)", r"Provider kept rejecting"),
    ("floor let an unverified turn end", r"allowing unverified finish|Verification floor exhausted"),
    ("stream stalls", r"chunk_stall"), ("round ended with nothing", r"ended with nothing"),
    ("path jail refusals", r"outside workspace"), ("gate refusals", r"Blocked by the harness gate"),
    ("no approver", r"NO_APPROVER"), ("tracebacks", r"Traceback"),
)


def runtime_log_data(workspaces: list[Path]) -> dict[str, Any]:
    """Counts of notable harness events in each workspace's `.agent/runtime.log`. The lines themselves are never returned."""
    total: collections.Counter[str] = collections.Counter()
    per_ws = []
    for ws in workspaces:
        log = ws / ".agent" / "runtime.log"
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts = {label: len(re.findall(pat, text)) for label, pat in _LOG_PATTERNS}
        counts = {k: v for k, v in counts.items() if v}
        total.update(counts)
        per_ws.append({"workspace": tilde(ws), "lines": text.count("\n"), "events": counts})
    return {"total": dict(total), "workspaces": per_ws}


# ── harness ────────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def disk_data(path: Path | None = None) -> dict[str, Any]:
    """Free space where the agent and the benchmark write. A full disk makes writes fail, which the judge scores as the agent doing nothing."""
    try:
        u = shutil.disk_usage(path or _home())
        return {"free_gb": round(u.free / 1073741824, 2), "total_gb": round(u.total / 1073741824, 1), "used_pct": round(100 * u.used / u.total, 1)}
    except OSError:
        return {}


def harness_data(root: Path) -> dict[str, Any]:
    """The checkout being shown, the flags a session started now would get, and the merged pull requests (from git, offline)."""
    out: dict[str, Any] = {"checkout": tilde(root), "sha": _git(root, "rev-parse", "--short", "HEAD") or "unknown",
                           "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown",
                           "dirty": bool(_git(root, "status", "--porcelain")), "flags": {}, "merged": [], "disk": disk_data()}
    try:
        from wisp.config import WispConfig
        from wisp.core.reasoning.decision import parse_modes

        cfg = WispConfig()
        modes = parse_modes(cfg.reasoning_core, cfg.reasoning_core_rules)
        out["flags"] = {
            "reasoning core: default": cfg.reasoning_core,
            **{f"reasoning core: {r}": modes.for_rule(r).value for r in ("R1", "R2", "R3", "R4", "R5") if r in _known_rules()},
            "invariant gates": cfg.invariant_gates, "dependency lock": cfg.dependency_lock,
            "tool profile": cfg.tool_profile, "verification loop": str(getattr(cfg, "verification_loop", "?")),
            "permission mode": str(getattr(cfg.permission_mode, "value", cfg.permission_mode)),
        }
        out["flags_note"] = "what a session started now, with this environment and config, would use"
    except Exception as exc:  # noqa: BLE001 — the harness may be mid-change; show the rest
        out["flags_error"] = f"{type(exc).__name__}: {exc}"[:160]
    raw = _git(root, "log", "--merges", "-n", "60", "--format=%h%x09%cs%x09%s")
    for line in raw.splitlines():
        parts = line.split("\t", 2)
        m = re.match(r"Merge pull request #(\d+) from [^/\s]+/(\S+)", parts[2]) if len(parts) == 3 else None
        if m:
            out["merged"].append({"sha": parts[0], "date": parts[1], "pr": int(m.group(1)), "branch": m.group(2)})
    return out


def _known_rules() -> tuple[str, ...]:
    try:
        from wisp.core.reasoning.decision import RULES

        return tuple(RULES)
    except Exception:  # noqa: BLE001
        return ("R1", "R2", "R3", "R4")


def overhead_data(workspace: Path) -> dict[str, Any]:
    """Tokens every request carries before any history: the core tool schemas, the skill tool schemas, and the skills menu (serialised size / 4)."""
    out: dict[str, Any] = {"workspace": tilde(workspace)}
    try:
        from wisp.extensions.skills import SkillExtension
        from wisp.skills import discover_skills
        from wisp.tools.profile import CORE_TOOLS
        from wisp.tools.registry import TOOL_SCHEMAS

        def name(t: Any) -> str:
            return str((t.get("function", t) or {}).get("name"))

        core = [t for t in TOOL_SCHEMAS if name(t) in CORE_TOOLS]
        ext = SkillExtension(str(workspace))
        ext.start()
        skills = discover_skills(str(workspace))
        menu = sum(len(f"- {s.name}: {s.description}") + 16 + len((s.instructions or "")[:200]) for s in skills)
        out.update(core_tools=len(core), core_tokens=len(json.dumps(core)) // 4, skill_tools=len(ext.tools()), skill_tool_tokens=len(json.dumps(ext.tools())) // 4,
                   skills=len(skills), skills_menu_tokens=menu // 4)
        out["total_tokens"] = out["core_tokens"] + out["skill_tool_tokens"] + out["skills_menu_tokens"]
        out["note"] = "estimates (serialised size / 4); the doctor line counts only the core tools"
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:160]
    return out


# ── findings and learning ─────────────────────────────────────────────────

_ROW = re.compile(r"^\|\s*((?:O|I|R|C|J)-\d+)\s*\|(.*)\|\s*$")


def findings_data(root: Path) -> dict[str, Any]:
    """The findings registers (docs/harness/field-observations-*.md and findings-*.md), as id, one-line summary, severity and status."""
    items: list[dict[str, Any]] = []
    docs = root / "docs" / "harness"
    try:
        files = sorted(docs.glob("field-observations-*.md")) + sorted(docs.glob("findings-*.md"))
    except OSError:
        files = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _ROW.match(line)
            if not m:
                continue
            cells = [c.strip() for c in m.group(2).split("|")]
            status_cell = cells[-1] if cells else ""
            text = " ".join(cells)
            summary = re.sub(r"[*`]", "", cells[1] if len(cells) > 1 else cells[0])[:220]
            low = status_cell.lower()
            state = "fixed" if ("fixed" in low or "merged" in low) else ("refuted" if "refuted" in low else ("n/a" if low.startswith("n/a") else "open"))
            items.append({"id": m.group(1), "source": f.name, "summary": summary, "status": state, "status_text": re.sub(r"[*`]", "", status_cell)[:160],
                          "has_evidence_word": bool(re.search(r"verified|observed|read|reproduced", text, re.I))})
    counts = collections.Counter(i["status"] for i in items)
    return {"items": items, "counts": dict(counts), "total": len(items)}


def learning_data(workspaces: list[Path]) -> dict[str, Any]:
    """Whether the agent keeps what it learns: fact store, backups, session summaries, captured skills. Counts and dates only."""
    cfg = _home() / ".config" / "wisp"
    out: dict[str, Any] = {"facts": None, "workspace_fact_buckets": None, "fact_store_bytes": None, "fact_store_modified": None, "backups": [], "session_summaries": None,
                           "captured_skills": {"total": 0, "by_day": {}, "by_workspace": {}}, "remember": {"calls": 0, "ok": 0, "error": 0}}
    mem = cfg / "memory.json"
    try:
        d = json.loads(mem.read_text(encoding="utf-8"))
        out["facts"] = len(d.get("global_facts", [])) + sum(len(v) for v in d.get("workspace_facts", {}).values())
        out["workspace_fact_buckets"] = len(d.get("workspace_facts", {}))
        out["fact_store_bytes"] = mem.stat().st_size
        out["fact_store_modified"] = _iso(mem.stat().st_mtime)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        for b in sorted(cfg.glob("memory.json.bak-*")):
            try:
                bd = json.loads(b.read_text(encoding="utf-8"))
                n = len(bd.get("global_facts", [])) + sum(len(v) for v in bd.get("workspace_facts", {}).values())
            except (OSError, ValueError, AttributeError):
                n = None
            out["backups"].append({"name": b.name, "facts": n, "bytes": b.stat().st_size})
    except OSError:
        pass
    try:
        out["session_summaries"] = sum(1 for _ in (cfg / "agent_memory" / "sessions.jsonl").open(encoding="utf-8", errors="replace"))
    except OSError:
        pass
    by_day: collections.Counter[str] = collections.Counter()
    by_ws: collections.Counter[str] = collections.Counter()
    for ws in workspaces + [_home()]:
        try:
            for f in (ws / ".wisp" / "skills" / "auto").glob("*/SKILL.md"):
                by_day[_day(f.stat().st_mtime) or "?"] += 1
                by_ws[tilde(ws)] += 1
        except OSError:
            continue
    out["captured_skills"] = {"total": sum(by_day.values()), "by_day": dict(sorted(by_day.items())), "by_workspace": dict(by_ws)}
    for ws in workspaces:
        for d in _audit_rows(ws / ".wisp" / "audit.jsonl"):
            if d.get("tool") == "remember":
                out["remember"]["calls"] += 1
                out["remember"]["ok" if d.get("result_status") == "ok" else "error"] += 1
    try:
        out["global_skills"] = sum(1 for p in (_home() / ".agents" / "skills").iterdir() if p.is_dir())
    except OSError:
        out["global_skills"] = None
    return out
