"""The agent can run the GitHub workflow itself — through typed tools, never through bash.

`run_bash` is confined to the sandbox (no `gh`, no credentials, no network), so before these tools
an agent that needed to look at a PR, read its CI, comment, close or merge it could only hand the
command to the operator. Six read tools and four gated writes close that gap while keeping the
sandbox shut:

    reads   (no approval)   git_log  git_fetch  gh_pr_view  gh_pr_list  gh_pr_checks  gh_run_failed_logs
    writes  (asked, once)   gh_pr_comment  gh_pr_close  gh_pr_merge  git_sync_base

Held here:

* **argv, never a shell** — every call is a fixed argument list; a number is a positive int or the
  call is refused; free text (a comment body) is one argument and can never become a flag;
* **no `repo` parameter, anywhere** — `gh` runs in the workspace, so it can only act on the
  workspace's own remote. A model cannot aim `gh pr close` at another repository;
* **merge needs proof** — open, not a draft, mergeable, at least one check, every check green, no
  requested changes. Never `--admin`, `--auto`, `--squash`, `--delete-branch`;
* **sync merges, never rebases**, refuses a dirty tree, and leaves conflicts for the agent to resolve;
* **no push to main/master**;
* **approval** — through the production executor, writes are asked once and refused without a
  handler; reads run unprompted; subagent children get reads only.
"""
from __future__ import annotations

import inspect
import json

import pytest

import wisp.git_context as gc
from wisp.core.contracts import ToolRisk, risk_for_tool
from wisp.tools import git as tools

READS = ("git_log", "git_fetch", "gh_pr_view", "gh_pr_list", "gh_pr_checks", "gh_run_failed_logs")
WRITES = ("gh_pr_comment", "gh_pr_close", "gh_pr_merge", "git_sync_base")
NUMBERED = ("gh_pr_view", "gh_pr_checks", "gh_pr_comment", "gh_pr_close", "gh_pr_merge")

_CALLS = {
    "git_log": {}, "git_fetch": {}, "gh_pr_view": {"number": 7}, "gh_pr_list": {},
    "gh_pr_checks": {"number": 7}, "gh_run_failed_logs": {"run_id": 99},
    "gh_pr_comment": {"number": 7, "body": "hi"}, "gh_pr_close": {"number": 7},
    "gh_pr_merge": {"number": 7}, "git_sync_base": {},
}


class Fake:
    """Stands in for `git_context._run_git`; records (argv, command) and answers by prefix."""

    def __init__(self, monkeypatch, answers=None):
        self.calls: list[tuple[list[str], str]] = []
        self.answers = answers or {}
        monkeypatch.setattr(gc, "_run_git", self)

    def __call__(self, args, cwd, timeout=10, command="git"):
        self.calls.append((list(args), command))
        for prefix, reply in self.answers.items():
            if list(args)[: len(prefix)] == list(prefix):
                return reply
        return 0, "", ""

    @property
    def argvs(self):
        return [a for a, _ in self.calls]


def _pr(**over):
    base = {"state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE", "reviewDecision": "",
            "baseRefName": "main",
            "statusCheckRollup": [{"name": "test-python", "status": "COMPLETED", "conclusion": "SUCCESS"},
                                  {"name": "qa", "status": "COMPLETED", "conclusion": "SKIPPED"}]}
    base.update(over)
    return base


def _view(pr):
    return {("pr", "view", "7"): (0, json.dumps(pr), "")}


# ── argument lists ─────────────────────────────────────────────────────────────

def test_reads_are_fixed_argument_lists(monkeypatch):
    f = Fake(monkeypatch)
    tools.tool_git_log(limit=5, path="wisp/x.py")
    tools.tool_git_fetch()
    tools.tool_gh_pr_view(7)
    tools.tool_gh_pr_list(state="merged", limit=3)
    tools.tool_gh_pr_checks(7)
    tools.tool_gh_run_failed_logs(99)
    assert f.calls[0] == (["log", "-5", "--oneline", "--decorate", "--", "wisp/x.py"], "git")
    assert f.calls[1] == (["fetch", "origin"], "git")
    assert f.calls[2][1] == "gh" and f.calls[2][0][:3] == ["pr", "view", "7"] and "--json" in f.calls[2][0]
    assert f.calls[3] == (["pr", "list", "--state", "merged", "--limit", "3"], "gh")
    assert f.calls[4] == (["pr", "checks", "7"], "gh")
    assert f.calls[5] == (["run", "view", "99", "--log-failed"], "gh")


