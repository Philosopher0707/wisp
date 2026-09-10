"""Coding-agent reference graph: planner -> repomap -> splitter ->
implementation/security/tests (bounded, isolated contexts) -> merger ->
independent review -> gate -> tests -> final.

Workers return patches/artifacts; the merge node combines deterministically.
"""

from __future__ import annotations

from wisp.graph.types import (
    CycleSpec,
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    JoinPolicy,
    NodeContract,
    NodeType,
    RetryPolicy,
)

REFERENCE_YAML = """\
version: "1"
graph:
  id: coding-agent
  entry: planner
  nodes:
    planner: {type: agent, responsibility: decompose request into a scoped plan}
    repomap: {type: agent, responsibility: map repository symbols and blast radius}
    splitter:
      type: function
      function: split_work
      responsibility: partition plan into independent slices
    implement: {type: agent, responsibility: implement the slice, cycle: {entry: implement, body: [merger, review], exit: gate, max_iterations: 3}}
    security: {type: agent, responsibility: security review of the slice}
    tests: {type: agent, responsibility: exercise the slice with tests}
    merger:
      type: join
      join_policy: best_effort
    review:
      type: verifier
      responsibility: independent review of the merged change
    gate:
      type: gate
      function: release_gate
    final_tests: {type: agent, responsibility: run the full test suite}
    final: {type: agent, responsibility: summarize the change}
  edges:
    - {from: planner, to: repomap, reason: repomap consumes planner.output.scope}
    - {from: repomap, to: splitter, reason: splitter consumes repomap.output.symbols}
    - {from: splitter, to: implement, reason: implement consumes splitter.output.slice}
    - {from: splitter, to: security, reason: security consumes splitter.output.slice}
    - {from: splitter, to: tests, reason: tests consumes splitter.output.slice}
    - {from: implement, to: merger, reason: merger consumes implement.output.patch}
    - {from: security, to: merger, reason: merger consumes security.output.review}
    - {from: tests, to: merger, reason: merger consumes tests.output.report}
    - {from: merger, to: review, reason: review consumes merger.output.change}
    - {from: review, to: gate, reason: gate consumes review.output.decision, when: accept}
    - {from: review, to: implement, reason: implement consumes review.output.corrections, when: reject.implement}
    - {from: gate, to: final_tests, reason: final_tests consumes gate.output.change, when: allow}
    - {from: final_tests, to: final, reason: final consumes final_tests.output.report}
"""


def _agent(nid: str, responsibility: str, **kw) -> GraphNode:
    c = NodeContract(id=nid, name=nid, description=responsibility,
                     allowed_tools=tuple(kw.get("allowed_tools", ("all",))),
                     retry_policy=RetryPolicy(max_attempts=kw.get("retries", 1)))
    return GraphNode(id=nid, type=NodeType.AGENT, contract=c,
                     config={"prompt": responsibility})


def coding_agent_graph() -> Graph:
    impl = _agent("implement", "implement the slice")
    # Controlled correction cycle: review may send implement back exactly
    # max_iterations times; the gate exits on evidence (tests_green).
    impl = GraphNode(id=impl.id, type=impl.type, contract=impl.contract,
                     config=impl.config,
                     cycle=CycleSpec(entry="implement", body=("merger", "review"),
                                     exit_gate="gate", max_iterations=3))
    sec = _agent("security", "security review of the slice")
    tst = _agent("tests", "exercise the slice with tests")
    nodes = [
        _agent("planner", "decompose request into a scoped plan"),
        _agent("repomap", "map repository symbols and blast radius"),
        GraphNode(id="splitter", type=NodeType.FUNCTION, function="split_work",
                  contract=NodeContract(id="splitter", name="splitter")),
        impl, sec, tst,
        GraphNode(id="merger", type=NodeType.JOIN, join_policy=JoinPolicy.BEST_EFFORT,
                  contract=NodeContract(id="merger", name="merger")),
        GraphNode(id="review", type=NodeType.VERIFIER,
                  contract=NodeContract(id="review", name="review",
                                        retry_policy=RetryPolicy(max_attempts=1))),
        GraphNode(id="gate", type=NodeType.GATE, function="release_gate",
                  contract=NodeContract(id="gate", name="gate")),
        _agent("final_tests", "run the full test suite"),
        _agent("final", "summarize the change"),
    ]
    e = lambda f, t, r, when="": EdgeMapping(f, t, reason=r, condition=when)  # noqa: E731
    edges = [
        e("planner", "repomap", "repomap consumes planner.output.scope"),
        e("repomap", "splitter", "splitter consumes repomap.output.symbols"),
        e("splitter", "implement", "implement consumes splitter.output.slice"),
        e("splitter", "security", "security consumes splitter.output.slice"),
        e("splitter", "tests", "tests consumes splitter.output.slice"),
        e("implement", "merger", "merger consumes implement.output.patch"),
        e("security", "merger", "merger consumes security.output.review"),
        e("tests", "merger", "merger consumes tests.output.report"),
        e("merger", "review", "review consumes merger.output.change"),
        e("review", "gate", "gate consumes review.output.decision", "accept"),
        e("review", "implement", "implement consumes review.output.corrections",
          "reject.implement"),
        e("gate", "final_tests", "final_tests consumes gate.output.change", "allow"),
        e("final_tests", "final", "final consumes final_tests.output.report"),
    ]
    return Graph(id="coding-agent", version="1", entrypoint="planner",
                 nodes=tuple(nodes), edges=tuple(edges),
                 policies=GraphPolicy(max_concurrency=8))


def default_functions() -> dict:
    """Deterministic helpers wired by the host app (RepoMap, test results…)."""
    from wisp.graph.control import split_by_items

    def split_work(inputs: dict) -> dict:
        items = inputs.get("symbols", inputs.get("items", []))
        if isinstance(items, dict):
            items = list(items.values())
        return {"slices": split_by_items(list(items), 4)}

    def release_gate(inputs: dict) -> dict:
        ok = bool(inputs.get("tests_green", inputs.get("allowed", False)))
        return {"allowed": ok,
                "reason": "evidence green" if ok else "evidence missing"}
    return {"split_work": split_work, "release_gate": release_gate}
