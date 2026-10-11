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


def run_fan_out(monkeypatch, tmp_path, failing=(), findings="found three things"):
    """The `parallel-analysis` template through `run_coding_template` and the real executor, with a fake node runner (a model is not needed to
    know what the summary says about what the nodes returned)."""
    from wisp.graph import runner as graph_runner
    from wisp.graph.executor import GraphExecutor
    from wisp.graph.store import GraphStore
    from wisp.graph.types import NodeResult, NodeStatus

    async def run(node, inputs):
        if node.id in failing:
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="NODE_FAILED", message="Provider kept rejecting requests (HTTP 429)")
        if node.id == "synthesize":
            return NodeResult(node.id, NodeStatus.SUCCESS, output={"text": "Layout is clean; deps has no tests."})
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"findings": f"{node.id}: {findings}"})

    async def no_sleep(seconds):
        pass

    def executor(workspace, max_concurrency=8, emit=None, config=None):
        ex = GraphExecutor(runner=run, workspace=workspace, store=GraphStore(workspace=workspace), sleep=no_sleep)
        ex.register_function("split_work", lambda inputs: {"ok": True})
        return ex

    monkeypatch.setattr(graph_runner, "default_executor", executor)
    context = C.task_context_from_prompt("audit the services", str(tmp_path), {"workspace": str(tmp_path)})
    final = C.run_coding_template("parallel-analysis", context)
    return final, C.summarize_graph("parallel-analysis", final["run_id"], final)


class TestWhatTheGraphProducedIsNotThrownAway:
    """Found by running the shipped `parallel-analysis` template through the real executor (2026-10-11): a run in which every branch succeeded
    reported `graph succeeded` (the synthesised analysis, the whole point of the graph, was dropped: the summary looked for nodes called `final`,
    `final2` or `review`, and this template ends in `synthesize`); and a run in which one branch hit a 429 reported only `tests: Provider kept
    rejecting requests (HTTP 429)`, dropping what the three branches that finished had found."""

    def test_the_run_result_names_the_graphs_sinks(self, monkeypatch, tmp_path):
        final, _ = run_fan_out(monkeypatch, tmp_path)
        assert final["sinks"] == ["synthesize"]

    def test_a_finished_analysis_reports_what_it_found(self, monkeypatch, tmp_path):
        final, r = run_fan_out(monkeypatch, tmp_path)
        assert final["status"] == "succeeded" and r.success is True
        assert r.summary == "Layout is clean; deps has no tests."

    def test_one_dead_branch_does_not_hide_what_the_others_found(self, monkeypatch, tmp_path):
        final, r = run_fan_out(monkeypatch, tmp_path, failing=("tests",))
        assert final["status"] == "cancelled"
        assert r.success is False, "the status stays honest: the run did not finish"
        assert "tests: Provider kept rejecting requests (HTTP 429)" in r.summary, "and it says why"
        assert "finished before it stopped" in r.summary
        for branch in ("structure", "symbols", "deps"):
            assert f"{branch}: {branch}: found three things" in r.summary
        assert "synthesize" not in r.summary and "split" not in r.summary, "nodes that produced nothing are not listed"

    def test_what_the_finished_nodes_produced_is_bounded(self, monkeypatch, tmp_path):
        _, r = run_fan_out(monkeypatch, tmp_path, failing=("tests",), findings="x" * 5000)
        assert len(r.summary) <= 2000
        # each node's text starts with its own name (`deps: ` is 6 characters, `structure: ` is 11) and is cut to 150 characters in all
        assert "x" * 144 in r.summary and "x" * 145 not in r.summary, "each node is cut to 150 characters"

    def test_a_run_that_ended_at_its_reviewer_is_summarised_by_the_reviewer_not_by_every_node(self):
        r = C.summarize_graph("t", "graph-abc", final_with("REJECT"))
        assert r.summary == "reviewer said REJECT"

    def test_without_sinks_the_old_tail_names_still_work(self):
        final = final_with("ALLOW")
        assert "sinks" not in final
        assert C.summarize_graph("t", "graph-abc", final).summary == "reviewer said ALLOW"

    def test_many_finished_nodes_are_counted_not_all_listed(self):
        nodes = {f"b{i}": {"status": "success", "output": {"findings": f"found {i}"}} for i in range(10)}
        nodes["bad"] = {"status": "failure", "message": "boom"}
        r = C.summarize_graph("t", "graph-abc", {"status": "cancelled", "run_id": "g", "results_by_node": nodes, "sinks": ["sink"]})
        assert "bad: boom" in r.summary and "b0: found 0" in r.summary and "b5: found 5" in r.summary
        assert "b6" not in r.summary and "(+4 more)" in r.summary


