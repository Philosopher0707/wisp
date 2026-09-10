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


TEMPLATES = {"simple": simple_analyze_implement_verify,
             "parallel-analysis": parallel_repo_analysis,
             "repair": implement_review_repair,
             "complex": complex_coding}


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
