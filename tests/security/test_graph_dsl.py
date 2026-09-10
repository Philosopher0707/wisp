"""DSL / YAML parser attacks: fail closed, never crash, never escalate.

THREAT: malicious graph author crafts YAML to crash the host, smuggle
privilege (idempotent:"false"), or bypass caps.
EXPECTED: strict typed errors (ValueError naming the path) or clean rejects.
"""

from __future__ import annotations

import pytest

from wisp.graph.dsl import MAX_YAML_BYTES, graph_from_dict, graph_from_yaml
from wisp.graph.validator import validate_graph


class TestStrictCoercion:
    def test_quoted_false_idempotent_stays_false(self):
        g = graph_from_yaml("graph: {id: x, entry: a, nodes: {a: {type: agent}}}")
        assert g.nodes[0].effective_contract().idempotent is True

    def test_string_false_rejected_not_coerced(self):
        with pytest.raises(ValueError, match="idempotent"):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent, idempotent: "false"}}}')

    def test_string_tools_rejected(self):
        with pytest.raises(ValueError, match="allowed_tools"):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent, allowed_tools: all}}}')

    def test_nan_timeout_rejected(self):
        with pytest.raises(ValueError):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent, timeout: .nan}}}')

    def test_inf_timeout_rejected(self):
        with pytest.raises(ValueError):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent, timeout: .inf}}}')

    def test_unknown_node_type_is_clean_error(self):
        with pytest.raises(ValueError, match="unknown node type"):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: pwn}}}')

    def test_unknown_join_policy_is_clean_error(self):
        with pytest.raises(ValueError, match="unknown join policy"):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: join, join_policy: eventually}}}')

    def test_non_dict_root_rejected(self):
        with pytest.raises(ValueError):
            graph_from_dict(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_nodes_list_rejected(self):
        with pytest.raises(ValueError, match="nodes"):
            graph_from_dict({"graph": {"id": "x", "entry": "a", "nodes": ["a"]}})

    def test_edge_without_arrow_rejected(self):
        with pytest.raises(ValueError):
            graph_from_dict({"graph": {"id": "x", "entry": "a",
                                       "nodes": {"a": {"type": "agent"}},
                                       "edges": ["justastring"]}})

    def test_negative_budgets_rejected(self):
        with pytest.raises(ValueError):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent}}, '
                            'policies: {budget: {max_cost_usd: -5}}}')

    def test_huge_max_nodes_rejected(self):
        with pytest.raises(ValueError):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent}}, '
                            'policies: {max_nodes: 99999999}}')

    def test_negative_retries_rejected(self):
        with pytest.raises(ValueError):
            graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent}}, '
                            'policies: {max_retries: -1}}')

    def test_oversize_document_rejected(self):
        with pytest.raises(ValueError, match="too large"):
            graph_from_yaml("x: " + "y" * (MAX_YAML_BYTES + 1))

    def test_workspace_and_approvals_parse(self):
        g = graph_from_yaml('graph: {id: x, entry: a, nodes: {a: {type: agent}}, '
                            'policies: {workspace: /tmp/w, approval_required: [publish]}}')
        assert g.policies.workspace == "/tmp/w"
        assert g.policies.approval_required == ("publish",)

    def test_backoff_fields_parse(self):
        g = graph_from_yaml('graph: {id: x, entry: a, nodes: '
                            '{a: {type: agent, retry: {max_attempts: 3, backoff_base: 1.5}}}}')
        assert g.nodes[0].effective_contract().retry_policy.backoff_base_s == 1.5


class TestValidatorCloses:
    def test_empty_graph(self):
        from wisp.graph.types import Graph
        assert validate_graph(Graph(id="e", entrypoint="", nodes=(), edges=())) != []

    def test_bad_node_id_charset(self):
        from wisp.graph.types import Graph, GraphNode, NodeType, NodeContract
        g = Graph(id="t", entrypoint="a:b", nodes=(
            GraphNode(id="a:b", type=NodeType.AGENT, contract=NodeContract(id="a:b")),),
            edges=())
        assert any("node id" in e for e in validate_graph(g))

    def test_unknown_route_target(self):
        from wisp.graph.types import Graph, GraphNode, NodeType, NodeContract
        g = Graph(id="t", entrypoint="r",
                  nodes=(GraphNode(id="r", type=NodeType.ROUTER, routes={"x": "ghost"},
                                   contract=NodeContract(id="r")),),
                  edges=())
        assert any("ghost" in e for e in validate_graph(g))

    def test_missing_default_route_target(self):
        from wisp.graph.types import Graph, GraphNode, NodeType, NodeContract
        g = Graph(id="t", entrypoint="r",
                  nodes=(GraphNode(id="r", type=NodeType.ROUTER, routes={"x": "r"},
                                   default_route="ghost",
                                   contract=NodeContract(id="r")),),
                  edges=())
        assert any("default_route" in e for e in validate_graph(g))

    def test_function_node_without_function(self):
        from wisp.graph.types import Graph, GraphNode, NodeType, NodeContract
        g = Graph(id="t", entrypoint="f",
                  nodes=(GraphNode(id="f", type=NodeType.FUNCTION,
                                   contract=NodeContract(id="f")),), edges=())
        assert any("function" in e for e in validate_graph(g))

    def test_deep_graph_exceeds_max_depth(self):
        from wisp.graph.compat import chain_to_graph
        from wisp.graph.types import GraphPolicy
        g = chain_to_graph("c", [f"s{i}" for i in range(12)])
        g = type(g)(id=g.id, version=g.version, entrypoint=g.entrypoint,
                    nodes=g.nodes, edges=g.edges,
                    policies=GraphPolicy(max_concurrency=1, max_depth=8))
        assert any("depth" in e for e in validate_graph(g))
