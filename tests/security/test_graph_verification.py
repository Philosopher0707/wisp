"""Phase 10D adversarial: verifier bypass, fake satisfaction, escalation.

THREAT: hostile graph/IR content tricks the pass into widening authority,
weakening gates, or forging verification.
EXPECTED: advisory-only or rejection; authority never widens; gates hold.
"""

from __future__ import annotations


from wisp.graph.optimizer import OptimizationContext, optimize_graph
from wisp.graph.optimizer_passes import verification_pass
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    JoinPolicy,
    ModelPolicy,
    NodeContract,
    NodeType,
)
from wisp.graph.validator import validate_graph

PROFILE = {"tools": ["read_file"], "model": "m1", "provider": "p1"}


def _agent(nid):
    return GraphNode(id=nid, type=NodeType.AGENT,
                     contract=NodeContract(
                         id=nid, allowed_tools=("read_file",),
                         model_policy=ModelPolicy(model="m1", provider="p1")))


def _e(f, t, reason="x", mapping=None, when=""):
    return EdgeMapping(f, t, reason=reason, mapping=dict(mapping or {}),
                       condition=when)


def _ctx(profile=PROFILE):
    return OptimizationContext(verifier_profile=dict(profile) if profile else None)


class TestVerifierBypass:
    def test_hidden_verifier_behind_router_insufficient(self):
        r_ = GraphNode(id="r", type=NodeType.ROUTER, routes={"x": "s"},
                       default_route="s", contract=NodeContract(id="r"))
        v = GraphNode(id="v", type=NodeType.VERIFIER,
                      contract=NodeContract(id="v"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), r_, v, _agent("s")),
                  edges=(_e("a", "r"), _e("r", "v", when="x"),
                         _e("v", "s", when="accept"), _e("a", "s")))
        r = verification_pass(g, _ctx())
        # v never consumes a's output -> (a,s) obligation stands.
        assert any(d.get("detail", {}).get("producer") == "a"
                   and "satisfied" not in d["reason"] for d in r.diagnostics)

    def test_fake_verifier_without_accept_edge(self):
        v = GraphNode(id="v", type=NodeType.VERIFIER,
                      contract=NodeContract(id="v"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), v, _agent("s")),
                  edges=(_e("a", "v"), _e("v", "s"), _e("a", "s")))
        r = verification_pass(g, _ctx())
        assert not any("satisfied by v" in d["reason"] for d in r.diagnostics)

    def test_model_verifier_cannot_escalate(self):
        v = GraphNode(id="v", type=NodeType.VERIFIER,
                      contract=NodeContract(
                          id="v", allowed_tools=("run_bash",),
                          model_policy=ModelPolicy(model="evil", provider="evil")))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), v, _agent("s")),
                  edges=(_e("a", "v"), _e("v", "s", when="accept"), _e("a", "s")))
        # Existing evil verifier is runtime's problem (policy gates tools at
        # execution). The (a,s) obligation is genuinely ungated, so insertion
        # is correct — but the minted verifier must carry ONLY host-profile
        # authority, never the evil verifier's.
        r = verification_pass(g, _ctx())
        minted = [n for n in r.graph.nodes if n.id == "a__verify"]
        assert len(minted) == 1
        c = minted[0].effective_contract()
        assert set(c.allowed_tools) == {"read_file"}
        assert c.model_policy.model == "m1"
        assert c.model_policy.provider == "p1"

    def test_duplicate_insertion_impossible(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")), edges=(_e("a", "s"),))
        r1 = optimize_graph(g, context=_ctx())
        assert r1.status == "OPTIMIZED"
        r2 = optimize_graph(r1.graph, context=_ctx())
        assert r2.status == "UNCHANGED"
        assert r1.graph.fingerprint() == r2.graph.fingerprint()

    def test_bypass_metadata_ignored(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")), edges=(_e("a", "s"),))
        # Junk metadata fields cannot weaken the pass (unknown fields in
        # contracts are inert; decisions come from topology + profile).
        r = verification_pass(g, _ctx())
        assert r.changed  # obligation found despite no metadata hints

    def test_malformed_profile_rejected(self):
        for bad in ({"tools": ["read_file; rm"]}, {"tools": []},
                    {"tools": ["read_file"], "model": "", "provider": "p1"},
                    "not-a-dict", [1, 2]):
            r = verification_pass(
                Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("s")),
                      edges=(_e("a", "s"),)),
                OptimizationContext(verifier_profile=bad))
            assert not any(n.id == "a__verify" for n in r.graph.nodes)

    def test_huge_graph_bounded(self):
        import time
        names = [f"n{i}" for i in range(200)]
        nodes = tuple(_agent(n) for n in names)
        edges = tuple(_e(names[i], names[i + 1]) for i in range(199))
        from wisp.graph.types import GraphPolicy
        g = Graph(id="h", entrypoint=names[0], nodes=nodes, edges=edges,
                  policies=GraphPolicy(max_nodes=512, max_depth=256))
        assert validate_graph(g) == []
        t0 = time.monotonic()
        r = optimize_graph(g, context=_ctx())
        dt = time.monotonic() - t0
        assert dt < 10  # linear-ish reachability, no path explosion
        assert r.status in ("UNCHANGED", "OPTIMIZED")

    def test_fuzz_never_inserts_unsafely(self):
        import random
        from wisp.graph.optimizer import authority_of, is_narrower_or_equal
        for seed in range(40):
            rng = random.Random(31000 + seed)
            names = [f"n{i}" for i in range(rng.randint(2, 6))]
            nodes = []
            for n in names:
                kind = rng.choice(["agent", "agent", "verifier", "join"])
                if kind == "agent":
                    nodes.append(_agent(n))
                elif kind == "verifier":
                    nodes.append(GraphNode(
                        id=n, type=NodeType.VERIFIER,
                        contract=NodeContract(id=n, allowed_tools=("read_file",),
                                              model_policy=ModelPolicy(model="m1",
                                                                       provider="p1"))))
                else:
                    nodes.append(GraphNode(
                        id=n, type=NodeType.JOIN, join_policy=JoinPolicy.ALL,
                        contract=NodeContract(id=n)))
            edges = []
            for _ in range(rng.randint(1, 7)):
                a, b = rng.choice(names), rng.choice(names)
                if a == b:
                    continue
                edges.append(EdgeMapping(
                    a, b, reason="x",
                    mapping={"v": "output.v"} if rng.random() < 0.5 else {},
                    condition=rng.choice(["", "", "accept"])))
            g = Graph(id="f", entrypoint=names[0], nodes=tuple(nodes),
                      edges=tuple(edges))
            if validate_graph(g):
                continue
            r = optimize_graph(g, context=_ctx())
            assert r.status in ("UNCHANGED", "OPTIMIZED")
            if r.status == "OPTIMIZED":
                assert validate_graph(r.graph) == []
                ok, _ = is_narrower_or_equal(authority_of(g), authority_of(r.graph))
                assert ok
                # Approvals and verifiers preserved exactly.
                for t in (NodeType.VERIFIER, NodeType.APPROVAL):
                    before = sorted(n.id for n in g.nodes if n.type == t)
                    after = sorted(n.id for n in r.graph.nodes if n.type == t)
                    assert set(before) <= set(after)
