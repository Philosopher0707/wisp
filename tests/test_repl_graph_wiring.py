"""How the CLI REPL uses the graph path, and what it owes the conversation afterwards.

Found by driving a real REPL (a pty, the mock provider) with `implement the login api for this service across the module` (2026-10-10):

* the prompt was routed to the coding graph by substring hints, so `author` counted as `auth` and `capital` as `api`;
* the graph reviewer rejected the change four times, the run ended `correction cycle exhausted` with `verification: REJECT`, and the REPL printed a green
  tick, because the graph's own status (`succeeded`: every node ran) was read as the work's success;
* afterwards the REPL session held **zero messages**: the next prompt had no idea a graph had run, what it changed, or whether it was verified.
"""

from __future__ import annotations

import io
import time

import pytest

from wisp import coding as C


def ctx(prompt, ws="."):
    return C.task_context_from_prompt(prompt, ws, {"workspace": ws})


class TestHintsMatchWordsNotSubstrings:
    @pytest.mark.parametrize("prompt", [
        "rename author to writer in the authors module",       # `auth` inside `author`, plus one real hint
        "fix the capital city service typo",                   # `api` inside `capital`, plus one real hint
        "tidy the rapid parser and the submodule loader",      # `api` in `rapid`, `module` in `submodule`
        "make the authority table prettier across one screen",  # `auth` in `authority`
        "rewrite the submodule loader",                         # `module` in `submodule`, plus one real hint
    ])
    def test_a_word_that_merely_contains_a_hint_does_not_route_to_the_graph(self, prompt):
        d = C.decide_strategy(ctx(prompt))
        assert d.strategy == C.SINGLE_AGENT, d.reasons

    @pytest.mark.parametrize("prompt", [
        "refactor the auth module across multiple files with tests",
        "add user authentication with login, sessions, and password reset emails",
        "migrate the database and rewrite the api layer",
        "implement the login api for this service across the module",
        "audit the services for vulnerabilities and write regression tests",
        "fix the login service",                                # `login` is the decisive second hint
    ])
    def test_real_hints_still_route_to_the_graph(self, prompt):
        assert C.decide_strategy(ctx(prompt)).strategy == C.GRAPH

    def test_the_reason_names_the_hints_that_matched(self):
        d = C.decide_strategy(ctx("refactor the auth module across multiple files"))
        assert "refactor" in d.reasons[0] and "auth" in d.reasons[0]

    def test_the_public_hint_list_and_the_matchers_cannot_drift(self):
        assert set(C.GRAPH_HINTS) == set(C._HINT_PATTERNS)


def final_with(verification, changed=("a.py",), status="succeeded"):
    nodes = {"implement": {"status": "success", "output": {"summary": "implemented", "changed_files": list(changed)}}}
    if verification is not None:
        nodes["review"] = {"status": "success", "output": {"decision": verification, "summary": f"reviewer said {verification}"}}
    return {"status": status, "run_id": "graph-abc", "results_by_node": nodes}


class TestTheOutcomeIsAsHonestAsTheReview:
    def test_a_rejected_change_is_not_a_success_even_though_every_node_ran(self):
        r = C.summarize_graph("t", "graph-abc", final_with("REJECT"))
        assert r.success is False and r.verification == "REJECT"

    def test_an_escalation_is_not_a_success_either(self):
        assert C.summarize_graph("t", "graph-abc", final_with("ESCALATE")).success is False

    def test_an_approved_change_is_a_success(self):
        assert C.summarize_graph("t", "graph-abc", final_with("ALLOW")).success is True

    def test_changes_nobody_reviewed_are_not_called_a_success(self):
        r = C.summarize_graph("t", "graph-abc", final_with(None))
        assert r.success is False and r.verification == "unverified"

    def test_a_run_that_changed_nothing_and_was_not_reviewed_is_not_blamed(self):
        assert C.summarize_graph("t", "graph-abc", final_with(None, changed=())).success is True

    def test_a_graph_that_did_not_finish_is_a_failure_whatever_the_review_said(self):
        assert C.summarize_graph("t", "graph-abc", final_with("ALLOW", status="failed")).success is False


