"""REPL ↔ Graph coding integration (Phase 11).

Thin integration layer only: TaskContext (bounded execution context),
deterministic strategy gate, RepoMap context provider, graph control API,
unified result contract, and progress rendering. Execution stays in
GraphExecutor; authority stays in ToolExecutor.authorize(); understanding
stays in RepoMap. Nothing here grants tools, approves actions, or audits.
"""

from __future__ import annotations

import asyncio
import logging
import re
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SINGLE_AGENT = "SINGLE_AGENT"
GRAPH = "GRAPH"

GRAPH_HINTS = ("refactor", "migrate", "implement", "audit", "rewrite",
               "redesign", "multi-module", "multi-file", "multiple files",
               "across", "module", "end-to-end", "regression", "vulnerability",
               "feature", "epic", "parallel", "login", "auth", "api",
               "database", "service", "integrat")
# Each hint is matched as a WORD (or a word stem), never as a substring: `author` is not `auth`, `capital` is not `api`, `rapid` is not `api`,
# `modular` is not `module`. The names are the public `GRAPH_HINTS`; a test keeps the two in step.
_HINT_PATTERNS = {name: re.compile(pattern) for name, pattern in (
    ("refactor", r"\brefactor\w*"), ("migrate", r"\bmigrat\w*"), ("implement", r"\bimplement\w*"), ("audit", r"\baudit\w*"),
    ("rewrite", r"\brewrit\w*"), ("redesign", r"\bredesign\w*"), ("multi-module", r"\bmulti-module\b"), ("multi-file", r"\bmulti-file\b"),
    ("multiple files", r"\bmultiple files\b"), ("across", r"\bacross\b"), ("module", r"\bmodules?\b"), ("end-to-end", r"\bend-to-end\b"),
    ("regression", r"\bregressions?\b"), ("vulnerability", r"\bvulnerabilit\w*"), ("feature", r"\bfeatures?\b"), ("epic", r"\bepics?\b"),
    ("parallel", r"\bparallel\b"), ("login", r"\blogins?\b"), ("auth", r"\bauth(?:entic\w*|oriz\w*)?\b"), ("api", r"\bapis?\b"),
    ("database", r"\bdatabases?\b"), ("service", r"\bservices?\b"), ("integrat", r"\bintegrat\w*"))}
GRAPH_THRESHOLD = 2
RECORD_PROMPT_CHARS = 4000
RECORD_SUMMARY_CHARS = 600
RECORD_FILES = 12
MAX_FACT_FILES = 20


_QUESTIONS = ("what ", "why ", "how ", "explain", "show me", "describe",
                "where ", "which ", "is there", "does ", "can you explain")
def _is_question(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.endswith("?") or lowered.startswith(_QUESTIONS)


@dataclass(frozen=True)
class TaskContext:
    """Bounded execution context. Never the full transcript."""

    objective: str = ""
    constraints: tuple[str, ...] = ()
    workspace: str = "."
    profile: str = ""
    facts: tuple[str, ...] = ()       # existing repo paths mentioned
    repo_refs: tuple[str, ...] = ()   # RepoMap-derived refs (files/symbols)
    capabilities: tuple[str, ...] = ()  # informational only — never authority
    mode: str = "auto"                # auto | single | graph (explicit request)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyDecision:
    strategy: str
    reasons: tuple[str, ...] = ()
    explicit: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    strategy: str = SINGLE_AGENT
    success: bool = False
    summary: str = ""
    changed_files: tuple[str, ...] = ()
    test_results: str = ""
    verification: str = ""
    warnings: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    execution_id: str = ""
    followup: str = ""


def task_context_from_prompt(prompt: str, workspace: str = ".",
                             config: Any = None, mode: str = "auto") -> TaskContext:
    """Build a bounded context: objective + existing-file facts + constraints."""
    objective = prompt if isinstance(prompt, str) else ""
    constraints = tuple(
        line.split(":", 1)[1].strip()[:500] for line in objective.splitlines()
        if line.strip().lower().startswith(("constraint:", "must not:")))
    facts = _existing_files(objective, workspace)
    profile = ""
    if config is not None:
        try:
            profile = config.get("profile", "") if isinstance(config, dict) \
                else getattr(config, "profile", "") or ""
        except Exception:
            profile = ""
    return TaskContext(objective=objective[:8192], constraints=constraints[:16],
                       workspace=workspace, profile=str(profile)[:64],
                       facts=facts, mode=mode if mode in ("auto", "single", "graph")
                       else "auto")


def _existing_files(text: str, workspace: str) -> tuple[str, ...]:
    """Mentioned paths that actually exist (bounded). Never globs the repo."""
    import re
    found: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_.\-./]+\.(?:py|js|ts|tsx|go|rs|java|md|yaml|yml|toml|json)", text):
        candidate = token.strip("./")
        if not candidate or len(candidate) > 256 or candidate in found:
            continue
        try:
            if os.path.isfile(os.path.join(workspace, candidate)):
                found.append(candidate)
        except (OSError, ValueError):
            continue
        if len(found) >= MAX_FACT_FILES:
            break
    return tuple(found)