def test_writes_are_fixed_argument_lists(monkeypatch):
    f = Fake(monkeypatch)
    tools.tool_gh_pr_comment(7, "looks good")
    tools.tool_gh_pr_close(7, "superseded")
    tools.tool_gh_pr_close(7)
    assert f.calls[0] == (["pr", "comment", "7", "--body", "looks good"], "gh")
    assert f.calls[1] == (["pr", "close", "7", "--comment", "superseded"], "gh")
    assert f.calls[2] == (["pr", "close", "7"], "gh")


def test_a_body_that_looks_like_a_flag_stays_one_argument(monkeypatch):
    f = Fake(monkeypatch)
    hostile = "--repo other/repo --admin"
    tools.tool_gh_pr_comment(7, hostile)
    tools.tool_gh_pr_close(7, hostile)
    assert f.argvs[0][-1] == hostile and f.argvs[0].count("--body") == 1
    assert f.argvs[1][-1] == hostile and f.argvs[1].count("--comment") == 1
    assert all("--repo" not in a[:-1] for a in f.argvs)


@pytest.mark.parametrize("bad", ["7; rm -rf /", "-1", 0, -3, "abc", "", True, None, 7.5, "--repo x"])
@pytest.mark.parametrize("name", NUMBERED)
def test_a_number_is_a_positive_int_or_the_call_is_refused(monkeypatch, name, bad):
    f = Fake(monkeypatch)
    kwargs = {"body": "b"} if name == "gh_pr_comment" else {}
    out = getattr(tools, f"tool_{name}")(bad, **kwargs)
    assert out.startswith("Error"), out
    assert f.calls == [], "a refused number must never reach gh"


def test_a_numeric_string_is_accepted(monkeypatch):
    f = Fake(monkeypatch)
    tools.tool_gh_pr_view("12")
    assert f.argvs[0][:3] == ["pr", "view", "12"]


def test_list_state_and_limit_are_bounded(monkeypatch):
    f = Fake(monkeypatch)
    assert tools.tool_gh_pr_list(state="; rm").startswith("Error")
    tools.tool_gh_pr_list(limit=10_000)
    tools.tool_git_log(limit=10_000)
    assert f.argvs[0][-1] == "100"
    assert f.argvs[1][1] == "-100"


def test_a_comment_needs_a_body(monkeypatch):
    f = Fake(monkeypatch)
    assert tools.tool_gh_pr_comment(7, "   ").startswith("Error")
    assert f.calls == []


# ── the workspace's own remote, and nothing else ───────────────────────────────

@pytest.mark.parametrize("name", READS + WRITES)
def test_no_tool_takes_a_repo(name):
    fn = getattr(tools, f"tool_{name}")
    assert not [p for p in inspect.signature(fn).parameters if "repo" in p.lower()]
    with pytest.raises(TypeError):
        fn(**{**_CALLS[name], "repo": "other/repo"})


def test_no_schema_offers_a_repo():
    from wisp.tools.registry import TOOL_SCHEMAS

    by = {s["function"]["name"]: s["function"] for s in TOOL_SCHEMAS}
    for name in READS + WRITES:
        props = by[name]["parameters"].get("properties", {})
        assert not [p for p in props if "repo" in p.lower()], name


def test_gh_output_is_never_asked_to_run_against_another_repo(monkeypatch):
    f = Fake(monkeypatch, {**_view(_pr())})
    for name in READS + WRITES:
        getattr(tools, f"tool_{name}")(**_CALLS[name])
    assert all("--repo" not in a and "-R" not in a for a in f.argvs)


# ── merge needs proof ──────────────────────────────────────────────────────────

def test_a_green_pr_merges_with_a_plain_merge_commit(monkeypatch):
    f = Fake(monkeypatch, _view(_pr()))
    tools.tool_gh_pr_merge(7)
    assert f.argvs[-1] == ["pr", "merge", "7", "--merge"]
    flat = {a for argv in f.argvs for a in argv}
    assert not flat & {"--admin", "--auto", "--squash", "--rebase", "--delete-branch"}


