"""What a graph node's worker is told, and what a worker's reply becomes.

Observed with the real model (2026-10-11), each of these made a graph template unable to do its job:

* the user's request reached only the entry node, and in `parallel-analysis` the entry is a function node, so no analysis worker ever saw it;
* a worker's reply is free text, `NodeResult.output` was `{"text": ...}`, and every edge mapping `output.<key>` found nothing, so the synthesizer received `{}`;
* a verifier node's verdict is read from `output["decision"]`, which free text never has, so every verifier answered REJECT, whatever it said;
* a correction retry sent the identical task to the orchestrator, which answered from its result cache (1 ms), so the worker never ran again.

The model is the only thing stubbed here (at `SubagentRunner.run`, below the orchestrator's cache); the template, the planner, the executor, the node runner and the
orchestrator are the production ones.
"""

from __future__ import annotations

import json

from wisp import coding as C
from wisp.config import WispConfig
from wisp.multi_agent._runner import SubagentRunner
from wisp.multi_agent.task import SubagentContract, SubagentResult

SENT = "SENTINEL-OBJECTIVE"
#: What the harness saw a verifier do when it really ran the suite and it was green (`SubagentResult.observations`). An ALLOW stands only with this behind it.
GREEN_RUN = [{"tool": "run_tests", "green": True}]


def run_template(monkeypatch, tmp_path, template: str, reply, verifier_saw=()):
    """Run `template`; `reply(contract, n)` is what the n-th worker call (counting from 1) answers. `verifier_saw` is what the harness recorded of the `verify`
    node's tool results. Returns (run result, contracts in call order)."""
    calls: list[SubagentContract] = []

    async def fake(self, contract, *args, **kwargs):
        calls.append(contract)
        answer = reply(contract, len(calls))
        if isinstance(answer, SubagentResult):
            return answer
        return SubagentResult(task_id=contract.name, success=True, output=answer, observations=list(verifier_saw) if contract.name == "verify" else [])

    monkeypatch.setattr(SubagentRunner, "run", fake)
    config = WispConfig().replace(provider="mock", model="mock-model", workspace=str(tmp_path))
    ctx = C.task_context_from_prompt(f"audit the repo {SENT}", str(tmp_path), {"workspace": str(tmp_path)})
    return C.run_coding_template(template, ctx, config=config), calls


def verdict(decision: str, **extra) -> str:
    return "I checked the change.\n" + json.dumps({"decision": decision, **extra})


class TestEveryWorkerIsToldTheRequest:
    def test_the_analysis_branches_and_the_synthesizer_all_see_it(self, monkeypatch, tmp_path):
        _, calls = run_template(monkeypatch, tmp_path, "parallel-analysis", lambda c, n: f"reply of {c.name}")
        assert {c.name for c in calls} == {"structure", "symbols", "deps", "tests", "synthesize"}
        assert all(SENT in c.task for c in calls), [c.name for c in calls if SENT not in c.task]

    def test_a_node_after_the_entry_in_a_chain_sees_it_too(self, monkeypatch, tmp_path):
        _, calls = run_template(monkeypatch, tmp_path, "simple", lambda c, n: verdict("ALLOW", evidence=["x"]) if c.name == "verify" else "reply")
        assert [c.name for c in calls] == ["analyze", "implement", "verify"]
        assert all(SENT in c.task for c in calls)


class TestWhatAWorkerRepliedReachesTheNodeThatReadsIt:
    def test_the_synthesizer_gets_each_branchs_reply(self, monkeypatch, tmp_path):
        _, calls = run_template(monkeypatch, tmp_path, "parallel-analysis", lambda c, n: f"FINDINGS-FROM-{c.name}")
        synthesize = next(c for c in calls if c.name == "synthesize")
        for branch in ("structure", "symbols", "deps", "tests"):
            assert f"FINDINGS-FROM-{branch}" in synthesize.task, synthesize.task[-600:]

    def test_a_structured_reply_beats_the_text_for_the_key_it_names(self, monkeypatch, tmp_path):
        def reply(contract, n):
            if contract.name == "synthesize":
                return "done"
            return SubagentResult(task_id=contract.name, success=True, output="PROSE", validated_output={"findings": f"STRUCTURED-{contract.name}"})

        _, calls = run_template(monkeypatch, tmp_path, "parallel-analysis", reply)
        synthesize = next(c for c in calls if c.name == "synthesize")
        assert "STRUCTURED-structure" in synthesize.task and "PROSE" not in synthesize.task

    def test_the_implementation_report_reaches_the_verifier(self, monkeypatch, tmp_path):
        _, calls = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW", evidence=["x"]) if c.name == "verify" else "IMPLEMENTED-IN-X")
        assert "IMPLEMENTED-IN-X" in next(c for c in calls if c.name == "verify").task


