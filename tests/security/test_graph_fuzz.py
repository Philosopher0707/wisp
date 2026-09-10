"""Fuzzing: DSL, ids, labels, URIs, verdicts, persisted rows.

THREAT: malformed/crafted inputs crash the host, corrupt state, or slip
an authorization transition.
EXPECTED: ValueError/controlled rejects only; randomized graph shapes
always terminate with a terminal run status.
"""

from __future__ import annotations

import asyncio
import json
import random
import string

import pytest

from wisp.graph.artifacts import ArtifactStore
from wisp.graph.control import resolve_route
from wisp.graph.dsl import graph_from_dict, graph_from_yaml
from wisp.graph.executor import GraphExecutor
from wisp.graph.store import GraphStore
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    NodeContract,
    NodeResult,
    NodeStatus,
    NodeType,
)
from wisp.graph.validator import validate_graph
from wisp.graph.verifier import normalize_verdict
from .conftest import _no_sleep


async def _null_runner(node, inputs):
    return NodeResult(node.id, NodeStatus.SUCCESS, output={"n": 1})


def _rand_str(rng, n=12, alphabet=None):
    return "".join(rng.choice(alphabet or (string.ascii_letters + string.digits + ":;/-_. \x00é→")) for _ in range(n))


class TestFuzzDSL:
    @pytest.mark.parametrize("seed", range(50))
    def test_random_yaml_never_executes(self, seed):
        rng = random.Random(10_000 + seed)
        kind = rng.random()
        if kind < 0.3:
            doc = _rand_str(rng, rng.randint(0, 500))
        elif kind < 0.6:
            doc = "graph: {" + _rand_str(rng, rng.randint(0, 300)) + "}"
        else:
            doc = ("graph: {id: %s, entry: %s, nodes: {%s: {type: %s}}, edges: [%s]}" % (
                _rand_str(rng, 8), _rand_str(rng, 8), _rand_str(rng, 8),
                rng.choice(["agent", "join", "router", "bogus", "AGENT", ""]),
                _rand_str(rng, 20)))
        try:
            g = graph_from_yaml(doc)
        except ValueError:
            return  # fail-closed: acceptable
        errors = validate_graph(g)  # must not crash
        assert isinstance(errors, list)

    @pytest.mark.parametrize("seed", range(50))
    def test_random_dicts_never_crash_validator(self, seed):
        rng = random.Random(20_000 + seed)
        nodes = {}
        for i in range(rng.randint(0, 6)):
            nodes[_rand_str(rng, 6)] = {"type": rng.choice(["agent", "join", 1, None, []])}
        try:
            g = graph_from_dict({"graph": {"id": "f", "entry": "a",
                                           "nodes": nodes, "edges": []}})
        except ValueError:
            return
        assert isinstance(validate_graph(g), list)

    def test_yaml_anchors_do_not_explode(self):
        bomb = "a: &x [" + ",".join(["1"] * 100000) + "]\n" * 1
        bomb += "graph: {id: x, entry: a, nodes: {a: {type: agent}}, extra: *x}"
        try:
            graph_from_yaml(bomb)
        except ValueError:
            pass  # capped or rejected: fine


class TestFuzzBoundaries:
    @pytest.mark.parametrize("seed", range(100))
    def test_labels_and_uris(self, seed):
        rng = random.Random(30_000 + seed)
        label = _rand_str(rng, rng.randint(0, 500))
        target = resolve_route({"a": "n1"}, "n0", label)
        assert target in ("n0", "n1")  # never escapes the table
        v = normalize_verdict({"decision": label, "lane": label,
                               "reason_codes": [label], "evidence": [label]})
        assert v.decision in ("ALLOW", "REJECT", "RETRY", "ESCALATE")
        assert len(json.dumps({"v": v.decision})) < 100

    @pytest.mark.parametrize("seed", range(30))
    def test_random_topologies_terminate(self, tmp_path, seed):
        rng = random.Random(40_000 + seed)
        n = rng.randint(1, 10)
        names = [f"n{i}" for i in range(n)]
        nodes = tuple(GraphNode(id=x, type=NodeType.AGENT,
                                contract=NodeContract(id=x)) for x in names)
        edges = []
        for _ in range(rng.randint(0, n * 2)):
            a, b = rng.choice(names), rng.choice(names)
            if a != b:
                edges.append(EdgeMapping(a, b, reason="fuzz"))
        g = Graph(id="fz", entrypoint=names[0], nodes=nodes, edges=tuple(edges),
                  policies=GraphPolicy(max_depth=16))
        if validate_graph(g):
            return
        ws = str(tmp_path / f"s{seed}")
        ex = GraphExecutor(runner=_null_runner, workspace=ws,
                           store=GraphStore(workspace=ws), sleep=_no_sleep)
        r = asyncio.run(ex.run(g, {}))
        assert r["status"] in ("succeeded", "failed", "cancelled")

    def test_hostile_artifact_ids(self, tmp_path):
        s = ArtifactStore(workspace=str(tmp_path),
                          store=GraphStore(workspace=str(tmp_path)))
        for bad in ["artifact://", "artifact://" + "x" * 200, "x", "", "artifact://a/b",
                    "artifact://.hidden", 123, None]:
            with pytest.raises((ValueError, LookupError, OSError, TypeError)):
                s.get(bad, "r1")  # type: ignore[arg-type]
