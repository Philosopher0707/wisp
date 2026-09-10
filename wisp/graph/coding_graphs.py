"""Canonical coding-agent graph templates (Phase 11D–11G).

Host-authored IR dicts compiled through the existing pipeline
(compile_ir → optimize → validate → fingerprint). Roles are contracts;
capabilities come from ToolExecutor.authorize(), never from these files.
Repair uses the existing bounded CycleSpec, never invented topology.
"""

from __future__ import annotations

from typing import Any

READ = ["read_file", "list_files", "search_symbols", "search_codebase"]
WRITE = READ + ["write_file", "edit_file", "run_bash", "run_tests", "diagnose"]


def _agent(nid: str, responsibility: str, tools: list[str],
           output: str = "") -> dict[str, Any]:
    return {"type": "agent", "responsibility": responsibility,
            "allowed_tools": tools,
            "output_schema": {"type": "object"} if output else {},
            "timeout": 600, "retry": {"max_attempts": 1}}


def simple_analyze_implement_verify(ctx_files: list[str]) -> dict:
    """ANALYZE → IMPLEMENT → VERIFY. Small multi-step tasks."""
    return {"objective": "analyze, implement, verify",
            "execution_shape": "GRAPH",
            "graph": {
                "id": "coding-simple", "entry": "analyze",
                "nodes": {
                    "analyze": _agent("analyze", "analyze the request and locate relevant code", READ),
                    "implement": _agent("implement", "implement the change; report changed_files", WRITE),
                    "verify": {"type": "verifier", "responsibility": "verify the change with evidence",
                               "allowed_tools": READ},
                },
                "edges": [
                    {"from": "analyze", "to": "implement",
                     "reason": "implement consumes analyze.output.scope",
                     "mapping": {"scope": "output.scope"}},
                    {"from": "implement", "to": "verify",
                     "reason": "verify consumes implement.output.patch",
                     "mapping": {"patch": "output.patch"}},
                ]}}


def parallel_repo_analysis(ctx_files: list[str]) -> dict:
    """Split → STRUCTURE/SYMBOLS/DEPS/TESTS → SYNTHESIZE. Independent analysis."""
    branches = ["structure", "symbols", "deps", "tests"]
    focus = {"structure": "repository layout and module boundaries",
             "symbols": "key symbols and definitions",
             "deps": "dependency relationships and blast radius",
             "tests": "test layout and coverage gaps"}
    nodes: dict[str, Any] = {
        "split": {"type": "function", "function": "split_work",
                  "responsibility": "partition analysis"},
        "synthesize": _agent("synthesize", "synthesize branch findings", READ),
    }
    for b in branches:
        nodes[b] = _agent(b, f"analyze {focus[b]}; report findings", READ)
    edges = [{"from": "split", "to": b,
              "reason": f"{b} consumes split.output.slice"} for b in branches]
    edges += [{"from": b, "to": "synthesize",
               "reason": f"synthesize consumes {b}.output.findings",
               "mapping": {"findings": "output.findings"}} for b in branches]
    return {"objective": "parallel repository analysis",
            "execution_shape": "GRAPH",
            "graph": {"id": "coding-parallel-analysis", "entry": "split",
                      "nodes": nodes, "edges": edges,
                      "policies": {"max_concurrency": 8}}}


def implement_review_repair(ctx_files: list[str]) -> dict:
    """IMPLEMENT → VERIFY ⇄ REPAIR (bounded ×3) → DONE. Bug fixes."""
    return {"objective": "implement, verify, repair until green",
            "execution_shape": "GRAPH",
            "graph": {
                "id": "coding-repair", "entry": "implement",
                "nodes": {
                    "implement": {
                        "type": "agent",
                        "responsibility": "implement the change; report changed_files",
                        "allowed_tools": WRITE, "timeout": 900,
                        "retry": {"max_attempts": 1},
                        "cycle": {"entry": "implement", "body": ["verify"],
                                  "exit": "verify", "max_iterations": 3}},
                    "verify": {"type": "verifier",
                               "responsibility": "verify with test evidence",
                               "allowed_tools": READ},
                    "done": _agent("done", "summarize the verified change", READ),
                },
                "edges": [
                    {"from": "implement", "to": "verify",
                     "reason": "verify consumes implement.output.patch",
                     "mapping": {"patch": "output.patch"}},
                    {"from": "verify", "to": "done",
                     "reason": "done consumes verify.output on accept", "when": "accept"},
                    {"from": "verify", "to": "implement",
                     "reason": "implement consumes verify.output.corrections",
                     "when": "reject.implement"},
                ]}}