class TestWhichTextTheGraphReports:
    @staticmethod
    def tail(**out):
        final = {"status": "succeeded", "run_id": "g", "sinks": ["done"], "results_by_node": {"done": {"status": "success", "output": out}}}
        return C.summarize_graph("t", "g", final).summary

    @staticmethod
    def stopped(nodes):
        return C.summarize_graph("t", "g", {"status": "cancelled", "run_id": "g", "sinks": ["sink"], "results_by_node": nodes})

    def test_summary_beats_text_beats_findings(self):
        assert self.tail(summary="S", text="T", findings="F") == "S"
        assert self.tail(text="T", findings="F") == "T"
        assert self.tail(findings="F") == "F"

    def test_findings_given_as_a_list_are_joined_and_cut_to_five(self):
        assert self.tail(findings=["a", "b", "c", "d", "e", "f"]) == "a; b; c; d; e"

    def test_whitespace_is_not_a_summary_and_the_product_keeps_its_shape(self):
        assert self.tail(summary="  \n ", text="T") == "T"
        assert self.tail(summary="line one\nline two") == "line one\nline two"

    def test_a_node_listed_as_finished_is_one_line(self):
        r = self.stopped({"a": {"status": "success", "output": {"findings": "first line\nsecond   line"}}, "bad": {"status": "failure", "message": "boom"}})
        assert "a: first line second line" in r.summary

    def test_a_successful_run_with_no_text_at_its_sink_is_not_dressed_up_as_a_stop(self):
        nodes = {"implement": {"status": "success", "output": {"summary": "implemented", "changed_files": ["a.py"]}}, "verify": {"status": "success", "output": {"decision": "ALLOW"}}}
        final = {"status": "succeeded", "run_id": "g", "sinks": ["verify"], "results_by_node": nodes}
        assert C.summarize_graph("t", "g", final).summary == "graph succeeded"

    def test_a_failed_nodes_output_is_not_counted_as_finished(self):
        r = self.stopped({"a": {"status": "failure", "output": {"findings": "partial garbage"}, "message": "boom"}, "b": {"status": "success", "output": {"findings": "good"}}})
        assert "b: good" in r.summary and "garbage" not in r.summary

    def test_the_problems_are_cut_to_make_room_for_what_finished(self):
        nodes = {f"bad{i}": {"status": "failure", "message": "m" * 300} for i in range(4)}
        nodes["ok"] = {"status": "success", "output": {"findings": "kept"}}
        head, sep, tail = self.stopped(nodes).summary.partition(" | ")
        assert sep and len(head) == 200 and tail == "finished before it stopped: ok: kept"

    def test_without_anything_finished_the_problems_keep_their_whole_room(self):
        nodes = {f"bad{i}": {"status": "failure", "message": "m" * 300} for i in range(4)}
        summary = self.stopped(nodes).summary
        assert len(summary) > 1000 and " | " not in summary

    def test_the_whole_summary_is_cut_to_two_thousand_characters(self):
        nodes = {"bad": {"status": "failure", "message": "m" * 300}}
        nodes.update({f"b{i}" + "n" * 400: {"status": "success", "output": {"findings": "y" * 1000}} for i in range(8)})  # node ids have no bound of their own
        assert len(self.stopped(nodes).summary) == 2000

    def test_what_finished_comes_early_enough_to_survive_the_conversations_record(self):
        nodes = {"bad": {"status": "failure", "message": "m" * 300}}
        nodes.update({f"b{i}": {"status": "success", "output": {"findings": f"found {i}"}} for i in range(4)})
        kept = self.stopped(nodes).summary[:C.RECORD_SUMMARY_CHARS]
        assert "b0: found 0" in kept and "b1: found 1" in kept

    def test_the_sink_beats_a_node_that_merely_has_a_familiar_name(self):
        nodes = {"review": {"status": "success", "output": {"summary": "interim review"}}, "report": {"status": "success", "output": {"summary": "final report"}}}
        final = {"status": "succeeded", "run_id": "g", "sinks": ["report"], "results_by_node": nodes}
        assert C.summarize_graph("t", "g", final).summary == "final report"

    def test_the_graphs_product_is_cut_to_two_thousand_characters(self):
        assert len(self.tail(summary="z" * 5000)) == 2000

    def test_exactly_six_finished_nodes_are_all_listed_without_a_count_of_more(self):
        nodes = {f"b{i}": {"status": "success", "output": {"findings": f"found {i}"}} for i in range(6)}
        nodes["bad"] = {"status": "failure", "message": "boom"}
        summary = self.stopped(nodes).summary
        assert "b5: found 5" in summary and "more" not in summary

    def test_only_the_first_four_problems_are_reported(self):
        nodes = {f"bad{i}": {"status": "failure", "message": f"problem {i}"} for i in range(5)}
        summary = self.stopped(nodes).summary
        assert "bad3: problem 3" in summary and "bad4" not in summary

    def test_a_run_that_succeeded_but_whose_sink_made_no_text_lists_nothing(self):
        final = {"status": "succeeded", "run_id": "g", "sinks": ["sink"], "results_by_node": {"a": {"status": "success", "output": {"findings": "x"}}}}
        assert C.summarize_graph("t", "g", final).summary == "graph succeeded"


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