@pytest.mark.parametrize("label,pr", [
    ("closed", _pr(state="CLOSED")),
    ("merged", _pr(state="MERGED")),
    ("draft", _pr(isDraft=True)),
    ("conflicting", _pr(mergeable="CONFLICTING")),
    ("unknown mergeability", _pr(mergeable="UNKNOWN")),
    ("no checks at all", _pr(statusCheckRollup=[])),
    ("a failing check", _pr(statusCheckRollup=[{"name": "a", "status": "COMPLETED", "conclusion": "SUCCESS"},
                                                {"name": "b", "status": "COMPLETED", "conclusion": "FAILURE"}])),
    ("a pending check", _pr(statusCheckRollup=[{"name": "a", "status": "IN_PROGRESS", "conclusion": ""}])),
    ("a check not completed that carries a stale green conclusion",
     _pr(statusCheckRollup=[{"name": "a", "status": "IN_PROGRESS", "conclusion": "SUCCESS"}])),
    ("a cancelled check", _pr(statusCheckRollup=[{"name": "a", "status": "COMPLETED", "conclusion": "CANCELLED"}])),
    ("changes requested", _pr(reviewDecision="CHANGES_REQUESTED")),
])
def test_merge_is_refused_without_proof(monkeypatch, label, pr):
    f = Fake(monkeypatch, _view(pr))
    out = tools.tool_gh_pr_merge(7)
    assert out.startswith("Refused"), (label, out)
    assert not [a for a in f.argvs if a[:2] == ["pr", "merge"]], f"{label}: merge must not run"


def test_merge_is_refused_when_the_pr_cannot_be_read(monkeypatch):
    f = Fake(monkeypatch, {("pr", "view", "7"): (1, "", "not found")})
    assert tools.tool_gh_pr_merge(7).startswith("Error")
    assert not [a for a in f.argvs if a[:2] == ["pr", "merge"]]
    f = Fake(monkeypatch, {("pr", "view", "7"): (0, "not json", "")})
    assert tools.tool_gh_pr_merge(7).startswith("Error")


def test_status_contexts_count_too(monkeypatch):
    """Legacy commit statuses carry `state`, not `conclusion`."""
    ok = _pr(statusCheckRollup=[{"context": "ci/x", "state": "SUCCESS"}])
    bad = _pr(statusCheckRollup=[{"context": "ci/x", "state": "FAILURE"}])
    Fake(monkeypatch, _view(ok))
    assert not tools.tool_gh_pr_merge(7).startswith("Refused")
    Fake(monkeypatch, _view(bad))
    assert tools.tool_gh_pr_merge(7).startswith("Refused")


# ── sync: merge the base in, never rebase ──────────────────────────────────────

def test_sync_fetches_then_merges_the_base(monkeypatch):
    f = Fake(monkeypatch)
    tools.tool_git_sync_base("main")
    assert f.argvs == [["status", "--porcelain", "--untracked-files=no"],
                       ["fetch", "origin", "main"],
                       ["merge", "--no-edit", "origin/main"]]


def test_sync_refuses_a_dirty_tree_before_touching_anything(monkeypatch):
    f = Fake(monkeypatch, {("status",): (0, " M wisp/x.py\n", "")})
    out = tools.tool_git_sync_base("main")
    assert out.startswith("Refused") and "uncommitted" in out
    assert f.argvs == [["status", "--porcelain", "--untracked-files=no"]]


@pytest.mark.parametrize("base", ["--upstream", "-x", "a b", "x..y", "", "a;b", "main\nfoo", "../main"])
def test_sync_refuses_a_hostile_base(monkeypatch, base):
    f = Fake(monkeypatch)
    assert tools.tool_git_sync_base(base).startswith("Error")
    assert f.calls == []


def test_conflicts_are_reported_and_left_for_the_agent(monkeypatch):
    f = Fake(monkeypatch, {("merge",): (1, "CONFLICT (content): Merge conflict in wisp/a.py", ""),
                           ("diff",): (0, "wisp/a.py\nwisp/b.py\n", "")})
    out = tools.tool_git_sync_base("main")
    assert "wisp/a.py" in out and "wisp/b.py" in out and "conflict" in out.lower()
    flat = [a for argv in f.argvs for a in argv]
    assert "--abort" not in flat and "rebase" not in flat and "reset" not in flat