def complex_coding(ctx_files: list[str]) -> dict:
    """ANALYZE → parallel analysis → PLAN → IMPLEMENT → TEST → REVIEW ⇄ REPAIR."""
    nodes: dict[str, Any] = {
        "analyze": _agent("analyze", "scope the request", READ),
        "arch": _agent("arch", "analyze architecture impact", READ),
        "deps": _agent("deps", "analyze dependencies and blast radius", READ),
        "plan": _agent("plan", "produce an implementation plan", READ),
        "implement": {
            "type": "agent", "responsibility": "implement the plan; report changed_files",
            "allowed_tools": WRITE, "timeout": 900, "retry": {"max_attempts": 1},
            "cycle": {"entry": "implement", "body": ["test", "review"],
                      "exit": "review", "max_iterations": 3}},
        "test": _agent("test", "run relevant tests; report results", READ + ["run_bash", "run_tests"]),
        "review": {"type": "verifier", "responsibility": "review with evidence",
                   "allowed_tools": READ},
        "final": _agent("final", "summarize the change", READ),
    }
    edges = [
        {"from": "analyze", "to": "arch", "reason": "arch consumes analyze.output.scope",
         "mapping": {"scope": "output.scope"}},
        {"from": "analyze", "to": "deps", "reason": "deps consumes analyze.output.scope",
         "mapping": {"scope": "output.scope"}},
        {"from": "arch", "to": "plan", "reason": "plan consumes arch.output.impact",
         "mapping": {"impact": "output.impact"}},
        {"from": "deps", "to": "plan", "reason": "plan consumes deps.output.blast_radius",
         "mapping": {"blast_radius": "output.blast_radius"}},
        {"from": "plan", "to": "implement", "reason": "implement consumes plan.output.steps",
         "mapping": {"steps": "output.steps"}},
        {"from": "implement", "to": "test", "reason": "test consumes implement.output.patch",
         "mapping": {"patch": "output.patch"}},
        {"from": "test", "to": "review", "reason": "review consumes test.output.report",
         "mapping": {"report": "output.report"}},
        {"from": "review", "to": "implement",
         "reason": "implement consumes review.output.corrections",
         "when": "reject.implement"},
        {"from": "review", "to": "final", "reason": "final on accept", "when": "accept"},
    ]
    return {"objective": "complex coding task",
            "execution_shape": "GRAPH",
            "graph": {"id": "coding-complex", "entry": "analyze",
                      "nodes": nodes, "edges": edges,
                      "policies": {"max_concurrency": 8}}}


# Static run inputs per template (host-defined, never model output).
TEMPLATE_RUN_INPUTS = {
    "parallel-implement-merge": {"workers": ["impl-a", "impl-b"]},
}


