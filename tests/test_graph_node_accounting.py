"""What a graph run spent, per node and in total, and what is not known.

Seen in a real run (2026-10-11/12): `/graph trace <run>` and `wisp graph trace|inspect` printed `wall 0s tokens {} cost $0` for every stored run, because the stored
run's totals were hard-coded instead of summed from the node rows the executor had saved; each node's `provider` was empty and its `cost_usd` was always 0.

* Totals come from the stored node rows (`wisp.graph.trace.trace_from_store`, the one builder the REPL and `wisp graph` share).
* A node's provider and model are the ones the child actually ran with.
* Tokens are ESTIMATES (the providers do not report usage; `wisp.core.spend`), and the output says so.
* A cost is `tokens x price` from the one price table (`wisp.runtime.cost`). A model that is not in it has NO cost: the node says `unpriced` instead of `$0`, and a graph
  with a cost cap treats unpriceable spend as over the cap ("a cost that cannot be computed cannot be bounded", the meter's own rule).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from wisp.graph.executor import GraphExecutor, _result_from_dict, _result_to_dict
from wisp.graph.runner import SubagentNodeRunner
from wisp.graph.store import GraphStore
from wisp.graph.trace import render_ascii, totals_from_nodes, trace_from_store
from wisp.graph.types import EdgeMapping, Graph, GraphNode, GraphPolicy, NodeResult, NodeStatus, NodeType
from wisp.multi_agent.task import SubagentResult
from wisp.runtime.cost import price_of_call


# ── the price ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


class TestThePriceOfACall:
    def test_a_model_in_the_table_is_tokens_times_price(self):
        assert price_of_call("gpt-4o", input_tokens=1000, output_tokens=500) == pytest.approx(2.50 + 5.00)

    def test_a_free_model_in_the_table_is_zero(self):
        assert price_of_call("local", input_tokens=10_000, output_tokens=10_000) == 0.0

    @pytest.mark.parametrize("model", ["nvidia/nemotron-3-ultra-550b-a55b", "", "gpt-4o-2024-08-06", "GPT-4O", None, 7, [], ["gpt-4o"], {"a": 1}])
    def test_a_model_that_is_not_in_the_table_has_no_price_and_is_never_zero(self, model):
        assert price_of_call(model, input_tokens=1000, output_tokens=1000) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [-5, True, False, None, "9", float("nan"), float("inf"), float("-inf"), [3]])
    def test_a_token_count_that_is_not_a_count_is_read_as_zero_not_as_credit_or_as_one(self, bad):
        """Only the output side is real here (1000 tokens at 10.00 per 1k): the bad input must add nothing and take nothing away."""
        assert price_of_call("gpt-4o", input_tokens=bad, output_tokens=1000) == pytest.approx(10.0)
        assert price_of_call("gpt-4o", input_tokens=1000, output_tokens=bad) == pytest.approx(2.5)

    def test_the_table_is_read_at_call_time(self, monkeypatch):
        """A deployment replaces the placeholder prices by rebinding `PRICE_TABLE`; a copy taken at import would ignore it."""
        import wisp.runtime.cost as cost

        monkeypatch.setattr(cost, "PRICE_TABLE", {"my-model": cost.Price(1.0, 2.0)})
        assert price_of_call("my-model", input_tokens=1000, output_tokens=1000) == pytest.approx(3.0)
        assert price_of_call("gpt-4o", input_tokens=1000, output_tokens=1000) is None


# ── a node's result ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


class _Orch:
    config = SimpleNamespace(model="session-model")

    def __init__(self, result):
        self._result = result

    async def run(self, contract):
        return self._result


def _node_result(result: SubagentResult) -> NodeResult:
    node = GraphNode(id="n", type=NodeType.AGENT, config={"prompt": "p"})
    return asyncio.run(SubagentNodeRunner(_Orch(result), ".")(node, {"task": "t"}))


class TestWhatTheNodeRunnerRecords:
    def test_provider_and_model_are_the_ones_the_child_ran_with(self):
        r = _node_result(SubagentResult(task_id="n", success=True, output="ok", provider="openai", model_used="gpt-4o", input_tokens=10, output_tokens=5))
        assert (r.provider, r.model) == ("openai", "gpt-4o")

    def test_a_priced_model_has_a_cost_and_is_not_marked(self):
        r = _node_result(SubagentResult(task_id="n", success=True, output="ok", provider="openai", model_used="gpt-4o", input_tokens=1000, output_tokens=500))
        assert r.cost_usd == pytest.approx(7.5) and r.unpriced is False

    def test_a_model_without_a_price_is_marked_not_zero_dollars(self):
        r = _node_result(SubagentResult(task_id="n", success=True, output="ok", provider="nvidia", model_used="nemotron", input_tokens=1000, output_tokens=500))
        assert r.cost_usd == 0.0 and r.unpriced is True

    def test_a_node_that_spent_nothing_is_not_unpriced(self):
        r = _node_result(SubagentResult(task_id="n", success=True, output="ok", provider="nvidia", model_used="nemotron"))
        assert r.unpriced is False

    def test_a_failed_node_that_spent_tokens_is_priced_the_same_way(self):
        r = _node_result(SubagentResult(task_id="n", success=False, error="x", provider="openai", model_used="gpt-4o", input_tokens=1000, output_tokens=0))
        assert r.cost_usd == pytest.approx(2.5)

    def test_a_runner_that_reports_its_own_cost_keeps_it(self):
        r = _node_result(SimpleNamespace(success=True, output="ok", error=None, provider="x", model_used="nemotron", input_tokens=100, output_tokens=100,
                                         cost_usd=0.42, elapsed_seconds=1.0))
        assert r.cost_usd == pytest.approx(0.42) and r.unpriced is False

    @pytest.mark.parametrize("reported", [-1.0, float("nan"), float("inf"), True, "0.5", None])
    def test_a_reported_cost_that_is_not_a_cost_is_not_believed(self, reported):
        r = _node_result(SimpleNamespace(success=True, output="ok", error=None, provider="openai", model_used="gpt-4o", input_tokens=1000, output_tokens=500,
                                         cost_usd=reported, elapsed_seconds=1.0))
        assert r.cost_usd == pytest.approx(7.5) and r.unpriced is False  # priced from the table instead

    def test_the_child_the_orchestrator_really_ran_reports_its_provider_and_model(self, tmp_path, monkeypatch):
        """Production path: real CompositionRoot -> orchestrator -> runner -> runtime; only the model is scripted. This printed `provider ''` before."""
        from wisp.composition import CompositionRoot
        from wisp.config import WispConfig
        from wisp.providers.factory import ProviderFactory
        from wisp.providers.mock import MockProvider
        import wisp.provider_catalog as pc

        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "a.py").write_text("x = 1\n")
        monkeypatch.setattr(pc, "resolve_selection", lambda cfg: SimpleNamespace(status="ok", suggested=None, provider="mock", detail="", model="mock-model",
                                                                                   alternatives=[]))
        read = {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
        monkeypatch.setattr(ProviderFactory, "from_config", lambda self, cfg: MockProvider(responses=["", "looked"], tool_calls=[[read]]))
        config = WispConfig().replace(workspace=str(ws), provider="mock", model="mock-model", permission_mode="auto_edit", auto_approve=False)
        root = CompositionRoot(config)
        node = GraphNode(id="n", type=NodeType.AGENT, config={"prompt": "look"})
        r = asyncio.run(SubagentNodeRunner(root.subagent_orchestrator, str(ws))(node, {"task": "look at a.py"}))
        assert r.provider == "mock" and r.model == "mock-model"
        assert r.input_tokens > 0 and r.output_tokens > 0
        assert r.unpriced is True  # `mock-model` has no price: it must not read as $0


    def test_the_model_a_child_resolved_for_an_unset_session_model_is_the_one_recorded(self, tmp_path, monkeypatch):
        """The owner's `.env` has `WISP_MODEL=` (empty): the parent turn serves "the first model the provider serves" and so must a child. The record must name that model,
        not the empty session setting."""
        from wisp.composition import CompositionRoot
        from wisp.config import WispConfig
        from wisp.providers.factory import ProviderFactory
        from wisp.providers.mock import MockProvider
        import wisp.provider_catalog as pc

        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(pc, "resolve_selection", lambda cfg: SimpleNamespace(status="ok", suggested="served-model", provider="mock", detail="", model="served-model",
                                                                                   alternatives=[]))
        monkeypatch.setattr(ProviderFactory, "from_config", lambda self, cfg: MockProvider(responses=["done"]))
        config = WispConfig().replace(workspace=str(ws), provider="mock", model="", permission_mode="auto_edit", auto_approve=False)
        root = CompositionRoot(config)
        node = GraphNode(id="n", type=NodeType.AGENT, config={"prompt": "p"})
        r = asyncio.run(SubagentNodeRunner(root.subagent_orchestrator, str(ws))(node, {"task": "say done"}))
        assert r.model == "served-model", (r.model, r.error_code, r.message)


class TestTheStoredRowKeepsIt:
    def test_the_flag_survives_a_round_trip(self):
        r = NodeResult("n", NodeStatus.SUCCESS, input_tokens=5, output_tokens=6, cost_usd=0.0, unpriced=True)
        assert _result_from_dict("n", _result_to_dict(r)).unpriced is True
        assert _result_from_dict("n", _result_to_dict(NodeResult("n"))).unpriced is False

    @pytest.mark.parametrize("junk", ["yes", 1, None, [], {"x": 1}])
    def test_a_row_that_says_something_else_is_not_unpriced(self, junk):
        assert _result_from_dict("n", {"status": "success", "unpriced": junk}).unpriced is False


# ── the cost cap ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _agent(nid):
    return GraphNode(id=nid, type=NodeType.AGENT)


def _run(tmp_path, results: dict[str, NodeResult], max_cost=None, max_tokens=None, nodes=("a", "b")):
    async def runner(node, inputs):
        r = results[node.id]
        return NodeResult(node.id, r.status, output=dict(r.output), input_tokens=r.input_tokens, output_tokens=r.output_tokens, cost_usd=r.cost_usd,
                          unpriced=r.unpriced, model="m", provider="p")

    async def no_sleep(_):
        return None

    edges = tuple(EdgeMapping(nodes[i], nodes[i + 1], reason="r") for i in range(len(nodes) - 1))
    g = Graph(id="g", entrypoint=nodes[0], nodes=tuple(_agent(n) for n in nodes), edges=edges, policies=GraphPolicy(max_cost_usd=max_cost, max_tokens=max_tokens))
    ex = GraphExecutor(runner=runner, workspace=str(tmp_path), store=GraphStore(workspace=str(tmp_path)), sleep=no_sleep)
    return asyncio.run(ex.run(g, {})), ex


class TestACostCapIsEnforcedOnlyWhenTheCostIsKnown:
    def test_a_known_cost_over_the_cap_stops_the_graph(self, tmp_path):
        out, _ = _run(tmp_path, {"a": NodeResult("a", cost_usd=2.0, input_tokens=10), "b": NodeResult("b")}, max_cost=1.0)
        assert out["status"] == "failed" and out["error"] == "budget_exceeded"

    def test_a_known_cost_under_the_cap_runs_on(self, tmp_path):
        out, _ = _run(tmp_path, {"a": NodeResult("a", cost_usd=0.5, input_tokens=10), "b": NodeResult("b")}, max_cost=1.0)
        assert out["status"] == "succeeded"

    def test_spend_that_cannot_be_priced_cannot_be_bounded_so_the_graph_stops(self, tmp_path):
        out, _ = _run(tmp_path, {"a": NodeResult("a", input_tokens=900, output_tokens=100, unpriced=True), "b": NodeResult("b")}, max_cost=1.0)
        assert out["status"] == "failed" and out["error"] == "budget_exceeded"

    def test_without_a_cap_unpriced_spend_is_only_reported(self, tmp_path):
        out, _ = _run(tmp_path, {"a": NodeResult("a", input_tokens=900, output_tokens=100, unpriced=True), "b": NodeResult("b")})
        assert out["status"] == "succeeded" and out["unpriced_nodes"] == ["a"]

    def test_a_token_cap_does_not_care_about_prices(self, tmp_path):
        out, _ = _run(tmp_path, {"a": NodeResult("a", input_tokens=900, output_tokens=100, unpriced=True), "b": NodeResult("b")}, max_tokens=5000)
        assert out["status"] == "succeeded"

    def test_the_audit_record_of_the_stop_names_them(self, tmp_path, monkeypatch):
        seen: list[tuple[str, object]] = []
        original = GraphExecutor._audit

        def spy(self, event, *args, **kwargs):
            seen.append((event, kwargs.get("evidence")))
            return original(self, event, *args, **kwargs)

        monkeypatch.setattr(GraphExecutor, "_audit", spy)
        _run(tmp_path, {"a": NodeResult("a", input_tokens=900, unpriced=True), "b": NodeResult("b")}, max_cost=1.0)
        evidence = next(e for name, e in seen if name == "graph.budget_exceeded")
        assert evidence["unpriced_nodes"] == ["a"] and evidence["tokens"] == 900

    def test_the_final_result_names_the_nodes_whose_cost_is_unknown(self, tmp_path):
        out, _ = _run(tmp_path, {"a": NodeResult("a", input_tokens=900, unpriced=True), "b": NodeResult("b")}, max_cost=1.0)
        assert out["error"] == "budget_exceeded" and out["unpriced_nodes"] == ["a"]


# ── the totals ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


class TestTheTotalsAreSummedFromTheNodes:
    def test_sums_tokens_cost_and_lists_the_unpriced(self):
        nodes = {"a": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.5, "status": "success"},
                 "b": {"input_tokens": 200, "output_tokens": 20, "cost_usd": 0.0, "unpriced": True, "status": "success"}}
        totals = totals_from_nodes(nodes)
        assert totals["tokens"] == {"input": 300, "output": 30}
        assert totals["cost_usd"] == pytest.approx(0.5) and totals["unpriced_nodes"] == ["b"]

    @pytest.mark.parametrize("junk", [None, "x", [], {"a": None}, {"a": "x"}, {"a": {"input_tokens": "9", "output_tokens": True, "cost_usd": float("nan")}}])
    def test_junk_is_zero_not_a_crash(self, junk):
        totals = totals_from_nodes(junk)  # type: ignore[arg-type]
        assert totals["tokens"] == {"input": 0, "output": 0} and totals["cost_usd"] == 0.0

    def test_negative_numbers_are_not_credit(self):
        totals = totals_from_nodes({"a": {"input_tokens": -50, "output_tokens": 5, "cost_usd": -3.0}, "b": {"cost_usd": 1.0}})
        assert totals["tokens"] == {"input": 0, "output": 5} and totals["cost_usd"] == pytest.approx(1.0)

    @pytest.mark.parametrize("flag", ["yes", 1, "true", [1], {"x": 1}, None, False])
    def test_only_a_boolean_true_marks_a_node_unpriced(self, flag):
        assert totals_from_nodes({"a": {"unpriced": flag}})["unpriced_nodes"] == []


class TestAStoredRunShowsWhatItSpent:
    def _stored(self, tmp_path):
        out, ex = _run(tmp_path, {"a": NodeResult("a", input_tokens=1500, output_tokens=100, cost_usd=0.25), "b": NodeResult("b", input_tokens=700, output_tokens=40, unpriced=True)})
        return out["run_id"], GraphStore(workspace=str(tmp_path))

    def test_the_wall_time_of_a_finished_run_is_read_from_the_run_and_a_running_one_has_none(self, tmp_path):
        async def slow(node, inputs):
            await asyncio.sleep(0.2)
            return NodeResult(node.id, input_tokens=1)

        async def no_sleep(_):
            return None

        g = Graph(id="g", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = GraphExecutor(runner=slow, workspace=str(tmp_path), store=GraphStore(workspace=str(tmp_path)), sleep=no_sleep)
        out = asyncio.run(ex.run(g, {}))
        store = GraphStore(workspace=str(tmp_path))
        assert trace_from_store(store, out["run_id"])["wall_s"] >= 0.15
        store.set_run_status(out["run_id"], "running")
        assert trace_from_store(store, out["run_id"])["wall_s"] == 0.0

    def test_the_trace_built_from_the_store_has_the_real_totals(self, tmp_path):
        run_id, store = self._stored(tmp_path)
        trace = trace_from_store(store, run_id)
        assert trace["tokens"] == {"input": 2200, "output": 140}
        assert trace["cost_usd"] == pytest.approx(0.25) and trace["unpriced_nodes"] == ["b"]
        assert trace["wall_s"] >= 0 and trace["status"] == "succeeded"

    def test_an_unknown_run_is_none(self, tmp_path):
        assert trace_from_store(GraphStore(workspace=str(tmp_path)), "nope") is None

    def test_what_is_printed_says_estimated_and_says_which_cost_is_unknown(self, tmp_path):
        run_id, store = self._stored(tmp_path)
        text = render_ascii(trace_from_store(store, run_id))
        assert "tokens" in text and "2200" in text and "estimated" in text
        assert "unpriced" in text and "b" in text.split("unpriced")[1]
        assert "cost $0.25 + unpriced: b" in text  # a partial cost is shown as a part, never as the total

    def test_each_node_line_shows_its_own_tokens(self, tmp_path):
        run_id, store = self._stored(tmp_path)
        text = render_ascii(trace_from_store(store, run_id))
        a_line = next(line for line in text.splitlines() if " a " in f" {line} ")
        assert "1500" in a_line and "100" in a_line

    def test_a_fully_priced_run_prints_a_plain_cost(self, tmp_path):
        out, _ = _run(tmp_path, {"a": NodeResult("a", input_tokens=10, cost_usd=0.25), "b": NodeResult("b", input_tokens=10, cost_usd=0.5)})
        text = render_ascii(trace_from_store(GraphStore(workspace=str(tmp_path)), out["run_id"]))
        assert "cost $0.75" in text and "unpriced" not in text

    def test_hostile_text_in_a_node_id_cannot_inject_terminal_sequences(self):
        trace = {"graph_id": "g", "run_id": "r", "status": "succeeded", "tokens": {"input": 1, "output": 1}, "cost_usd": 0, "unpriced_nodes": ["\x1b[31mevil\x1b[0m"],
                 "results_by_node": {}}
        assert "\x1b" not in render_ascii(trace)

    def test_the_command_line_and_the_repl_print_the_same_totals(self, tmp_path, capsys):
        from wisp.coding import trace_text
        from wisp.graph.cli import main

        run_id, store = self._stored(tmp_path)
        assert "2200" in trace_text(store, run_id)
        assert main(["trace", run_id], workspace=str(tmp_path)) == 0
        printed = capsys.readouterr().out
        assert "2200" in printed and "unpriced" in printed

    def test_metrics_and_inspect_carry_them_too(self, tmp_path, capsys):
        import json

        from wisp.graph.cli import main

        run_id, _ = self._stored(tmp_path)
        assert main(["metrics", run_id], workspace=str(tmp_path)) == 0
        metrics = json.loads(capsys.readouterr().out)
        assert metrics["token_amplification"] == {"input": 2200, "output": 140}
        assert main(["inspect", run_id], workspace=str(tmp_path)) == 0
        assert "2200" in capsys.readouterr().out
