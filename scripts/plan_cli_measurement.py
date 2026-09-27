"""Measure the plan CLI: what `wisp plan` / `wisp progress` / `wisp plan list` / `wisp plan abort`
show after an agent writes a plan, how the agent keys that plan, and what each candidate would show.

`PHASE_PLAN_CLI.md` §2 is this script's output. Every child runs with a **private `HOME`**, so the
operator's real `~/.config/wisp/plans` is never read or written (`PLANS_DIR` binds at import).

    env -u PYTHONPATH .venv/bin/python scripts/plan_cli_measurement.py

1. **The key.** The agent's write goes `session["workspace"]` → the executor → `registry.execute_tool`
   → `tool_plan_task(workspace=…)` → `Plan.workspace`. `session["workspace"]` is
   `WispConfig().workspace`. It is measured under each way an operator sets it: the default (the
   cwd), `WISP_WORKSPACE=.`, a relative path, a symlink, and a trailing slash.
2. **Today's CLI.** Each of the four commands runs as the real console entry point
   (`python -m wisp …`) in the workspace, after the agent's write.
3. **The candidates, driven.** The readers are rebound in the child to each candidate's key, and
   rotation is driven by saving ten plans in another workspace.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
TASKS = "1. [low] Read report.py — files: report.py\n2. [medium] Add --json — deps: 1"

AGENT_WRITE = r'''
import json, sys
from wisp.config import WispConfig
from wisp.tools.registry import execute_tool
ws = WispConfig().workspace            # what the CLI/REPL puts in session["workspace"]
r = json.loads(execute_tool("plan_task", {"goal": "add --json", "tasks": sys.argv[1]}, ws))
from wisp.planner import PlanStore
key = PlanStore().list_all()[0]["workspace"]
print("OUT " + json.dumps({"session_workspace": ws, "stored_key": key, "tool_status": r["status"]}))
'''

CANDIDATE = r'''
import json, os, sys, io, contextlib
mode = sys.argv[1]
from wisp.config import WispConfig, safe_getcwd
import wisp.planner as planner
from wisp.planner import PlanStore, Plan
import wisp.__main__ as m
if mode in ("c1", "c2"):
    key = WispConfig().workspace                     # the agent's own resolver
    real = PlanStore.load_active
    PlanStore.load_active = lambda self, ws: real(self, key if ws == "." else ws)
    import wisp.progress as pr
    real_list = pr.list_plans
    pr.list_plans = lambda ws="": real_list(key if ws == "." else ws)
if mode == "c2":
    def _rotate_per_workspace(self):
        with planner._PLAN_LOCK:
            by_ws = {}
            for p in planner.PLANS_DIR.glob("*.json"):
                try:
                    by_ws.setdefault(json.loads(p.read_text()).get("workspace", ""), []).append(p)
                except Exception:
                    pass
            for paths in by_ws.values():
                for old in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[planner._MAX_PLANS:]:
                    old.unlink()
    PlanStore._rotate = _rotate_per_workspace
def run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        (m.cmd_plan if args[0] == "plan" else m.cmd_progress)(args[1:])
    return buf.getvalue().strip()
out = {"plan": run(["plan"]), "progress": run(["progress"]), "plan list": run(["plan", "list"])}
import time
for i in range(10):
    time.sleep(0.01)
    PlanStore().save(Plan(goal=f"elsewhere {i}", workspace=sys.argv[2]))
out["after_10_plans_elsewhere: plan"] = run(["plan"])
print("OUT " + json.dumps(out))
'''


def _env(home: pathlib.Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and not k.startswith("WISP_")
           and k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO))
    env.update(extra or {})
    return env


def _run(args: list[str], cwd: pathlib.Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=180)


def _out(proc: subprocess.CompletedProcess) -> dict:
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("OUT ")), None)
    if line is None:
        raise SystemExit(f"probe failed ({proc.returncode}):\n{proc.stderr[-2500:]}")
    return json.loads(line[4:])


def _fresh() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    root = pathlib.Path(tempfile.mkdtemp(prefix="wisp-plancli-")).resolve()
    home, ws = root / "home", root / "project"
    home.mkdir()
    (ws / "sub").mkdir(parents=True)
    return root, home, ws


def _mask(text: str, root: pathlib.Path) -> str:
    return text.replace(str(root), "<tmp>")


def measure_keys() -> list[dict]:
    rows = []
    for label, cwd_rel, setting in (
        ("default (cwd = the project)", ".", None),
        ("WISP_WORKSPACE=.", ".", "."),
        ("WISP_WORKSPACE=sub/.. (relative)", ".", "sub/.."),
        ("WISP_WORKSPACE=<project>/ (trailing slash)", ".", "@WS/"),
        ("WISP_WORKSPACE=<symlink to project>", ".", "@LINK"),
        ("default, started in project/sub", "sub", None),
    ):
        root, home, ws = _fresh()
        link = root / "link"
        link.symlink_to(ws)
        extra = {}
        if setting is not None:
            extra["WISP_WORKSPACE"] = setting.replace("@WS", str(ws)).replace("@LINK", str(link))
        cwd = ws / cwd_rel
        r = _out(_run(["-c", AGENT_WRITE, TASKS], cwd, _env(home, extra)))
        cli = {name: _mask(_run(["-m", "wisp", *args], cwd, _env(home, extra)).stdout.strip(), root)
               for name, args in (("wisp plan", ["plan"]), ("wisp progress", ["progress"]),
                                  ("wisp plan list", ["plan", "list"]))}
        rows.append({"case": label,
                     "stored_key": _mask(r["stored_key"], root),
                     "key_is_resolved_absolute": r["stored_key"] == str(pathlib.Path(r["stored_key"]).resolve()),
                     "cli_today": cli})
    return rows


def measure_abort() -> dict:
    root, home, ws = _fresh()
    _out(_run(["-c", AGENT_WRITE, TASKS], ws, _env(home)))
    out = _run(["-m", "wisp", "plan", "abort"], ws, _env(home)).stdout.strip()
    status = json.loads(next((home / ".config/wisp/plans").glob("plan-*.json")).read_text())["status"]
    return {"wisp plan abort": out, "agent_plan_status_after": status}


def measure_candidates() -> dict:
    res = {}
    for mode in ("today", "c1", "c2"):
        root, home, ws = _fresh()
        other = root / "other"
        other.mkdir()
        _out(_run(["-c", AGENT_WRITE, TASKS], ws, _env(home)))
        res[mode] = {k: _mask(v, root) for k, v in
                     _out(_run(["-c", CANDIDATE, mode, str(other)], ws, _env(home))).items()}
    return res


def main() -> None:
    print(json.dumps({"keys_and_today": measure_keys(), "abort": measure_abort(),
                      "candidates": measure_candidates()}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
