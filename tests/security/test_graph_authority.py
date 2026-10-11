"""Authority confinement: no graph-defined value may grant authority.

THREAT: malicious graph author / model output escalates tools, models,
providers, workspace, or function bindings beyond active policy.
EXPECTED: validator fails closed AND runtime enforcement (ToolExecutor)
never sees unfiltered requests. These tests pin the graph layer's half:
fail-closed validation + safe runner plumbing.
"""

from __future__ import annotations

import pytest

from wisp.graph.dsl import graph_from_yaml
from wisp.graph.types import (
    Graph,
    GraphNode,
    GraphPolicy,
    ModelPolicy,
    NodeContract,
    NodeType,
)
from wisp.graph.validator import validate_graph
from .conftest import make_executor, ok_runner


def _agent(nid, **kw):
    return GraphNode(id=nid, type=NodeType.AGENT, contract=NodeContract(id=nid, **kw))


class TestToolConfinement:
    def test_all_tools_rejected_under_restrictive_policy(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=(),
                  policies=GraphPolicy(allowed_tools=("read_file",)))
        assert any("all" in e for e in validate_graph(g))

    def test_unknown_tool_name_rejected_by_charset(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a", allowed_tools=("run_bash; rm -rf",)),), edges=())
        assert validate_graph(g) != []

    def test_bypass_permission_rejected(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a", permissions=("bypass_approval",)),), edges=())
        assert any("bypass" in e for e in validate_graph(g))

    def test_mcp_qualified_names_allowed_but_checked(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a", allowed_tools=("server:tool",)),), edges=(),
                  policies=GraphPolicy(allowed_tools=("server:tool",)))
        assert validate_graph(g) == []

    def test_runner_tools_come_from_contract_only(self):
        """Runner must forward contract tools verbatim — never merge model input."""
        seen = {}

        async def spy(node, inputs):
            seen["node"] = node.id
            raise RuntimeError("stop")

        import asyncio

        async def main(tmp_path):
            from wisp.graph.runner import SubagentNodeRunner
            orch_calls = []

            class FakeOrch:
                async def run(self, sub):
                    orch_calls.append(sub)
                    from wisp.multi_agent.task import SubagentResult
                    return SubagentResult(task_id="x", success=True, output={})

            r = SubagentNodeRunner(FakeOrch(), str(tmp_path))
            node = _agent("n", allowed_tools=("read_file",))
            await r(node, {"task": "x", "injected_tools": ["run_bash"]})
            return orch_calls

        calls = asyncio.run(main(__import__("tempfile").mkdtemp()))
        assert calls[0].tools == ["read_file"]

    @pytest.mark.asyncio
    async def test_graph_policy_never_widens_tool_set(self, tmp_path):
        """Even if validator were skipped, the runner only narrows."""
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a", allowed_tools=("read_file",)),),
                  edges=())
        ex = make_executor(tmp_path, ok_runner())
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"


class TestModelProviderConfinement:
    def test_unauthorized_model_rejected(self):
        c = NodeContract(id="a", model_policy=ModelPolicy(model="evil-9"))
        g = Graph(id="t", entrypoint="a", nodes=(GraphNode(id="a", contract=c),),
                  edges=(), policies=GraphPolicy(allowed_models=("good",)))
        assert any("evil-9" in e for e in validate_graph(g))

    def test_unauthorized_provider_rejected(self):
        c = NodeContract(id="a", model_policy=ModelPolicy(provider="evil"))
        g = Graph(id="t", entrypoint="a", nodes=(GraphNode(id="a", contract=c),),
                  edges=(), policies=GraphPolicy(allowed_providers=("good-p",)))
        assert any("evil" in e for e in validate_graph(g))

    def test_unknown_model_class_rejected_in_dsl(self):
        with pytest.raises(ValueError):
            graph_from_yaml("graph: {id: x, entry: a, nodes: {a: {type: agent, model: superintelligent}}}")

    @pytest.mark.asyncio
    async def test_workspace_escape_refused(self, tmp_path):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=(),
                  policies=GraphPolicy(workspace="/etc"))
        ex = make_executor(tmp_path, ok_runner())
        with pytest.raises(PermissionError):
            await ex.run(g, {})

    @pytest.mark.asyncio
    async def test_cross_workspace_resume_refused(self, tmp_path):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner())
        r = await ex.run(g, {}, run_id="ws-run")
        assert r["status"] == "succeeded"
        other = str(tmp_path / "other")
        import os
        os.makedirs(other, exist_ok=True)
        ex2 = make_executor(tmp_path / "other", ok_runner())
        # fresh store in other ws: unknown run
        with pytest.raises(KeyError):
            await ex2.resume(g, "ws-run")