class TestAVerifierCanSayYes:
    def accepted(self, result) -> bool:
        return result["results_by_node"]["verify"]["output"].get("decision") == "ALLOW"

    def test_a_reply_that_ends_in_an_allow_with_evidence_is_accepted_and_nothing_is_redone(self, monkeypatch, tmp_path):
        result, calls = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW", evidence=["pytest: 1 passed"]) if c.name == "verify" else "fixed",
                                     verifier_saw=GREEN_RUN)
        assert self.accepted(result)
        assert [c.name for c in calls] == ["implement", "verify", "done"]  # one attempt, then the summary node

    def test_the_same_reply_with_no_test_run_behind_it_is_not(self, monkeypatch, tmp_path):
        """The evidence list is the model's own text. Only what the harness saw (`verifier_saw`) lets an ALLOW stand (details: tests/test_graph_harness_evidence.py)."""
        result, _ = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW", evidence=["pytest: 1 passed"]) if c.name == "verify" else "fixed")
        out = result["results_by_node"]["verify"]["output"]
        assert out["decision"] == "REJECT" and "NO_TEST_RUN" in out["reason_codes"]

    def test_prose_with_no_verdict_is_a_reject_that_says_why(self, monkeypatch, tmp_path):
        result, _ = run_template(monkeypatch, tmp_path, "repair", lambda c, n: "Looks good to me." if c.name == "verify" else "fixed")
        out = result["results_by_node"]["verify"]["output"]
        assert out["decision"] == "REJECT" and "NO_VERDICT" in out["reason_codes"]

    def test_an_allow_without_evidence_is_a_reject(self, monkeypatch, tmp_path):
        result, _ = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW") if c.name == "verify" else "fixed")
        out = result["results_by_node"]["verify"]["output"]
        assert out["decision"] == "REJECT" and "NO_EVIDENCE" in out["reason_codes"]

    def test_evidence_that_is_not_text_does_not_count(self, monkeypatch, tmp_path):
        result, _ = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW", evidence=[1, None, ""]) if c.name == "verify" else "fixed")
        assert result["results_by_node"]["verify"]["output"]["decision"] == "REJECT"

    def test_a_verdict_that_is_not_one_of_the_four_is_a_reject(self, monkeypatch, tmp_path):
        result, _ = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("MAYBE", evidence=["x"]) if c.name == "verify" else "fixed")
        assert result["results_by_node"]["verify"]["output"]["decision"] == "REJECT"

    def test_a_broken_json_tail_is_a_reject_not_a_crash(self, monkeypatch, tmp_path):
        result, _ = run_template(monkeypatch, tmp_path, "repair", lambda c, n: 'checked. {"decision": "ALLOW", "evidence": [' if c.name == "verify" else "fixed")
        assert result["results_by_node"]["verify"]["output"]["decision"] == "REJECT"

    def test_the_last_verdict_in_the_reply_is_the_one_that_counts(self, monkeypatch, tmp_path):
        text = 'first thought {"decision": "ALLOW", "evidence": ["early"]} but then ' + verdict("REJECT", message="no")
        result, _ = run_template(monkeypatch, tmp_path, "repair", lambda c, n: text if c.name == "verify" else "fixed")
        assert result["results_by_node"]["verify"]["output"]["decision"] == "REJECT"

    def test_the_verifier_is_told_what_to_reply(self, monkeypatch, tmp_path):
        _, calls = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW", evidence=["x"]) if c.name == "verify" else "fixed")
        task = next(c for c in calls if c.name == "verify").task
        assert '"decision"' in task and "ALLOW" in task and "evidence" in task

    def test_only_a_verifier_is_asked_for_a_verdict(self, monkeypatch, tmp_path):
        _, calls = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW", evidence=["x"]) if c.name == "verify" else "fixed")
        assert '"decision"' not in next(c for c in calls if c.name == "implement").task

    def test_the_verifier_can_run_the_tests_it_judges_by(self, monkeypatch, tmp_path):
        _, calls = run_template(monkeypatch, tmp_path, "repair", lambda c, n: verdict("ALLOW", evidence=["x"]) if c.name == "verify" else "fixed")
        assert "run_tests" in next(c for c in calls if c.name == "verify").tools


class TestACorrectionReallyRunsTheWorkerAgain:
    def reply(self, contract, n):
        if contract.name == "verify":
            return verdict("REJECT", message="totals.py still subtracts one") if n == 2 else verdict("ALLOW", evidence=["pytest: 1 passed"])
        return "fixed"

    def test_the_worker_runs_a_second_time_and_is_told_what_was_wrong(self, monkeypatch, tmp_path):
        result, calls = run_template(monkeypatch, tmp_path, "repair", self.reply, verifier_saw=GREEN_RUN)
        names = [c.name for c in calls]
        assert names[:4] == ["implement", "verify", "implement", "verify"], names
        assert "totals.py still subtracts one" in calls[2].task
        assert result["results_by_node"]["verify"]["output"]["decision"] == "ALLOW"

    def test_identical_tasks_are_not_answered_from_the_cache(self, monkeypatch, tmp_path):
        """Even when nothing in the task changed (the verifier gave no reason), a retry is a real run."""
        n_implement = []

        def reply(contract, n):
            if contract.name == "implement":
                n_implement.append(1)
                return "fixed"
            return verdict("REJECT") if len(n_implement) < 2 else verdict("ALLOW", evidence=["x"])

        run_template(monkeypatch, tmp_path, "repair", reply, verifier_saw=GREEN_RUN)
        assert len(n_implement) == 2


