"""GraphExecutor — owns execution authority; contains no model logic.

Responsibilities: validate, durable GraphRun, ready-queue scheduling,
bounded concurrency, joins, deterministic routing, verifier gates, partial
retries, per-transition persistence, resume, budgets, events, final result.

Model invocation happens ONLY through the injected ``runner``
``async (node, inputs) -> NodeResult``. FUNCTION/GATE/ROUTER-function
nodes run through the local function registry — never a model.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any, Awaitable, Callable

from wisp.graph import control as _control
from wisp.graph.artifacts import ArtifactStore, content_hash
from wisp.graph.scheduler import SchedulerState, is_finished, ready_nodes
from wisp.graph.store import GraphStore, new_run_id
from wisp.graph.types import (
    FAILED_NODE_STATUSES,
    Graph,
    GraphNode,
    JoinPolicy,
    NodeResult,
    NodeStatus,
    NodeType,
    RunStatus,
)
from wisp.graph.validator import validate_graph
from wisp.graph.verifier import normalize_verdict

NodeRunner = Callable[[GraphNode, dict[str, Any]], Awaitable[NodeResult]]
FunctionImpl = Callable[[dict[str, Any]], Any]
EmitFn = Callable[[dict[str, Any]], None]


def _noop_emit(event: dict[str, Any]) -> None:
    pass


class GraphExecutor:
    def __init__(self, runner: NodeRunner | None = None,
                 workspace: str = ".",
                 max_concurrency: int = 8,
                 limits: dict[str, int] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 store: GraphStore | None = None,
                 artifacts: ArtifactStore | None = None,
                 emit: EmitFn | None = None) -> None:
        self._runner = runner or _missing_runner
        self.workspace = workspace
        self.max_concurrency = max_concurrency
        self.limits = limits or {}
        self._sleep = sleep
        self._store = store
        self._artifacts = artifacts
        self._emit = emit or _noop_emit
        self._functions: dict[str, FunctionImpl] = {}
        self._cancelled: set[str] = set()
        self._approvals: dict[str, dict[str, bool]] = {}
        self._join_wait: dict[str, float] = {}

    # ── setup ──
    def register_function(self, name: str, fn: FunctionImpl) -> None:
        self._functions[name] = fn

    def _stores(self) -> tuple[GraphStore, ArtifactStore]:
        if self._store is None:
            self._store = GraphStore(workspace=self.workspace)
        if self._artifacts is None:
            self._artifacts = ArtifactStore(workspace=self.workspace, store=self._store)
        return self._store, self._artifacts

    # ── public API ──
    async def run(self, graph: Graph, inputs: dict[str, Any],
                  run_id: str = "") -> dict[str, Any]:
        errors = validate_graph(graph)
        if errors:
            raise ValueError("invalid graph: " + "; ".join(errors[:5]))
        store, _ = self._stores()
        rid = run_id or new_run_id()
        if graph.policies.workspace and os.path.abspath(graph.policies.workspace) != \
                os.path.abspath(self.workspace):
            raise PermissionError(
                f"graph workspace '{graph.policies.workspace}' != execution workspace "
                f"'{self.workspace}'")
        store.create_run(rid, graph.id, graph.version, graph.fingerprint(),
                         _graph_def(graph), inputs)
        store.set_run_status(rid, RunStatus.RUNNING.value)
        self._event(store, rid, "graph.created",
                    {"graph_id": graph.id, "version": graph.version})
        self._event(store, rid, "graph.started", {"inputs": _small(inputs)})
        state = SchedulerState(
            statuses={n.id: NodeStatus.PENDING for n in graph.nodes})
        return await self._drive(graph, rid, inputs, store, state, {}, {}, 0, time.time())

    async def resume(self, graph: Graph, run_id: str,
                     approvals: dict[str, bool] | None = None) -> dict[str, Any]:
        store, _ = self._stores()
        row = store.get_run(run_id)
        if row is None:
            raise KeyError(f"unknown run {run_id}")
        if row["graph_hash"] != graph.fingerprint():
            raise ValueError(f"run {run_id} started with graph hash {row['graph_hash']}; "
                             f"definition changed ({graph.fingerprint()}). Refusing.")
        contracts = {n.id: n.effective_contract() for n in graph.nodes}
        statuses: dict[str, NodeStatus] = {}
        results: dict[str, NodeResult] = {}
        attempts: dict[str, int] = {}
        taken: dict[str, str] = {}
        seq = 0
        saved = store.latest_checkpoint(run_id)
        if saved:
            snap = json.loads(saved["state"])
            seq = int(saved["seq"])
            taken = dict(snap.get("taken", {}))
        for row_n in store.node_runs(run_id):
            nid = row_n["node_id"]
            attempts[nid] = max(attempts.get(nid, 0), int(row_n["attempt"] or 0))
            if row_n["status"] == "success":
                statuses[nid] = NodeStatus.SUCCESS
                results[nid] = _result_from_dict(nid, json.loads(row_n["result"] or "{}"))
        for n in graph.nodes:
            if n.id not in statuses:
                statuses[n.id] = NodeStatus.PENDING
            elif statuses[n.id] == NodeStatus.RUNNING:
                # Interrupted mid-flight: safe work re-runs, side effects park.
                if contracts[n.id].idempotent:
                    statuses[n.id] = NodeStatus.PENDING
                else:
                    statuses[n.id] = NodeStatus.CANCELLED
        if approvals:
            self._approvals[run_id] = approvals
        self._cancelled.discard(run_id)
        store.set_run_status(run_id, RunStatus.RUNNING.value)
        self._event(store, run_id, "graph.resumed", {"checkpoint_seq": seq})
        state = SchedulerState(statuses=statuses, taken=taken)
        inputs = json.loads(row["inputs"] or "{}")
        return await self._drive(graph, run_id, inputs, store, state, results,
                                 attempts, seq, time.time())

    def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)
        if self._store is not None:
            try:
                row = self._store.get_run(run_id)
                if row is not None and row["status"] not in ("succeeded", "failed", "cancelled"):
                    self._store.set_run_status(run_id, RunStatus.CANCELLED.value)
                    self._store.append_event(run_id, "graph.cancelled", {})
            except Exception:
                pass

    # ── drive loop ──
    async def _drive(self, graph: Graph, rid: str, inputs: dict[str, Any],
                     store: GraphStore, sched: SchedulerState,
                     results: dict[str, NodeResult], attempts: dict[str, int],
                     seq: int, started: float) -> dict[str, Any]:
        nodes = {n.id: n for n in graph.nodes}
        budget = graph.policies
        cap = min(self.max_concurrency, budget.max_concurrency or self.max_concurrency)
        global_sem = asyncio.Semaphore(max(cap, 1))
        provider_sems: dict[str, asyncio.Semaphore] = {}
        in_flight: dict[asyncio.Task, str] = {}
        cycle_counts: dict[str, int] = {}

        def provider_sem(name: str) -> asyncio.Semaphore | None:
            key = f"provider:{name}"
            if key in self.limits:
                return provider_sems.setdefault(key, asyncio.Semaphore(self.limits[key]))
            return None

        async def launch(nid: str) -> None:
            node = nodes[nid]
            contract = node.effective_contract()
            node_inputs = self._derive_inputs(graph, nid, results, inputs)
            ihash = content_hash(node_inputs)[:12]
            attempt = attempts.get(nid, 0) + 1
            key = f"{rid}:{nid}:{ihash}:{attempt}"
            if contract.idempotent:
                dup = store.find_completed(key)
                if dup:
                    results[nid] = _result_from_dict(nid, json.loads(dup["result"] or "{}"))
                    sched.statuses[nid] = NodeStatus.SUCCESS
                    return
            attempts[nid] = attempt
            sched.statuses[nid] = NodeStatus.RUNNING
            self._event(store, rid, "graph.node_ready", {"node_id": nid})
            self._event(store, rid, "graph.node_started",
                        {"node_id": nid, "attempt": attempt})
            store.put_node_run(_nrid(rid, nid), rid, nid, "running", attempt,
                               ihash, {}, key, time.time(), 0.0)

            async def _run() -> NodeResult:
                async with global_sem:
                    psem = provider_sem(contract.model_policy.provider or "")
                    if psem is not None:
                        async with psem:
                            return await self._exec_node(node, node_inputs, attempt)
                    return await self._exec_node(node, node_inputs, attempt)

            task = asyncio.create_task(_guard(node, _run(), contract.timeout_s))
            in_flight[task] = nid

        async def settle_one() -> bool:
            done, _ = await asyncio.wait(list(in_flight), return_when=asyncio.FIRST_COMPLETED)
            progressed = False
            for task in done:
                nid = in_flight.pop(task)
                result: NodeResult = task.result()
                result.attempt = attempts.get(nid, 1)
                if await self._on_settled(graph, rid, store, sched, results, attempts,
                                          cycle_counts, nodes[nid], result):
                    progressed = True  # a retry was queued (node back to PENDING)
                else:
                    progressed = True  # a result was recorded
            return progressed

        async def drain() -> None:
            while in_flight:
                await settle_one()

        while True:
            if rid in self._cancelled:
                for t in in_flight:
                    t.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.gather(*in_flight, return_exceptions=True)
                in_flight.clear()
                store.set_run_status(rid, RunStatus.CANCELLED.value)
                self._event(store, rid, "graph.cancelled", {})
                return _final(graph, rid, RunStatus.CANCELLED, results, "cancelled", started)
            if _over_budget(budget, _tokens(results), _cost(results), time.time() - started):
                for t in in_flight:
                    t.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.gather(*in_flight, return_exceptions=True)
                in_flight.clear()
                store.set_run_status(rid, RunStatus.FAILED.value, "budget_exceeded")
                self._event(store, rid, "graph.failed", {"error": "budget_exceeded"})
                return _final(graph, rid, RunStatus.FAILED, results,
                              "budget_exceeded", started)

            progressed = False
            for nid in ready_nodes(graph, sched):
                node = nodes[nid]
                if node.type == NodeType.APPROVAL:
                    await drain()
                    decision = self._approvals.get(rid, {}).get(nid)
                    if decision is None:
                        store.set_run_status(rid, RunStatus.AWAITING_APPROVAL.value)
                        seq += 1
                        store.put_checkpoint(rid, seq, _snapshot(sched, results))
                        self._event(store, rid, "graph.paused",
                                    {"node_id": nid, "reason": "approval_required"})
                        return _final(graph, rid, RunStatus.AWAITING_APPROVAL,
                                      results, f"awaiting approval: {nid}", started)
                    sched.statuses[nid] = NodeStatus.SUCCESS if decision else NodeStatus.CANCELLED
                    results[nid] = NodeResult(nid, sched.statuses[nid],
                                              output={"approved": decision})
                    self._event(store, rid,
                                "graph.gate_opened" if decision else "graph.gate_closed",
                                {"node_id": nid})
                    progressed = True
                    continue
                if node.type == NodeType.JOIN:
                    if self._resolve_join(graph, rid, store, sched, results, node):
                        progressed = True
                    continue
                await launch(nid)
                progressed = True

            if in_flight:
                await settle_one()
                progressed = True
                seq += 1
                store.put_checkpoint(rid, seq, _snapshot(sched, results))
                self._event(store, rid, "graph.checkpoint", {"seq": seq})
                continue
            if is_finished(graph, sched):
                break
            if not progressed:
                # Dead end: conditional lanes that never fired are skipped.
                pending = [i for i in nodes
                           if sched.statuses.get(i, NodeStatus.PENDING) == NodeStatus.PENDING]
                if not pending:
                    break
                for nid in pending:
                    sched.statuses[nid] = NodeStatus.SKIPPED
                self._event(store, rid, "graph.join_wait",
                            {"skipped_untaken": sorted(pending)})
                if is_finished(graph, sched):
                    break
                # Newly skipped nodes may release joins; loop once more.
                progressed = True
                continue

        sinks = [i for i in nodes if not any(e.from_node == i for e in graph.edges)]
        failed = [s for s in sinks
                  if sched.statuses.get(s, NodeStatus.PENDING) in FAILED_NODE_STATUSES]
        status = RunStatus.FAILED if failed else RunStatus.SUCCEEDED
        store.set_run_status(rid, status.value, "; ".join(failed))
        self._event(store, rid,
                    "graph.completed" if status == RunStatus.SUCCEEDED else "graph.failed",
                    {"sinks": sinks, "failed": failed})
        return _final(graph, rid, status, results, "", started)

    # ── node execution (no model logic here; runner owns it) ──
    async def _exec_node(self, node: GraphNode, node_inputs: dict,
                         attempt: int) -> NodeResult:
        t0 = time.time()
        try:
            if node.type in (NodeType.FUNCTION, NodeType.GATE) or \
                    (node.type in (NodeType.ROUTER,) and node.function):
                fn = self._functions.get(node.function)
                if fn is None:
                    return NodeResult(node.id, NodeStatus.FAILURE,
                                      error_code="UNKNOWN_FUNCTION",
                                      message=f"no function '{node.function}'")
                out = fn(dict(node_inputs))
                if asyncio.iscoroutine(out):
                    out = await out
                out = out if isinstance(out, dict) else {"value": out}
                if node.type == NodeType.GATE and "allowed" not in out:
                    return NodeResult(node.id, NodeStatus.FAILURE,
                                      error_code="BAD_GATE",
                                      message="gate function must return {'allowed': bool}")
                return NodeResult(node.id, NodeStatus.SUCCESS, output=out,
                                  duration_s=time.time() - t0, attempt=attempt)
            # AGENT / VERIFIER / ROUTER-classifier: injected model runner.
            result = await self._runner(node, node_inputs)
            result.attempt = attempt
            result.duration_s = result.duration_s or (time.time() - t0)
            if node.type == NodeType.VERIFIER:
                verdict = normalize_verdict(result.output)
                result.output = {**result.output, "decision": verdict.decision,
                                 "reason_codes": verdict.reason_codes,
                                 "evidence": verdict.evidence}
            return result
        except asyncio.TimeoutError:
            return NodeResult(node.id, NodeStatus.TIMEOUT, error_code="TIMEOUT",
                              message="node exceeded timeout",
                              duration_s=time.time() - t0, attempt=attempt)
        except Exception as exc:  # runtime failure -> typed failure status
            return NodeResult(node.id, NodeStatus.FAILURE,
                              error_code=type(exc).__name__,
                              message=str(exc)[:500],
                              duration_s=time.time() - t0, attempt=attempt)

    async def _on_settled(self, graph: Graph, rid: str, store: GraphStore,
                          sched: SchedulerState, results: dict[str, NodeResult],
                          attempts: dict[str, int], cycle_counts: dict[str, int],
                          node: GraphNode, result: NodeResult) -> bool:
        """Record a settled node. Returns True when a retry was queued."""
        from wisp.graph.scheduler import condition_matches
        contract = node.effective_contract()
        if result.status in FAILED_NODE_STATUSES and result.attempt < contract.retry_policy.max_attempts:
            if result.status == NodeStatus.TIMEOUT and not contract.retry_policy.retry_on_timeout:
                pass
            elif contract.retry_policy.unsafe_side_effects or not contract.idempotent:
                self._event(store, rid, "graph.node_failed",
                            {"node_id": node.id, "error": result.message,
                             "retry": "refused-unsafe"})
            else:
                delay = min(contract.retry_policy.backoff_base_s * (2 ** (result.attempt - 1)),
                            contract.retry_policy.backoff_max_s)
                self._event(store, rid, "graph.node_retry",
                            {"node_id": node.id, "attempt": result.attempt + 1,
                             "backoff_s": delay})
                await self._sleep(delay)
                # Re-queue ONLY the failed unit; successes are preserved.
                sched.statuses[node.id] = NodeStatus.PENDING
                store.put_node_run(_nrid(rid, node.id), rid, node.id, "pending",
                                   result.attempt, "", {}, f"retry:{rid}:{node.id}",
                                   time.time(), 0.0)
                return True
        results[node.id] = result
        sched.statuses[node.id] = result.status
        label = _label_for(node, result.output)
        if label:
            sched.taken[node.id] = label
            self._event(store, rid, "graph.route_selected"
                        if node.type == NodeType.ROUTER else "graph.edge_evaluated",
                        {"node_id": node.id, "label": label})
        if node.type == NodeType.VERIFIER:
            self._event(store, rid, "graph.verification_accepted"
                        if result.output.get("decision") == "ALLOW"
                        else "graph.verification_rejected",
                        {"node_id": node.id, "verdict": result.output.get("decision")})
        self._event(store, rid, "graph.node_completed" if result.ok else "graph.node_failed",
                    {"node_id": node.id, "status": result.status.value,
                     "usage": result.usage()})
        store.put_node_run(_nrid(rid, node.id), rid, node.id, result.status.value,
                           result.attempt, "", _result_to_dict(result),
                           f"{rid}:{node.id}", time.time(), time.time())
        # Correction edge: a taken conditional label re-arms a settled cycle
        # member (short-term return path — only the failed unit is corrected).
        if label:
            for e in graph.edges:
                if e.from_node != node.id or not e.condition:
                    continue
                if not condition_matches(label, e.condition):
                    continue
                target = sched.statuses.get(e.to_node, NodeStatus.PENDING)
                if target in (NodeStatus.PENDING, NodeStatus.RUNNING):
                    continue
                if not _in_cycle(graph, e.to_node):
                    continue
                cycle_counts[e.to_node] = cycle_counts.get(e.to_node, 0) + 1
                if cycle_counts[e.to_node] > _cycle_bound(graph, e.to_node):
                    sched.statuses[e.to_node] = NodeStatus.FAILURE
                    results[e.to_node] = NodeResult(
                        e.to_node, NodeStatus.FAILURE, error_code="CYCLE_BOUND",
                        message="correction cycle exhausted")
                else:
                    # Replay the loop body: reset the correction target and
                    # everything downstream of it inside the declared cycle
                    # (e.g. generator AND verifier), clearing taken labels.
                    for m in _cycle_replay_set(graph, e.to_node):
                        sched.statuses[m] = NodeStatus.PENDING
                        results.pop(m, None)
                        sched.taken.pop(m, None)
                    self._event(store, rid, "graph.node_retry",
                                {"node_id": e.to_node, "via": "correction_edge"})
                return True
        return False

    def _resolve_join(self, graph: Graph, rid: str, store: GraphStore,
                      sched: SchedulerState, results: dict[str, NodeResult],
                      node: GraphNode) -> bool:
        """Evaluate a join decision point. Returns True when it settled."""
        preds = [e.from_node for e in graph.edges
                 if e.to_node == node.id and not e.condition]
        branch = {p: results[p] for p in preds if p in results}
        now = time.time()
        if node.join_timeout_s:
            start = self._join_wait.setdefault(f"{rid}:{node.id}", now)
            if now - start > node.join_timeout_s and branch:
                # TIMEOUT join: release with whatever settled (partial, explicit).
                sched.statuses[node.id] = NodeStatus.SUCCESS
                results[node.id] = NodeResult(node.id, NodeStatus.SUCCESS,
                                              output={"branches": sorted(branch),
                                                      "successes": sorted(
                                                          _control.collect_successes(branch)),
                                                      "failures": _control.collect_failures(branch),
                                                      "timed_out": True})
                self._event(store, rid, "graph.join_released",
                            {"node_id": node.id, "reason": "timeout: partial release"})
                self._join_wait.pop(f"{rid}:{node.id}", None)
                return True
        released, reason = _control.evaluate_join(node.join_policy, node.join_param, branch)
        settled_all = all(sched.statuses.get(p) in (
            NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.TIMEOUT,
            NodeStatus.CANCELLED, NodeStatus.SKIPPED) for p in preds)
        if released or (settled_all and node.join_policy == JoinPolicy.BEST_EFFORT):
            if not released:
                reason = "best_effort: partial"
            self._join_wait.pop(f"{rid}:{node.id}", None)
            sched.statuses[node.id] = NodeStatus.SUCCESS
            results[node.id] = NodeResult(node.id, NodeStatus.SUCCESS,
                                          output={"branches": sorted(branch),
                                                  "successes": sorted(
                                                      _control.collect_successes(branch)),
                                                  "failures": _control.collect_failures(branch)})
            self._event(store, rid, "graph.join_released",
                        {"node_id": node.id, "reason": reason})
            return True
        if settled_all:
            # Contained failure: the join fails, the graph records it explicitly.
            self._join_wait.pop(f"{rid}:{node.id}", None)
            sched.statuses[node.id] = NodeStatus.FAILURE
            results[node.id] = NodeResult(node.id, NodeStatus.FAILURE,
                                          error_code="JOIN_UNSATISFIED", message=reason)
            self._event(store, rid, "graph.node_failed",
                        {"node_id": node.id, "error": reason})
            return True
        self._event(store, rid, "graph.join_wait",
                    {"node_id": node.id, "reason": reason})
        return False

    # ── inputs: explicit mapping + artifact refs, never whole transcripts ──
    def _derive_inputs(self, graph: Graph, nid: str, results: dict[str, NodeResult],
                       run_inputs: dict) -> dict[str, Any]:
        _, arts = self._stores()
        data: dict[str, Any] = {}
        for e in graph.edges:
            if e.to_node != nid or e.from_node not in results:
                continue
            src = results[e.from_node]
            if not src.ok and not e.condition:
                continue
            for dst_path, src_path in (e.mapping or {}).items():
                val = _resolve_path(src.output, src_path)
                if val is not None:
                    _assign_path(data, dst_path, val)
            if not e.mapping:
                data[e.from_node] = {"output": src.output,
                                     "artifacts": list(src.artifact_ids)}
        if nid == graph.entrypoint:
            data = {**run_inputs, **data}
        return arts.resolve_inputs(data)

    def _event(self, store: GraphStore, rid: str, type: str, data: dict) -> None:
        try:
            store.append_event(rid, type, data)
        except Exception:
            pass
        try:
            self._emit({"type": type, "run_id": rid, "data": data, "at": time.time()})
        except Exception:
            pass


# ── helpers ───────────────────────────────────────────────────────────

async def _missing_runner(node: GraphNode, inputs: dict) -> NodeResult:
    return NodeResult(node.id, NodeStatus.FAILURE, error_code="NO_RUNNER",
                      message="GraphExecutor needs a runner for agent nodes")


async def _guard(node: GraphNode, coro, timeout_s: float) -> NodeResult:
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        return NodeResult(node.id, NodeStatus.TIMEOUT, error_code="TIMEOUT",
                          message=f"exceeded {timeout_s}s")


def _label_for(node: GraphNode, output: dict) -> str:
    if node.type == NodeType.ROUTER:
        label = _control.classify_label(output)
        return _control.resolve_route(node.routes, node.default_route, label)
    if node.type == NodeType.VERIFIER:
        d = str(output.get("decision", "reject")).lower()
        lane = str(output.get("lane", "") or "")
        primary = {"allow": "accept", "reject": "reject", "retry": "retry",
                   "escalate": "escalate"}.get(d, d)
        if primary == "reject" and lane:
            return f"reject.{lane}"
        return primary
    if node.type == NodeType.GATE:
        return "allow" if output.get("allowed") else "deny"
    return ""


def _in_cycle(graph: Graph, nid: str) -> bool:
    return any(n.cycle is not None and (nid == n.cycle.entry or nid in n.cycle.body
                                       or nid == n.cycle.exit_gate) for n in graph.nodes)


def _cycle_bound(graph: Graph, nid: str) -> int:
    for n in graph.nodes:
        if n.cycle is not None and (nid == n.cycle.entry or nid in n.cycle.body
                                    or nid == n.cycle.exit_gate):
            return n.cycle.max_iterations
    return 1


def _cycle_replay_set(graph: Graph, target: str) -> set[str]:
    """Correction target + downstream members of its declared cycle."""
    members: set[str] = set()
    for n in graph.nodes:
        if n.cycle is not None and (target == n.cycle.entry or target in n.cycle.body
                                    or target == n.cycle.exit_gate):
            members = {n.cycle.entry, *n.cycle.body, n.cycle.exit_gate}
            break
    if not members:
        return {target}
    adj: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.from_node in members and e.to_node in members:
            adj.setdefault(e.from_node, []).append(e.to_node)
    replay, stack = {target}, [target]
    while stack:
        for m in adj.get(stack.pop(), []):
            if m not in replay:
                replay.add(m)
                stack.append(m)
    return replay


def _snapshot(sched: SchedulerState, results: dict[str, NodeResult]) -> dict:
    return {"statuses": {k: v.value for k, v in sched.statuses.items()},
            "taken": dict(sched.taken),
            "completed": sorted(k for k, r in results.items() if r.ok)}


def _nrid(rid: str, nid: str) -> str:
    return f"{rid}:{nid}"


def _final(graph: Graph, rid: str, status: RunStatus, results: dict[str, NodeResult],
           error: str, started: float) -> dict[str, Any]:
    return {"run_id": rid, "graph_id": graph.id, "status": status.value, "error": error,
            "wall_s": round(time.time() - started, 2),
            "results_by_node": {k: _result_to_dict(v) for k, v in results.items()},
            "tokens": {"input": sum(r.input_tokens for r in results.values()),
                       "output": sum(r.output_tokens for r in results.values())},
            "cost_usd": round(sum(r.cost_usd for r in results.values()), 6)}


def _tokens(results: dict[str, NodeResult]) -> int:
    return sum(r.input_tokens + r.output_tokens for r in results.values())


def _cost(results: dict[str, NodeResult]) -> float:
    return sum(r.cost_usd for r in results.values())


def _over_budget(budget, tokens: int, cost: float, elapsed: float) -> bool:
    if budget.max_tokens is not None and tokens > budget.max_tokens:
        return True
    if budget.max_cost_usd is not None and cost > budget.max_cost_usd:
        return True
    if budget.max_runtime_s is not None and elapsed > budget.max_runtime_s:
        return True
    return False


def _resolve_path(output: dict, path: str) -> Any:
    cur: Any = output
    clean = path[7:] if path.startswith("output.") else path
    if clean == "output":
        return output
    for part in clean.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _assign_path(data: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = data
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _small(inputs: dict) -> dict:
    return {k: (v if not isinstance(v, str) or len(v) < 500 else v[:500] + "…")
            for k, v in inputs.items()}


def _graph_def(graph: Graph) -> dict:
    return {"id": graph.id, "version": graph.version,
            "nodes": [n.id for n in graph.nodes],
            "edges": [f"{e.from_node}->{e.to_node}:{e.condition}" for e in graph.edges]}


def _result_to_dict(r: NodeResult) -> dict:
    return {"status": r.status.value, "output": r.output, "artifacts": r.artifact_ids,
            "model": r.model, "provider": r.provider, "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens, "cost_usd": r.cost_usd,
            "duration_s": r.duration_s, "attempt": r.attempt,
            "error_code": r.error_code, "message": r.message}


def _result_from_dict(nid: str, d: dict) -> NodeResult:
    try:
        status = NodeStatus(d.get("status", "success"))
    except ValueError:
        status = NodeStatus.FAILURE
    return NodeResult(node_id=nid, status=status, output=d.get("output", {}),
                      artifact_ids=d.get("artifacts", []), model=d.get("model", ""),
                      provider=d.get("provider", ""), input_tokens=d.get("input_tokens", 0),
                      output_tokens=d.get("output_tokens", 0), cost_usd=d.get("cost_usd", 0.0),
                      duration_s=d.get("duration_s", 0.0), attempt=d.get("attempt", 1),
                      error_code=d.get("error_code", ""), message=d.get("message", ""))