def decide_strategy(ctx: TaskContext, policy: Any = None) -> StrategyDecision:
    """Deterministic host gate. Model may recommend (metadata); host decides."""
    if ctx.mode == "single":
        return StrategyDecision(SINGLE_AGENT, ("explicit single-agent request",), True)
    graph_allowed, why = _graph_allowed(policy)
    if ctx.mode == "graph":
        if graph_allowed:
            return StrategyDecision(GRAPH, ("explicit graph request",), True)
        return StrategyDecision(SINGLE_AGENT, (f"graph requested but {why}",), True)
    if _is_question(ctx.objective):
        return StrategyDecision(SINGLE_AGENT, ("interrogative: explain, don't execute",))
    lowered = ctx.objective.lower()
    # Explicit orchestration requests stay single-agent: no graph node role
    # may call spawn/fanout/subagent tools, so routing them to a graph
    # always dies at the role gate. The parent loop owns the full surface.
    if "subagent" in lowered or "background agent" in lowered:
        return StrategyDecision(
            SINGLE_AGENT, ("explicit subagent request: graph workers lack spawn tools",))
    # First file is free (single-file tasks stay single-agent); additional
    # files each add evidence of multi-scope work.
    score = max(0, min(len(ctx.facts), 3) - 1) * 2
    hits = sorted(name for name, rx in _HINT_PATTERNS.items() if rx.search(lowered))
    score += min(len(hits), 3)
    if len(ctx.objective) > 600:
        score += 1
    # Multi-part requests ("X and Y and Z") suggest separable work.
    if lowered.count(" and ") >= 2 or lowered.count(",") >= 2:
        score += 1
        hits = hits + ["multi-part"]
    if not graph_allowed:
        return StrategyDecision(SINGLE_AGENT, (f"graph unavailable: {why}",))
    if score >= GRAPH_THRESHOLD:
        return StrategyDecision(
            GRAPH, (f"complexity score {score} (files={len(ctx.facts)}, "
                    f"hints={','.join(hits) or 'none'})",))
    return StrategyDecision(SINGLE_AGENT, (f"complexity score {score} below {GRAPH_THRESHOLD}",))


def _graph_allowed(policy: Any) -> tuple[bool, str]:
    if policy is None:
        return True, ""
    try:
        max_nodes = policy.max_nodes
    except AttributeError:
        return True, ""
    if isinstance(max_nodes, int) and max_nodes < 3:
        return False, "policy max_nodes < 3 cannot express fanout"
    return True, ""


# ── RepoMap context provider (11C) ────────────────────────────────────

@dataclass(frozen=True)
class RepoContext:
    text: str = ""
    files: tuple[str, ...] = ()
    truncated: bool = False