def test_no_tool_ever_rebases_or_forces(monkeypatch):
    f = Fake(monkeypatch, _view(_pr()))
    for name in READS + WRITES:
        getattr(tools, f"tool_{name}")(**_CALLS[name])
    flat = {a for argv in f.argvs for a in argv}
    assert not flat & {"rebase", "--force", "-f", "--force-with-lease", "reset", "--hard"}


# ── push never lands on the default branch ─────────────────────────────────────

@pytest.mark.parametrize("branch", ["main", "master"])
def test_push_refuses_the_default_branch(monkeypatch, branch):
    f = Fake(monkeypatch, {("rev-parse", "--git-dir"): (0, ".git", ""),
                           ("rev-parse", "--abbrev-ref"): (0, branch + "\n", "")})
    out = tools.tool_git_push()
    assert out.startswith("Error") and branch in out
    assert not [a for a in f.argvs if a[:1] == ["push"]]


def test_push_from_a_feature_branch_still_works(monkeypatch):
    f = Fake(monkeypatch, {("rev-parse", "--git-dir"): (0, ".git", ""),
                           ("rev-parse", "--abbrev-ref"): (0, "feat/x\n", "")})
    tools.tool_git_push(set_upstream=True)
    assert ["push", "-u", "origin", "HEAD"] in f.argvs


# ── output handling ────────────────────────────────────────────────────────────

def test_pending_or_failing_checks_are_information_not_an_error(monkeypatch):
    """`gh pr checks` exits 8 while checks are pending and 1 on a failure; the text is the answer."""
    Fake(monkeypatch, {("pr", "checks"): (8, "test-python\tpending\t0\thttps://x", "")})
    assert not tools.tool_gh_pr_checks(7).startswith("Error")
    Fake(monkeypatch, {("pr", "checks"): (1, "qa\tfail\t3s\thttps://x", "")})
    assert "fail" in tools.tool_gh_pr_checks(7)


def test_failed_run_logs_are_capped(monkeypatch):
    Fake(monkeypatch, {("run", "view"): (0, "x" * 500_000, "")})
    out = tools.tool_gh_run_failed_logs(99)
    assert len(out) < 30_000 and "truncated" in out


def test_gh_missing_is_reported_plainly(monkeypatch):
    Fake(monkeypatch, {("pr", "view"): (1, "", "gh not found")})
    assert "gh" in tools.tool_gh_pr_view(7)


# ── classification and the production gate ─────────────────────────────────────

@pytest.mark.parametrize("name", READS)
def test_reads_are_read_risk(name):
    assert risk_for_tool(name) == ToolRisk.READ


@pytest.mark.parametrize("name", WRITES)
def test_writes_are_exec_risk(name):
    assert risk_for_tool(name) == ToolRisk.EXEC


def test_every_tool_has_a_schema_and_an_implementation():
    from wisp.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS

    schemas = {s["function"]["name"] for s in TOOL_SCHEMAS}
    for name in READS + WRITES:
        assert name in schemas, f"{name} has no schema"
        assert name in TOOL_IMPLS, f"{name} has no implementation"


def _executor(tmp_path, mode="auto_edit"):
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor

    cfg = WispConfig().replace(workspace=str(tmp_path), permission_mode=mode, auto_approve=False)
    ex = ToolExecutor(config=cfg, hook_manager=None, mcp=None, file_lock=None,
                      lsp_manager=None, subagent_orchestrator=None, extensions=None)
    ran: list[str] = []

    async def body(name, args, ws):
        ran.append(name)
        return '{"status": "ok", "data": "done"}', 0.0

    ex._execute_tool = body
    return ex, ran


def _flat(ev) -> dict:
    return ev if isinstance(ev, dict) else {"type": str(getattr(ev, "type", "")),
                                            **dict(getattr(ev, "data", {}))}


async def _run(ex, tool, tmp_path, handler) -> list[dict]:
    return [_flat(ev) async for ev in ex.execute(tool, _CALLS[tool], str(tmp_path),
                                                 tool_call_id="c1", approval_handler=handler)]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", WRITES)
