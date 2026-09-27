"""ADR-0064: the operator's plan commands see the plan the agent wrote, and a plan's workspace
has one resolver, inside `PlanStore`.

`PHASE_PLAN_CLI.md` §2 measured the following:

* The agent keys a plan by `session["workspace"]` verbatim: one directory had five keys under five
  spellings.
* `wisp plan` / `wisp progress` / `wisp plan list` / `wisp plan abort` query `"."`, so they show
  nothing in the default configuration.
* Rotation kept the ten newest plans across **all** workspaces.

What this file holds:

* **The operator sees the agent's plan** (driven). The agent writes through the production tool path
  (`registry.execute_tool("plan_task", …, WispConfig().workspace)`). Each command then runs as the real
  console entry point (`python -m wisp …`) in that workspace, against the real store in a private
  `HOME`. The RED reason is the one ADR-0064 names: the store key is the agent's workspace, the CLI
  asked for `"."`, and the test asserts that key before asserting the miss.
* **Any spelling is one workspace**: a symlink, a trailing slash and `WISP_WORKSPACE=.`.
* **Rotation is per workspace**: ten plans elsewhere leave this workspace's plan in place, and an
  eleventh plan *here* still rotates the oldest here away.
* **Old plans stay readable**: a file written under the old scheme (`"."`, or an unresolved path) is
  found, and is not rewritten by being read.
* **One resolver**: no production call passes the literal `"."` to the store's readers (AST, F73).

**Floors** (F81): every CLI test asserts that the agent's plan file exists before asserting what the
CLI shows. The AST test asserts that it found the store's readers.
**F92**: the tests assert what the operator reads (the goal, the status on disk), not how the
commands format it.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
GOAL = "add a json flag to report"
TASKS = "1. [low] Read report.py\n2. [medium] Add the flag — deps: 1"

_AGENT_WRITE = r'''
import json, sys
from wisp.config import WispConfig
from wisp.tools.registry import execute_tool
r = json.loads(execute_tool("plan_task", {"goal": sys.argv[1], "tasks": sys.argv[2]},
                            WispConfig().workspace))
assert r["status"] == "ok", r
'''


def _env(home: pathlib.Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and not k.startswith("WISP_")
           and k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO), **extra)
    return env


def _py(args: list[str], cwd: pathlib.Path, env: dict[str, str]) -> str:
    proc = subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


@pytest.fixture
def world(tmp_path):
    root = tmp_path.resolve()
    home, project = root / "home", root / "project"
    home.mkdir()
    project.mkdir()
    return root, home, project


def _plans(home: pathlib.Path) -> list[dict]:
    d = home / ".config" / "wisp" / "plans"
    return [json.loads(p.read_text()) for p in sorted(d.glob("plan-*.json"))]


def _agent_writes(home, cwd, **extra) -> dict:
    _py(["-c", _AGENT_WRITE, GOAL, TASKS], cwd, _env(home, **extra))
    plans = _plans(home)
    assert len(plans) == 1, "floor: the agent's plan must be in the store"
    return plans[0]


class TestTheOperatorSeesTheAgentsPlan:
    def test_plan_progress_and_list_show_it(self, world) -> None:
        _root, home, project = world
        stored = _agent_writes(home, project)
        assert stored["workspace"] == str(project), "the agent keys by its workspace, not '.'"
        for args in (["plan"], ["progress"], ["plan", "list"]):
            out = _py(["-m", "wisp", *args], project, _env(home))
            assert GOAL[:30] in out, f"wisp {' '.join(args)} did not show the agent's plan:\n{out}"

    def test_plan_abort_aborts_it(self, world) -> None:
        _root, home, project = world
        _agent_writes(home, project)
        _py(["-m", "wisp", "plan", "abort"], project, _env(home))
        assert _plans(home)[0]["status"] == "aborted"

    @pytest.mark.parametrize("spelling", ["link", "trailing-slash", "dot"])
    def test_every_spelling_is_one_workspace(self, world, spelling) -> None:
        root, home, project = world
        link = root / "link"
        link.symlink_to(project)
        setting = {"link": str(link), "trailing-slash": f"{project}/", "dot": "."}[spelling]
        _agent_writes(home, project, WISP_WORKSPACE=setting)
        out = _py(["-m", "wisp", "plan"], project, _env(home))
        assert GOAL[:30] in out


class TestRotationIsPerWorkspace:
    def test_ten_plans_elsewhere_leave_this_one(self, world) -> None:
        root, home, project = world
        _agent_writes(home, project)
        other = root / "other"
        other.mkdir()
        _py(["-c", "import sys, time\nfrom wisp.planner import Plan, PlanStore\n"
                   "for i in range(10):\n    time.sleep(0.01)\n"
                   "    PlanStore().save(Plan(goal=f'elsewhere {i}', workspace=sys.argv[1]))",
             str(other)], project, _env(home))
        assert len(_plans(home)) == 11
        out = _py(["-m", "wisp", "plan"], project, _env(home))
        assert GOAL[:30] in out

    def test_the_eleventh_plan_here_still_rotates_the_oldest_here(self, world) -> None:
        _root, home, project = world
        first = _agent_writes(home, project)
        _py(["-c", "import sys, time\nfrom wisp.planner import Plan, PlanStore\n"
                   "for i in range(10):\n    time.sleep(0.01)\n"
                   "    PlanStore().save(Plan(goal=f'here {i}', workspace=sys.argv[1]))",
             str(project)], project, _env(home))
        ids = [p["id"] for p in _plans(home)]
        assert len(ids) == 10 and first["id"] not in ids


class TestOldPlansStayReadable:
    @pytest.mark.parametrize("legacy_key", [".", "@PROJECT/"])
    def test_a_legacy_key_is_found_and_not_rewritten(self, world, legacy_key) -> None:
        _root, home, project = world
        d = home / ".config" / "wisp" / "plans"
        d.mkdir(parents=True)
        f = d / "plan-0123456789ab.json"
        f.write_text(json.dumps({
            "id": "plan-0123456789ab", "goal": GOAL, "status": "active",
            "workspace": legacy_key.replace("@PROJECT", str(project)),
            "tasks": [{"id": "task-1", "description": "Read report.py", "status": "pending"}],
            "created_at": "2026-09-01T00:00:00+00:00", "updated_at": "2026-09-01T00:00:00+00:00",
        }))
        before = f.read_bytes()
        out = _py(["-m", "wisp", "plan"], project, _env(home))
        assert GOAL[:30] in out
        assert f.read_bytes() == before, "reading a plan must not rewrite it"


class TestNoPlanNoChange:
    def test_an_empty_store_says_so(self, world) -> None:
        _root, home, project = world
        shown = _py(["-m", "wisp", "plan"], project, _env(home))
        listed = _py(["-m", "wisp", "plan", "list"], project, _env(home))
        assert "No active plan." in shown
        assert "No plans found." in listed


_READERS = {"load_active", "list_plans"}


class TestOneResolver:
    def test_no_reader_is_passed_a_literal_dot(self) -> None:
        found, offenders = 0, []
        for path in (REPO / "wisp").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name not in _READERS:
                    continue
                found += 1
                if any(isinstance(a, ast.Constant) and a.value == "." for a in node.args):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
        assert found >= 3, "floor: the store's readers must be found"
        assert offenders == [], ("ADR-0064 R2: a plan reader is keyed on the literal '.', not the "
                                 "agent's workspace: " + ", ".join(offenders))