def repo_context(workspace: str, query: str = "", files: tuple[str, ...] = (),
                 budget_tokens: int = 2000) -> RepoContext:
    """Bounded repo context via the existing RepoMap. Query/files are data."""
    from wisp.repo_map import RepoMap
    from pathlib import Path
    budget_tokens = max(256, min(int(budget_tokens), 8000))
    try:
        rm = RepoMap(Path(workspace))
        try:
            rm.build()
        except Exception:
            pass
        relevant: list[str] = []
        if query:
            try:
                relevant = list(rm.get_relevant_files(query, top_k=12) or [])
            except Exception:
                relevant = []
        seeds = [f for f in list(files)[:20] + relevant if isinstance(f, str)][:24]
        deps: list[str] = []
        for seed in seeds[:8]:
            try:
                deps.extend(rm.get_dependencies(seed) or [])
            except Exception:
                continue
        seen = sorted({s for s in seeds + deps[:16] if isinstance(s, str)})[:32]
        text = rm.format_for_llm(max_tokens=budget_tokens // 4 or 256)
    except Exception as exc:
        logger.warning("repo_context failed: %s", exc)
        return RepoContext(text="", files=(), truncated=True)
    if len(text) > budget_tokens * 4:
        text, truncated = text[:budget_tokens * 4], True
    else:
        truncated = False
    return RepoContext(text=text, files=tuple(seen), truncated=truncated)


# ── control API (11B): REPL drives graphs through Graph API only ──────

def run_coding_template(template: str, ctx: TaskContext,
                        emit: Any = None, max_concurrency: int = 8,
                        config: Any = None) -> dict:
    """Compile → optimize → execute a host-authored coding template. Blocking.

    *config* (a WispConfig) becomes the graph children's provider/identity —
    without it workers fall back to default-constructed config, which can
    point at an unconfigured endpoint.
    """
    from wisp.graph.coding_graphs import TEMPLATE_RUN_INPUTS, build_template
    from wisp.graph.planner import compile_optimized
    from wisp.graph.runner import default_executor
    ir = build_template(template, ctx)
    graph, diags, meta, _narrowed = compile_optimized(ir, None)
    ex = default_executor(ctx.workspace, max_concurrency, emit=emit, config=config)
    run_inputs = {"objective": ctx.objective, "files": list(ctx.facts)}
    run_inputs.update(TEMPLATE_RUN_INPUTS.get(template, {}))
    result = asyncio.run(ex.run(graph, run_inputs))
    result["template"], result["transports"] = template, meta.get(
        "artifact_transport", {}).get("transports", {})
    return result


def summarize_graph(template: str, run_id: str, final: dict) -> ExecutionResult:
    """Durable refs only — never the transcript."""
    results = final.get("results_by_node", {})
    ok = final.get("status") == "succeeded"
    changed: list[str] = []
    artifacts: list[str] = []
    for r in results.values():
        if not isinstance(r, dict):
            continue
        out = r.get("output", {})
        if isinstance(out, dict):
            for f in out.get("changed_files", []) or []:
                if isinstance(f, str) and f not in changed:
                    changed.append(f)
        artifacts.extend(a for a in r.get("artifacts", []) if isinstance(a, str))
    tail = results.get("final") or results.get("final2") or results.get("review") or {}
    summary = ""
    if isinstance(tail, dict) and isinstance(tail.get("output"), dict):
        summary = str(tail["output"].get("summary", tail["output"].get("text", "")))[:2000]
    if not summary:
        # Surface failed-node messages (e.g. conflict reports) instead of silence.
        problems = []
        for nid, r in results.items():
            if isinstance(r, dict) and r.get("status") not in ("success", "skipped"):
                msg = str(r.get("message", "") or "")[:300]
                if msg:
                    problems.append(f"{nid}: {msg}")
        summary = "; ".join(problems[:4])[:2000]
    verification = "unverified"
    for r in results.values():
        if isinstance(r, dict) and isinstance(r.get("output"), dict) \
                and "decision" in r["output"]:
            verification = str(r["output"]["decision"])
    # The graph's own status says every node ran, not that the work was accepted: a change the reviewer REJECTED or ESCALATED is not a success, and
    # a change nobody reviewed is not either (a run that changed nothing is not blamed for having no review).
    accepted = verification == "ALLOW" or (verification == "unverified" and not changed)
    return ExecutionResult(
        strategy=GRAPH, success=ok and accepted, summary=summary or f"graph {final.get('status')}",
        changed_files=tuple(changed[:64]), verification=verification,
        artifacts=tuple(list(dict.fromkeys(artifacts))[:64]),
        evidence=(f"graph:{run_id}",),
        execution_id=run_id,
        followup="" if ok else "graph run did not succeed; inspect trace")


def status_line(store: Any, run_id: str) -> str:
    row = store.get_run(run_id)
    if row is None:
        return f"unknown run {run_id}"
    return f"{row['run_id']} {row['graph_id']} {row['status']}"


def trace_text(store: Any, run_id: str) -> str:
    from wisp.graph.trace import render_ascii
    import json
    row = store.get_run(run_id)
    if row is None:
        return f"unknown run {run_id}"
    nodes = {}
    for n in store.node_runs(run_id):
        try:
            payload = json.loads(n["result"] or "{}")
        except (ValueError, TypeError):
            payload = {"status": "corrupt"}
        nodes[n["node_id"]] = {"status": n["status"], **(payload if isinstance(payload, dict) else {})}
    return render_ascii({"graph_id": row["graph_id"], "run_id": run_id,
                         "status": row["status"], "results_by_node": nodes,
                         "wall_s": 0, "tokens": {}, "cost_usd": 0})


# ── progress model (11I): structured events → terminal lines ──────────

_PROGRESS: dict[str, tuple[str, str]] = {
    "graph.node_started": ("…", "started"),
    "graph.node_completed": ("✓", "done"),
    "graph.node_failed": ("✗", "failed"),
    "graph.node_retry": ("↻", "retrying"),
    "graph.verification_accepted": ("✓", "verified"),
    "graph.verification_rejected": ("✗", "rejected"),
    "graph.join_released": ("✓", "joined"),
    "graph.route_selected": ("→", "routed"),
    "graph.paused": ("…", "awaiting approval"),
    "graph.completed": ("✓", "graph complete"),
    "graph.failed": ("✗", "graph failed"),
    "graph.cancelled": ("✗", "graph cancelled"),
}


def render_progress(event: dict) -> str | None:
    """User-facing line for a graph event. Model text never becomes status."""
    if not isinstance(event, dict):
        return None
    kind = _PROGRESS.get(str(event.get("type", "")))
    if kind is None:
        return None
    glyph, label = kind
    data = event.get("data", {})
    node = data.get("node_id", "") if isinstance(data, dict) else ""
    name = str(node).replace("_", " ").replace("-", " ")[:60]
    return f"{glyph} {name} {label}".strip() if name else f"{glyph} {label}"


# ── REPL entry (11A/11B): returns True when the prompt was handled ─────

def handle_prompt(runner: Any, prompt: str) -> bool:
    """Strategy-gated turn. False = fall through to the single-agent loop."""
    try:
        config = getattr(runner, "config", None)
        workspace = config.get("workspace", ".") if isinstance(config, dict) \
            else getattr(config, "workspace", ".") or "."
        out = getattr(runner, "out", None)
        ctx = task_context_from_prompt(prompt, workspace, config)
        if _loop_worthy(runner, ctx):
            from wisp.autonomous_repl import run_repl_converge
            run_repl_converge(runner, ctx.objective)
            return True
        decision = decide_strategy(ctx)
        if decision.strategy != GRAPH:
            return False
        return _run_graph_turn(runner, ctx, decision, out)
    except Exception as exc:
        logger.exception("coding gate failed; falling back to agent loop")
        try:
            runner.out.write(f"(graph path unavailable: {exc}; using agent loop)\n")
        except Exception:
            pass
        return False


def _loop_worthy(runner: Any, ctx: TaskContext) -> bool:
    """Route to the objective-level loop only when the host can verify the objective: the user stated a checkable end, or named a symbol to define.

    Never for a question, for an explicit single-agent or graph request, when the operator turned it off, when the runner cannot run a loop, or in a
    directory that is not a project (the derivation reads the workspace).
    """
    from wisp.autonomous_repl import assess, routing_enabled
    from wisp.core.workspace_walk import is_home_directory

    if ctx.mode != "auto" or _is_question(ctx.objective) or not routing_enabled():
        return False
    if getattr(runner, "loop", None) is None or getattr(runner, "runtime", None) is None:
        return False
    if not ctx.workspace or ctx.workspace == "/" or is_home_directory(ctx.workspace):
        return False
    return assess(ctx.objective, ctx.workspace).verifiable


def _run_graph_turn(runner: Any, ctx: TaskContext, decision: StrategyDecision,
                    out: Any) -> bool:
    from wisp.graph.coding_graphs import pick_template
    template = pick_template(ctx.objective)
    repo = repo_context(ctx.workspace, query=ctx.objective[:500],
                        files=ctx.facts, budget_tokens=2000)
    ctx = TaskContext(objective=ctx.objective, constraints=ctx.constraints,
                      workspace=ctx.workspace, profile=ctx.profile,
                      facts=ctx.facts, repo_refs=repo.files,
                      capabilities=ctx.capabilities, mode=ctx.mode,
                      metadata={**ctx.metadata,
                                "repo_truncated": repo.truncated})
    _emit_line(out, f"graph path ({template}): {decision.reasons[0] if decision.reasons else ''}")
    lines: list[str] = []

    def emit(event: dict) -> None:
        line = render_progress(event)
        if line is not None and (not lines or lines[-1] != line):
            lines.append(line)
            _emit_line(out, f"  {line}")

    prompt = ctx.objective
    try:
        cfg = getattr(runner, "config", None)
        from wisp.config import WispConfig
        live_config = cfg if isinstance(cfg, WispConfig) else None
        final = run_coding_template(template, ctx, emit=emit, config=live_config)
    except Exception as exc:
        _emit_line(out, f"graph run failed: {exc}")
        record_turn(runner, prompt, f"[graph run: {template}] ✗ graph run failed: {str(exc)[:RECORD_SUMMARY_CHARS]}")
        return True
    result = summarize_graph(template, final.get("run_id", ""), final)
    _emit_line(out, f"{'✓' if result.success else '✗'} {result.summary[:1500]}")
    if result.changed_files:
        _emit_line(out, f"  changed: {', '.join(result.changed_files[:16])}")
    _emit_line(out, f"  verification: {result.verification}{_verification_caveat(result)}")
    _emit_line(out, f"  trace: /graph trace {result.execution_id}")
    record_turn(runner, prompt, graph_note(template, result))
    return True


def _verification_caveat(result: ExecutionResult) -> str:
    if result.verification in ("REJECT", "ESCALATE"):
        return " (not verified: the change was not accepted)"
    if result.verification == "unverified" and result.changed_files:
        return " (changes were not reviewed)"
    return ""


def graph_note(template: str, result: ExecutionResult) -> str:
    """What the conversation keeps of a graph run: bounded, durable references, and the verdict in plain words."""
    lines = [f"[graph run: {template}] {'✓' if result.success else '✗'} {result.summary[:RECORD_SUMMARY_CHARS]}"]
    files = result.changed_files
    if files:
        more = f" (+{len(files) - RECORD_FILES} more)" if len(files) > RECORD_FILES else ""
        lines.append(f"changed: {', '.join(files[:RECORD_FILES])}{more}")
    lines.append(f"verification: {result.verification}{_verification_caveat(result)}")
    lines.append(f"trace: /graph trace {result.execution_id}")
    return "\n".join(lines)


def record_turn(runner: Any, prompt: str, note: str) -> None:
    """Put the graph turn into the session the agent loop reads, so the next prompt knows it happened. The session is a plain dict; it is saved by the
    REPL as for any other turn (this module never touches the database)."""
    session = getattr(runner, "session", None)
    if not isinstance(session, dict):
        return
    messages = session.setdefault("messages", [])
    messages.append({"role": "user", "content": prompt[:RECORD_PROMPT_CHARS]})
    messages.append({"role": "assistant", "content": note})


def _emit_line(out: Any, text: str) -> None:
    try:
        out.write(text + "\n")
        out.flush()
    except Exception:
        pass