async def test_a_write_is_asked_exactly_once_and_runs_on_yes(tmp_path, tool):
    ex, ran = _executor(tmp_path)
    asked: list[str] = []

    async def yes(name, args, reason):
        asked.append(name)
        return True, None

    await _run(ex, tool, tmp_path, yes)
    assert asked == [tool] and ran == [tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", WRITES)
async def test_a_write_does_not_run_on_no(tmp_path, tool):
    ex, ran = _executor(tmp_path)

    async def no(name, args, reason):
        return False, None

    events = await _run(ex, tool, tmp_path, no)
    assert ran == []
    assert "USER_DENIED" in str([e for e in events if e.get("type") == "tool_result"][0].get("result"))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", WRITES)
async def test_a_write_never_runs_unprompted(tmp_path, tool):
    ex, ran = _executor(tmp_path)
    await _run(ex, tool, tmp_path, None)
    assert ran == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto_edit", "read_only"])
@pytest.mark.parametrize("tool", READS)
async def test_a_read_runs_without_asking(tmp_path, tool, mode):
    ex, ran = _executor(tmp_path, mode)
    asked: list[str] = []

    async def handler(name, args, reason):
        asked.append(name)
        return True, None

    await _run(ex, tool, tmp_path, handler)
    assert ran == [tool] and asked == [], f"{tool} must run unprompted in {mode}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", WRITES)
async def test_a_write_does_not_run_in_read_only(tmp_path, tool):
    ex, ran = _executor(tmp_path, "read_only")

    async def yes(name, args, reason):
        return True, None

    await _run(ex, tool, tmp_path, yes)
    assert ran == []


def test_subagent_children_get_the_reads_and_none_of_the_writes():
    from wisp.infra.policy_engine import filter_allowed_for_mode

    offered = filter_allowed_for_mode("auto_edit", ["read_file", *READS, *WRITES])
    assert offered == ["read_file", *READS]


# ── the same logic against real git (a bare remote and two clones in tmp) ─────────

import subprocess  # noqa: E402


def _git(cwd, *args):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture()
def remote_and_clone(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "a.txt").write_text("one\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "first")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "checkout", "-b", "feat/x")
    return seed, clone


def _advance_main(seed, name="b.txt", text="two\n"):
    (seed / name).write_text(text)
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", f"add {name}")
    _git(seed, "push", "origin", "main")


def test_real_sync_brings_the_base_in(remote_and_clone):
    seed, clone = remote_and_clone
    assert "already up to date" in tools.tool_git_sync_base("main", workspace=str(clone)).lower()
    _advance_main(seed)
    out = tools.tool_git_sync_base("main", workspace=str(clone))
    assert not out.startswith(("Error", "Refused")), out
    assert (clone / "b.txt").read_text() == "two\n"


def test_real_sync_leaves_conflicts_for_the_agent(remote_and_clone):
    seed, clone = remote_and_clone
    (clone / "a.txt").write_text("mine\n")
    _git(clone, "commit", "-am", "mine")
    _advance_main(seed, "a.txt", "theirs\n")
    out = tools.tool_git_sync_base("main", workspace=str(clone))
    assert out.startswith("Error") and "a.txt" in out and "conflict" in out.lower()
    assert (clone / ".git" / "MERGE_HEAD").exists(), "the merge must be left in place, not aborted"
    assert "<<<<<<<" in (clone / "a.txt").read_text()


def test_real_sync_refuses_a_dirty_tree(remote_and_clone):
    seed, clone = remote_and_clone
    (clone / "a.txt").write_text("edited\n")
    _advance_main(seed)
    out = tools.tool_git_sync_base("main", workspace=str(clone))
    assert out.startswith("Refused")
    assert not (clone / "b.txt").exists() and (clone / "a.txt").read_text() == "edited\n"


def test_real_push_refuses_main_and_allows_a_feature_branch(remote_and_clone):
    _, clone = remote_and_clone
    (clone / "c.txt").write_text("c\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "c")
    assert not tools.tool_git_push(set_upstream=True, workspace=str(clone)).startswith("Error")
    assert "feat/x" in _git(clone, "branch", "-r", "--list", "origin/feat/x")
    _git(clone, "checkout", "main")
    out = tools.tool_git_push(workspace=str(clone))
    assert out.startswith("Error") and "main" in out


def test_real_git_log_and_fetch(remote_and_clone):
    seed, clone = remote_and_clone
    assert "first" in tools.tool_git_log(5, workspace=str(clone))
    _advance_main(seed)
    assert tools.tool_git_fetch(workspace=str(clone)).startswith("✓")
    assert "add b.txt" in _git(clone, "log", "origin/main", "--oneline")