class TestTheOrchestratorCacheCanBeBypassed:
    def test_a_contract_that_says_so_is_never_answered_from_the_cache(self, monkeypatch, tmp_path):
        import asyncio

        from wisp.multi_agent import SubagentOrchestrator

        runs: list[int] = []

        async def fake(self, contract, *args, **kwargs):
            runs.append(1)
            return SubagentResult(task_id=contract.name, success=True, output="ok")

        monkeypatch.setattr(SubagentRunner, "run", fake)
        orch = SubagentOrchestrator(config=WispConfig().replace(provider="mock", model="mock-model", workspace=str(tmp_path)), workspace=tmp_path)

        async def go(cache: bool):
            runs.clear()
            for _ in range(2):
                await orch.run(SubagentContract(name="w", task="same task", cache=cache))
            return len(runs)

        assert asyncio.run(go(True)) == 1  # the default is unchanged: the second identical contract is a hit
        orch._cache.clear()
        assert asyncio.run(go(False)) == 2


class TestEachHalfOfTheCacheBypass:
    """The lookup and the store are guarded separately; either guard alone would hide the other, so each is pinned on its own."""

    def orch(self, monkeypatch, tmp_path):
        from wisp.multi_agent import SubagentOrchestrator

        runs: list[int] = []

        async def fake(self, contract, *args, **kwargs):
            runs.append(1)
            return SubagentResult(task_id=contract.name, success=True, output="ok")

        monkeypatch.setattr(SubagentRunner, "run", fake)
        orch = SubagentOrchestrator(config=WispConfig().replace(provider="mock", model="mock-model", workspace=str(tmp_path)), workspace=tmp_path)
        return orch, runs

    def test_a_stored_result_is_not_served_to_a_contract_that_opted_out(self, monkeypatch, tmp_path):
        import asyncio

        orch, runs = self.orch(monkeypatch, tmp_path)
        asyncio.run(orch.run(SubagentContract(name="w", task="t")))
        asyncio.run(orch.run(SubagentContract(name="w", task="t", cache=False)))
        assert len(runs) == 2

    def test_the_result_of_an_opted_out_run_is_not_stored_for_the_next(self, monkeypatch, tmp_path):
        import asyncio

        orch, runs = self.orch(monkeypatch, tmp_path)
        asyncio.run(orch.run(SubagentContract(name="w", task="t", cache=False)))
        asyncio.run(orch.run(SubagentContract(name="w", task="t")))
        assert len(runs) == 2


class TestTheVerdictIsReadFromTheEndOfTheReplyOnly:
    def test_a_verdict_buried_far_before_the_end_is_not_found(self):
        from wisp.graph.runner import verdict_from_reply

        buried = verdict("ALLOW", evidence=["x"]) + " filler" * 5000
        assert verdict_from_reply(buried)["reason_codes"] == ["NO_VERDICT"]

    def test_thousands_of_open_braces_before_the_verdict_cost_nothing_and_still_find_it(self):
        import time

        from wisp.graph.runner import verdict_from_reply

        start = time.monotonic()
        out = verdict_from_reply("{" * 15000 + verdict("ALLOW", evidence=["observed"]))
        assert out["decision"] == "ALLOW" and time.monotonic() - start < 5


class TestOnlyAnAgentsOrAVerifiersTextStandsInForAKey:
    def graph(self):
        from wisp.graph.dsl import graph_from_yaml

        return graph_from_yaml(
            'version: "1"\ngraph:\n  id: g\n  entry: f\n  nodes:\n    f: {type: function, function: split_work, responsibility: x}\n'
            "    a: {type: agent, allowed_tools: [read_file], responsibility: x}\n  edges:\n    - {from: f, to: a, reason: a consumes f.output.k}\n")

    def test_an_agents_text_stands_in(self):
        from wisp.graph.executor import _reply_for_key
        from wisp.graph.types import NodeResult, NodeStatus

        src = NodeResult("a", NodeStatus.SUCCESS, output={"text": "reply"})
        assert _reply_for_key(self.graph(), "a", src, "output.k") == "reply"

    def test_a_function_nodes_missing_key_stays_missing(self):
        from wisp.graph.executor import _reply_for_key
        from wisp.graph.types import NodeResult, NodeStatus

        src = NodeResult("f", NodeStatus.SUCCESS, output={"text": "looks like a reply"})
        assert _reply_for_key(self.graph(), "f", src, "output.k") is None

    def test_a_nested_path_or_the_text_key_itself_is_not_substituted(self):
        from wisp.graph.executor import _reply_for_key
        from wisp.graph.types import NodeResult, NodeStatus

        src = NodeResult("a", NodeStatus.SUCCESS, output={"text": "reply"})
        assert _reply_for_key(self.graph(), "a", src, "output.k.deeper") is None
        assert _reply_for_key(self.graph(), "a", src, "output.text") is None
