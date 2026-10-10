"""A fan-in node must receive what every branch produced: two edges may not write the same input.

`GraphExecutor._derive_inputs` assigns each incoming edge's mapped values into one dict in edge order, so two edges that map to the same destination key
overwrite each other and the node sees only the last branch. `parallel_repo_analysis` did exactly that: four branches each mapped `findings` into `synthesize`,
and the synthesizer was told about one of them. Nothing failed and nothing was logged, which is why it went unnoticed (found while checking Wisp's graphs
against the "edges are data contracts" rule; see `docs/harness/graph-fan-in.md`).

These tests run the real executor with the host's function bindings; only the node runner is scripted.
"""

from __future__ import annotations

import asyncio
import json
import tempfile

import pytest

from wisp.graph import coding_graphs
from wisp.graph.dsl import graph_from_dict, graph_from_yaml
from wisp.graph.executor import GraphExecutor
from wisp.graph.reference import REFERENCE_YAML, default_functions
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    NodeContract,
    NodeResult,
    NodeStatus,
    NodeType,
)
from wisp.graph.validator import validate_graph


def _agent(nid: str) -> GraphNode:
    return GraphNode(id=nid, type=NodeType.AGENT, contract=NodeContract(id=nid, allowed_tools=("read_file",)))


def _edge(src: str, dst: str, mapping: dict | None = None, when: str = "") -> EdgeMapping:
    return EdgeMapping(src, dst, reason=f"{dst} consumes {src}", mapping=dict(mapping or {}), condition=when)


def _fan_in(*edges: EdgeMapping) -> Graph:
    """s fans out to a and b; `edges` are the ones under test (a and b into c)."""
    return Graph(id="g", entrypoint="s", nodes=tuple(_agent(n) for n in ("s", "a", "b", "c")),
                 edges=(_edge("s", "a"), _edge("s", "b"), *edges))


class TestTheValidatorNamesTheCollision:
    def test_two_edges_writing_the_same_input_are_rejected_with_both_sources_named(self):
        errors = validate_graph(_fan_in(_edge("a", "c", {"x": "output.v"}), _edge("b", "c", {"x": "output.v"})))
        assert any("'a'" in e and "'b'" in e and "'x'" in e and "'c'" in e for e in errors), errors

    def test_an_input_that_is_a_prefix_of_another_is_a_collision(self):
        errors = validate_graph(_fan_in(_edge("a", "c", {"x": "output.v"}), _edge("b", "c", {"x.y": "output.v"})))
        assert any("'x'" in e and "'x.y'" in e for e in errors), errors

    def test_the_prefix_rule_is_symmetric(self):
        errors = validate_graph(_fan_in(_edge("a", "c", {"x.y": "output.v"}), _edge("b", "c", {"x": "output.v"})))
        assert errors

    def test_distinct_inputs_are_accepted(self):
        assert validate_graph(_fan_in(_edge("a", "c", {"from_a": "output.v"}), _edge("b", "c", {"from_b": "output.v"}))) == []

    def test_sibling_paths_under_one_parent_are_distinct(self):
        assert validate_graph(_fan_in(_edge("a", "c", {"findings.a": "output.v"}), _edge("b", "c", {"findings.b": "output.v"}))) == []

    def test_a_key_that_only_shares_leading_characters_is_not_a_prefix(self):
        assert validate_graph(_fan_in(_edge("a", "c", {"find": "output.v"}), _edge("b", "c", {"findings": "output.v"}))) == []

    def test_conditional_edges_may_share_a_destination_because_only_one_lane_is_taken(self):
        assert validate_graph(_fan_in(_edge("a", "c", {"x": "output.v"}, when="accept"), _edge("b", "c", {"x": "output.v"}, when="reject"))) == []

    def test_edges_without_a_mapping_never_collide(self):
        assert validate_graph(_fan_in(_edge("a", "c"), _edge("b", "c"))) == []

    def test_two_unconditional_edges_from_the_same_source_writing_one_input_are_also_a_collision(self):
        errors = validate_graph(_fan_in(_edge("a", "c", {"x": "output.v"}), _edge("a", "c", {"x": "output.w"}), _edge("b", "c", {"y": "output.v"})))
        assert any("'a' and 'a'" in e for e in errors), errors

    def test_a_collision_between_edges_that_are_not_neighbours_is_found(self):
        errors = validate_graph(_fan_in(_edge("a", "c", {"x": "output.v"}), _edge("b", "c", {"y": "output.v"}), _edge("s", "c", {"x": "output.w"})))
        assert any("'a' and 's'" in e and "'x'" in e for e in errors), errors

    def test_two_mappings_on_one_edge_are_that_edges_own_business(self):
        assert validate_graph(_fan_in(_edge("a", "c", {"x": "output.v", "y": "output.w"}), _edge("b", "c", {"z": "output.v"}))) == []


class TestTheTemplatesAndTheReferenceGraphAreClean:
    @pytest.mark.parametrize("name", sorted(coding_graphs.TEMPLATES))
    def test_every_registered_coding_template_validates(self, name):
        graph = graph_from_dict(coding_graphs.TEMPLATES[name]([])["graph"])
        assert validate_graph(graph) == []

    def test_the_reference_graph_validates(self):
        assert validate_graph(graph_from_yaml(REFERENCE_YAML)) == []

    def test_whatever_the_router_can_pick_is_a_registered_template(self):
        picked = {coding_graphs.pick_template(o) for o in ("parallel implement two features", "audit the repo", "fix the bug", "refactor the module", "add a flag")}
        assert picked == set(coding_graphs.TEMPLATES)


def _run(graph: Graph, fail: frozenset[str] = frozenset()):
    seen: dict[str, dict] = {}

    async def runner(node, inputs):
        seen[node.id] = inputs
        if node.id in fail:
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="TIMEOUT", message="timed out")
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"findings": f"findings from {node.id}"})

    executor = GraphExecutor(runner=runner, workspace=tempfile.mkdtemp(), max_concurrency=8)
    for name, fn in default_functions().items():
        executor.register_function(name, fn)
    return asyncio.run(executor.run(graph, {"task": "analyze the repo"}, "")), seen


class TestTheSynthesizerSeesEveryBranch:
    def test_parallel_repo_analysis_hands_all_four_branches_findings_to_synthesize(self):
        graph = graph_from_dict(coding_graphs.parallel_repo_analysis([])["graph"])
        result, seen = _run(graph)
        assert result["status"] == "succeeded"
        text = json.dumps(seen["synthesize"])
        for branch in ("structure", "symbols", "deps", "tests"):
            assert f"findings from {branch}" in text, f"{branch} never reached synthesize: {seen['synthesize']}"

    def test_each_branchs_findings_stay_attributable_to_the_branch_that_made_them(self):
        graph = graph_from_dict(coding_graphs.parallel_repo_analysis([])["graph"])
        _result, seen = _run(graph)
        findings = seen["synthesize"]["findings"]
        assert {k: v for k, v in findings.items()} == {b: f"findings from {b}" for b in ("structure", "symbols", "deps", "tests")}