class FakeRunner:
    def __init__(self):
        self.out = io.StringIO()
        self.session = {"id": "s"}
        self.config = {"workspace": "."}


GRAPH_PROMPT = "implement the login api for this service across the module"


class TestTheGraphTurnIsPartOfTheConversation:
    def run(self, monkeypatch, final):
        monkeypatch.setattr(C, "run_coding_template", lambda *a, **k: final)
        monkeypatch.setattr(C, "repo_context", lambda *a, **k: C.RepoContext())
        runner = FakeRunner()
        assert C.handle_prompt(runner, GRAPH_PROMPT) is True
        return runner

    def test_the_prompt_and_the_outcome_are_recorded_in_the_session(self, monkeypatch):
        runner = self.run(monkeypatch, final_with("ALLOW", changed=("app.py", "auth.py")))
        msgs = runner.session["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == GRAPH_PROMPT
        note = msgs[1]["content"]
        assert "app.py" in note and "auth.py" in note and "ALLOW" in note and "/graph trace graph-abc" in note

    def test_a_rejected_run_is_recorded_as_rejected_and_printed_without_a_tick(self, monkeypatch):
        runner = self.run(monkeypatch, final_with("REJECT"))
        note = runner.session["messages"][1]["content"]
        assert "REJECT" in note and "not verified" in note.lower()
        printed = runner.out.getvalue()
        assert "✗" in printed and "✓ implemented" not in printed and "verification: REJECT" in printed

    def test_a_graph_that_raises_is_recorded_too(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("provider unreachable")

        monkeypatch.setattr(C, "run_coding_template", boom)
        monkeypatch.setattr(C, "repo_context", lambda *a, **k: C.RepoContext())
        runner = FakeRunner()
        assert C.handle_prompt(runner, GRAPH_PROMPT) is True
        assert "provider unreachable" in runner.session["messages"][1]["content"]

    def test_a_single_agent_prompt_adds_nothing_here(self):
        runner = FakeRunner()
        assert C.handle_prompt(runner, "fix typo in main.py") is False
        assert "messages" not in runner.session

    def test_the_record_is_bounded(self, monkeypatch):
        final = final_with("ALLOW", changed=tuple(f"f{i}.py" for i in range(200)))
        final["results_by_node"]["review"]["output"]["summary"] = "x" * 50000  # the node whose summary the graph reports
        runner = self.run(monkeypatch, final)
        note = runner.session["messages"][1]["content"]
        assert len(note) < 1000 and "(+52 more)" in note  # the summary is cut to 600; 64 files are kept by the summary, 12 shown

    def test_the_gate_still_never_touches_the_database(self):
        import ast
        import wisp.coding as mod

        names = {n.attr for n in ast.walk(ast.parse(open(mod.__file__).read())) if isinstance(n, ast.Attribute)}
        assert not names & {"save_session", "execute", "cursor"}


class TestThroughARealGraphRun:
    """The real executor and the real subagent nodes, with the mock provider: nothing in the graph is faked."""

    def test_a_real_run_leaves_an_honest_record(self, tmp_path):
        from wisp.config import WispConfig

        (tmp_path / "app.py").write_text("def login(user, pw):\n    return True\n")
        runner = FakeRunner()
        runner.config = WispConfig().replace(workspace=str(tmp_path), provider="mock", model="mock")
        t0 = time.monotonic()
        assert C.handle_prompt(runner, GRAPH_PROMPT) is True
        assert time.monotonic() - t0 < 120
        msgs = runner.session["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        note, printed = msgs[1]["content"], runner.out.getvalue()
        assert "/graph trace" in note and "verification:" in note
        verdict_lines = [ln for ln in printed.splitlines() if ln[:1] in ("✓", "✗")]  # the node progress lines are indented; the verdict is not
        assert len(verdict_lines) == 1
        approved = "verification: ALLOW" in printed
        assert verdict_lines[0].startswith("✓") is approved, printed[-600:]