def parallel_implement_merge(ctx_files: list[str]) -> dict:
    """SETUP → IMPL-A + IMPL-B (isolated copies) → MERGE ⇄ → TEST → FINAL.

    Workers never touch canonical state; the MERGE router applies only on
    clean merge, routes conflicts to bounded repair, failures to report.
    """
    impl = lambda nid: {  # noqa: E731
        "type": "agent",
        "responsibility": "implement your slice in the isolated workspace; report summary",
        "allowed_tools": WRITE, "timeout": 900, "retry": {"max_attempts": 1}}
    nodes: dict[str, Any] = {
        "setup": {"type": "function", "function": "prepare_isolation",
                  "responsibility": "snapshot + isolated copies"},
        "impl-a": impl("impl-a"),
        "impl-b": impl("impl-b"),
        "merge": {"type": "router", "function": "merge_changesets",
                  "responsibility": "merge isolated work",
                  "routes": {"merged": "test", "conflict": "repair",
                             "stale": "fail", "invalid": "fail"},
                  "default_route": "fail"},
        "test": _agent("test", "run relevant tests on merged state",
                       READ + ["run_bash", "run_tests"]),
        "repair": _agent("repair", "resolve the reported conflicts completely; "
                                     "return resolutions, do not mutate files", READ),
        "merge2": {"type": "router", "function": "merge_changesets",
                   "responsibility": "merge repair outcome",
                   "routes": {"merged": "test2", "conflict": "fail",
                              "stale": "fail", "invalid": "fail"},
                   "default_route": "fail"},
        "test2": _agent("test2", "run relevant tests on repaired state",
                        READ + ["run_bash", "run_tests"]),
        "fail": {"type": "function", "function": "report_conflict",
                 "responsibility": "report unresolvable conflict"},
        "final": _agent("final", "summarize the merged change", READ),
        "final2": _agent("final2", "summarize the repaired change", READ),
    }
    edges = [
        {"from": "setup", "to": "impl-a",
         "reason": "impl-a consumes setup.output.copies",
         "mapping": {"isolated_workspace": "output.copies.impl-a",
                     "task_files": "output.files"}},
        {"from": "setup", "to": "impl-b",
         "reason": "impl-b consumes setup.output.copies",
         "mapping": {"isolated_workspace": "output.copies.impl-b",
                     "task_files": "output.files"}},
        {"from": "setup", "to": "merge",
         "reason": "merge consumes setup.output.snapshot",
         "mapping": {"snapshot": "output.snapshot", "copies": "output.copies"}},
        {"from": "impl-a", "to": "merge", "reason": "merge waits for impl-a"},
        {"from": "impl-b", "to": "merge", "reason": "merge waits for impl-b"},
        {"from": "merge", "to": "test", "reason": "test on merged", "when": "test",
         "mapping": {"applied": "output.applied"}},
        {"from": "merge", "to": "repair", "when": "repair",
         "reason": "repair consumes merge.output conflicts",
         "mapping": {"conflicts": "output.conflicts",
                     "non_conflicting": "output.non_conflicting",
                     "snapshot": "output.snapshot"}},
        {"from": "setup", "to": "merge2",
         "reason": "merge2 consumes setup.output.snapshot",
         "mapping": {"snapshot": "output.snapshot"}},
        {"from": "repair", "to": "merge2",
         "reason": "merge2 consumes repair.output resolutions",
         "mapping": {"branches": "output.branches"}},
        {"from": "merge2", "to": "test2", "reason": "test2 on repaired", "when": "test2",
         "mapping": {"applied": "output.applied"}},
        {"from": "merge2", "to": "fail",
         "reason": "fail on merge2 non-merged outcome", "when": "fail",
         "mapping": {"conflicts": "output.conflicts"}},
        {"from": "merge", "to": "fail",
         "reason": "fail on merge non-merged outcome", "when": "fail"},
        {"from": "test", "to": "final", "reason": "final summarizes test",
         "mapping": {"report": "output.summary"}},
        {"from": "test2", "to": "final2", "reason": "final2 summarizes test2",
         "mapping": {"report": "output.summary"}},
    ]
    return {"objective": "parallel implement with isolated merge",
            "execution_shape": "GRAPH",
            "graph": {"id": "coding-parallel-merge", "entry": "setup",
                      "nodes": nodes, "edges": edges,
                      "policies": {"max_concurrency": 8}}}


TEMPLATES = {"simple": simple_analyze_implement_verify,
             "parallel-analysis": parallel_repo_analysis,
             "repair": implement_review_repair,
             "complex": complex_coding,
             "parallel-implement-merge": parallel_implement_merge}


def build_template(name: str, ctx: Any) -> dict:
    """Build a template IR. Unknown names fall back to simple (never crash)."""
    files = list(getattr(ctx, "facts", []) or [])
    fn = TEMPLATES.get(name, simple_analyze_implement_verify)
    ir = fn(files)
    ir["objective"] = getattr(ctx, "objective", "")[:2048] or ir["objective"]
    return ir


def pick_template(objective: str) -> str:
    """Deterministic template routing by task shape keywords."""
    lowered = (objective or "").lower()
    if any(k in lowered for k in ("parallel implement", "independent changes",
                                  "two features", "simultaneous",
                                  "independent features")):
        return "parallel-implement-merge"
    if any(k in lowered for k in ("audit", "review the", "analyze the repo",
                                  "map the", "survey", "inventory")):
        return "parallel-analysis"
    if any(k in lowered for k in ("fix", "bug", "broken", "failing", "regression",
                                  "repair", "typo")):
        return "repair"
    if any(k in lowered for k in ("refactor", "migrate", "redesign", "rewrite",
                                  "implement", "feature", "epic")):
        return "complex"
    return "simple"
