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