class TestModelTiers:
    """A graph says how much model a node deserves (`cheap | standard | strong`); only the operator's map says which model that is.

    The graph, its planner and every node input are untrusted, so none of them can name a model the subagent will run."""

    @staticmethod
    def run_node(tmp_path, node, tiers=None, inputs=None, session_model=""):
        import asyncio
        from types import SimpleNamespace

        from wisp.graph.runner import SubagentNodeRunner
        from wisp.multi_agent.task import SubagentResult

        calls = []

        class FakeOrch:
            config = SimpleNamespace(model=session_model)

            async def run(self, sub):
                calls.append(sub)
                return SubagentResult(task_id="x", success=True, output={})

        kwargs = {} if tiers is None else {"model_tiers": tiers}
        result = asyncio.run(SubagentNodeRunner(FakeOrch(), str(tmp_path), **kwargs)(node, inputs or {"task": "x"}))
        return calls[0], result

    @staticmethod
    def tiered(tier, **policy):
        return _agent("n", model_policy=ModelPolicy(model_class=tier, **policy))

    def test_a_tier_reaches_the_subagent_only_through_the_operators_map(self, tmp_path):
        sub, _ = self.run_node(tmp_path, self.tiered("cheap"), tiers={"cheap": "tiny-model"})
        assert sub.model == "tiny-model"

    def test_a_tier_nobody_mapped_leaves_the_session_model(self, tmp_path):
        sub, _ = self.run_node(tmp_path, self.tiered("cheap"), tiers={"strong": "big-model"})
        assert sub.model is None

    def test_no_map_at_all_changes_nothing(self, tmp_path):
        for tier in ("cheap", "standard", "strong"):
            assert self.run_node(tmp_path, self.tiered(tier))[0].model is None

    def test_each_node_gets_its_own_tier(self, tmp_path):
        tiers = {"cheap": "c", "standard": "s", "strong": "x"}
        assert [self.run_node(tmp_path, self.tiered(t), tiers=tiers)[0].model for t in ("cheap", "standard", "strong")] == ["c", "s", "x"]

    def test_the_default_tier_is_standard_and_maps_like_any_other(self, tmp_path):
        sub, _ = self.run_node(tmp_path, _agent("n"), tiers={"standard": "std-model"})
        assert sub.model == "std-model"

    def test_a_model_the_graph_names_never_reaches_the_subagent(self, tmp_path):
        node = self.tiered("cheap", model="evil-9", provider="evil", fallback_chain=("evil-10",))
        assert self.run_node(tmp_path, node, tiers={})[0].model is None
        assert self.run_node(tmp_path, node, tiers={"cheap": "tiny-model"})[0].model == "tiny-model"

    def test_node_inputs_cannot_pick_a_model_or_a_tier(self, tmp_path):
        inputs = {"task": "x", "model": "evil-9", "model_class": "strong", "model_tiers": {"cheap": "evil-9"}}
        assert self.run_node(tmp_path, self.tiered("cheap"), tiers={"strong": "big-model"}, inputs=inputs)[0].model is None

    def test_the_node_result_records_the_model_it_was_asked_to_use(self, tmp_path):
        _, result = self.run_node(tmp_path, self.tiered("cheap"), tiers={"cheap": "tiny-model"}, session_model="session-model")
        assert result.model == "tiny-model"

    def test_an_unmapped_node_records_the_session_model_so_a_trace_always_says_which_ran(self, tmp_path):
        _, result = self.run_node(tmp_path, self.tiered("strong"), tiers={"cheap": "tiny-model"}, session_model="session-model")
        assert result.model == "session-model"

    def test_the_stock_executor_reads_the_setting_from_the_config(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.graph.runner import default_executor

        ex = default_executor(str(tmp_path), config=WispConfig().replace(graph_model_tiers="cheap=tiny-model,strong=big-model"))
        assert ex._runner.model_tiers == {"cheap": "tiny-model", "strong": "big-model"}

    def test_the_stock_executor_without_a_config_reads_the_environment(self, tmp_path, monkeypatch):
        from wisp.graph.runner import default_executor

        monkeypatch.setenv("WISP_GRAPH_MODEL_TIERS", "cheap=env-model")
        assert default_executor(str(tmp_path))._runner.model_tiers == {"cheap": "env-model"}

    def test_the_stock_executor_with_nothing_set_has_an_empty_map(self, tmp_path, monkeypatch):
        from wisp.graph.runner import default_executor

        monkeypatch.delenv("WISP_GRAPH_MODEL_TIERS", raising=False)
        assert default_executor(str(tmp_path))._runner.model_tiers == {}

    def test_the_map_the_runner_reports_is_a_copy(self, tmp_path):
        from wisp.graph.runner import SubagentNodeRunner

        runner = SubagentNodeRunner(object(), str(tmp_path), model_tiers={"cheap": "a"})
        runner.model_tiers["cheap"] = "evil"
        assert runner.model_tiers == {"cheap": "a"}

    def test_the_runner_keeps_its_own_copy_of_the_map_it_was_given(self, tmp_path):
        from wisp.graph.runner import SubagentNodeRunner

        given = {"cheap": "a"}
        runner = SubagentNodeRunner(object(), str(tmp_path), model_tiers=given)
        given["cheap"] = "evil"
        assert runner.model_tiers == {"cheap": "a"}

    def test_a_result_that_names_its_model_wins_over_what_the_runner_asked_for(self, tmp_path):
        import asyncio
        from types import SimpleNamespace

        from wisp.graph.runner import SubagentNodeRunner

        class Orch:
            config = SimpleNamespace(model="session-model")

            async def run(self, sub):
                return SimpleNamespace(success=True, output={}, model_used="what-actually-ran")

        result = asyncio.run(SubagentNodeRunner(Orch(), str(tmp_path), model_tiers={"cheap": "asked"})(self.tiered("cheap"), {"task": "x"}))
        assert result.model == "what-actually-ran"


class TestConfiguredTiers:
    """Where the stock executor finds the operator's map."""

    def test_a_config_that_carries_the_setting_is_the_authority_even_when_it_is_empty(self, monkeypatch):
        from types import SimpleNamespace

        from wisp.graph.runner import _configured_tiers

        monkeypatch.setenv("WISP_GRAPH_MODEL_TIERS", "cheap=from-env")
        assert _configured_tiers(SimpleNamespace(graph_model_tiers="strong=from-config")) == {"strong": "from-config"}
        assert _configured_tiers(SimpleNamespace(graph_model_tiers="")) == {}

    def test_a_config_without_the_setting_falls_back_to_the_environment(self, monkeypatch):
        from types import SimpleNamespace

        from wisp.graph.runner import _configured_tiers

        monkeypatch.setenv("WISP_GRAPH_MODEL_TIERS", "cheap=from-env")
        assert _configured_tiers(SimpleNamespace()) == {"cheap": "from-env"}
        assert _configured_tiers(None) == {"cheap": "from-env"}

    def test_an_unreadable_config_file_means_no_tiers_not_a_stopped_graph(self, monkeypatch, tmp_path, caplog):
        import logging

        import wisp.config as cfg_mod
        from wisp.graph.runner import _configured_tiers

        broken = tmp_path / "config.json"
        broken.write_text("{ this is not json")
        monkeypatch.setattr(cfg_mod, "get_config_path", lambda: broken)
        monkeypatch.delenv("WISP_GRAPH_MODEL_TIERS", raising=False)
        with caplog.at_level(logging.WARNING, logger="wisp.graph.runner"):
            assert _configured_tiers(None) == {}
        assert "could not be read" in " ".join(r.getMessage() for r in caplog.records)
