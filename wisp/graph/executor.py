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
from wisp.graph.security import scrub, scrub_text as redact
from wisp.graph.artifacts import ArtifactStore, content_hash
from wisp.graph.scheduler import (
    SchedulerState,
    blocked_by_failure,
    is_finished,
    ready_nodes,
)
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
from wisp.graph.validator import _NODE_ID_RX as _NODE_RX, validate_graph
from wisp.graph.verifier import normalize_verdict

NodeRunner = Callable[[GraphNode, dict[str, Any]], Awaitable[NodeResult]]
FunctionImpl = Callable[[dict[str, Any]], Any]
EmitFn = Callable[[dict[str, Any]], None]

# Result/output volume caps (DB + event bloat guard).
MAX_OUTPUT_BYTES = 262_144
MAX_INPUT_BYTES = 1_048_576


def _valid_run_id(run_id: str) -> bool:
    return isinstance(run_id, str) and 0 < len(run_id) <= 128 and \
        all(ch.isalnum() or ch in "-_." for ch in run_id)


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
                 emit: EmitFn | None = None,
                 auditor: Any | None = None) -> None:
        self._runner = runner or _missing_runner
        self.workspace = workspace
        self.max_concurrency = max_concurrency
        self.limits = limits or {}
        self._sleep = sleep
        self._store = store
        self._artifacts = artifacts
        self._emit = emit or _noop_emit
        self._auditor = auditor
        self._functions: dict[str, FunctionImpl] = {}
        self._cancelled: set[str] = set()
        self._approvals: dict[str, dict[str, bool]] = {}
        self._join_wait: dict[str, float] = {}
        self._join_wait_reason: dict[str, str] = {}

    # ── setup ──
    def register_function(self, name: str, fn: FunctionImpl) -> None:
        self._functions[name] = fn

    def _stores(self) -> tuple[GraphStore, ArtifactStore]:
        if self._store is None:
            self._store = GraphStore(workspace=self.workspace)
        if self._artifacts is None:
            self._artifacts = ArtifactStore(workspace=self.workspace, store=self._store)
        return self._store, self._artifacts

    def _audit(self, event: str, graph: Graph | None = None, run_id: str = "",
               node_id: str = "", attempt: int = 0, allowed: bool = False,
               reason: str = "", evidence: Any = None) -> str:
        """Best-effort immutable audit; returns entry hash or '' (never raises)."""
        try:
            if self._auditor is None:
                from wisp.graph.audit import GraphSecurityAuditor
                self._auditor = GraphSecurityAuditor(workspace=self.workspace)
            return self._auditor.emit(
                event, graph_id=graph.id if graph else "",
                graph_hash=graph.fingerprint() if graph else "",
                run_id=run_id, node_id=node_id, attempt=attempt,
                allowed=allowed, reason=reason, evidence=evidence)
        except Exception:
            return ""

    # ── public API ──
    async def run(self, graph: Graph, inputs: dict[str, Any],
                  run_id: str = "") -> dict[str, Any]:
        errors = validate_graph(graph)
        if errors:
            reason = "; ".join(errors[:5])
            gov = any(k in reason for k in ("policy", "not in graph", "forbidden",
                                            "allowed_"))
            self._audit("graph.policy_rejected" if gov else "graph.validation_rejected",
                        graph, "", allowed=False, reason=reason)
            raise ValueError("invalid graph: " + reason)
        store, _ = self._stores()
        rid = run_id or new_run_id()
        if not _valid_run_id(rid):
            raise ValueError(f"invalid run id {rid!r}")
        if len(json.dumps(inputs, default=str)) > MAX_INPUT_BYTES:
            raise ValueError("graph inputs too large")
        if graph.policies.workspace and os.path.realpath(graph.policies.workspace) != \
                os.path.realpath(self.workspace):
            raise PermissionError(
                f"graph workspace '{graph.policies.workspace}' != execution workspace "
                f"'{self.workspace}'")
        try:
            store.create_run(rid, graph.id, graph.version, graph.fingerprint(),
                             _graph_def(graph), inputs, self.workspace)
        except Exception as exc:
            raise ValueError(f"cannot create run {rid!r}: {exc}") from exc
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
            self._audit("graph.fingerprint_mismatch", graph, run_id, allowed=False,
                        reason="stored graph hash != current definition; resume refused",
                        evidence={"stored": str(row["graph_hash"])[:64],
                                  "current": graph.fingerprint()})
            raise ValueError(f"run {run_id} started with graph hash {row['graph_hash']}; "
                             f"definition changed ({graph.fingerprint()}). Refusing.")
        try:
            saved_def = json.loads(row["graph_def"] or "{}")
        except (ValueError, TypeError):
            saved_def = {}
        saved_ws = saved_def.get("workspace", "")
        if saved_ws and os.path.realpath(saved_ws) != os.path.realpath(self.workspace):
            self._audit("graph.state_tamper", graph, run_id, allowed=False,
                        reason="resume workspace != run workspace",
                        evidence={"saved": str(saved_ws)[:256]})
            raise PermissionError(f"run {run_id} belongs to workspace {saved_ws!r}; "
                                  f"refusing resume from {self.workspace!r}")
        contracts = {n.id: n.effective_contract() for n in graph.nodes}
        statuses: dict[str, NodeStatus] = {}
        results: dict[str, NodeResult] = {}
        attempts: dict[str, int] = {}
        taken: dict[str, str] = {}
        seq = 0
        saved = store.latest_checkpoint(run_id)
        if saved:
            try:
                snap = json.loads(saved["state"])
            except (ValueError, TypeError):
                self._audit("graph.state_tamper", graph, run_id, allowed=False,
                            reason="checkpoint JSON corrupt; resume refused")
                raise ValueError(f"run {run_id}: checkpoint corrupt; refusing resume")
            if not isinstance(snap, dict):
                self._audit("graph.state_tamper", graph, run_id, allowed=False,
                            reason="checkpoint shape invalid; resume refused")
                raise ValueError(f"run {run_id}: checkpoint corrupt; refusing resume")
            seq = int(snap.get("seq", saved["seq"]) or 0)
            raw_taken = snap.get("taken", {})
            if not isinstance(raw_taken, dict):
                raw_taken = {}
            taken = {str(k)[:128]: str(v)[:256] for k, v in raw_taken.items()
                     if isinstance(k, str) and isinstance(v, str)}
        for row_n in store.node_runs(run_id):
            nid = row_n["node_id"]
            if nid not in contracts:
                continue  # definition drift beyond hash pin: ignore foreign rows
            try:
                attempt = max(0, int(row_n["attempt"] or 0))
            except (TypeError, ValueError):
                attempt = 0
            attempts[nid] = max(attempts.get(nid, 0), attempt)
            if row_n["status"] == "success":
                try:
                    results[nid] = _result_from_dict(nid, json.loads(row_n["result"] or "{}"))
                except (ValueError, TypeError):
                    continue  # corrupt row: re-run instead of trusting it
                statuses[nid] = NodeStatus.SUCCESS
            elif row_n["status"] in ("running", "pending"):
                # Interrupted mid-flight: safe work re-runs, side effects park.
                # Recorded terminal states (failure/timeout/cancelled/skipped)
                # are NOT trusted — the node re-runs under current authority.
                if contracts[nid].idempotent:
                    statuses[nid] = NodeStatus.PENDING
                else:
                    statuses[nid] = NodeStatus.CANCELLED
        for n in graph.nodes:
            if n.id not in statuses:
                statuses[n.id] = NodeStatus.PENDING
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
            if not _finite_timeout(contract.timeout_s):
                sched.statuses[nid] = NodeStatus.FAILURE
                results[nid] = NodeResult(nid, NodeStatus.FAILURE,
                                          error_code="BAD_TIMEOUT",
                                          message="non-finite timeout refused")
                return
            node_inputs = self._derive_inputs(graph, nid, results, inputs, rid)
            ihash = content_hash(node_inputs)[:12]
            attempt = attempts.get(nid, 0) + 1
            key = f"{rid}:{nid}:{ihash}:{attempt}"
            if contract.idempotent:
                try:
                    dup = store.find_completed(key)
                except Exception:
                    dup = None
                if dup:
                    try:
                        results[nid] = _result_from_dict(nid, json.loads(dup["result"] or "{}"))
                    except (ValueError, TypeError):
                        pass
                    else:
                        sched.statuses[nid] = NodeStatus.SUCCESS
                        return
            attempts[nid] = attempt
            sched.statuses[nid] = NodeStatus.RUNNING
            self._event(store, rid, "graph.node_ready", {"node_id": nid})
            self._event(store, rid, "graph.node_started",
                        {"node_id": nid, "attempt": attempt})
            store.put_node_run(_attempt_nrid(rid, nid, attempt), rid, nid, "running",
                               attempt, ihash, {}, key, time.time(), 0.0)

            async def _run() -> NodeResult:
                async with global_sem:
                    psem = provider_sem(contract.model_policy.provider or "")
                    if psem is not None:
                        async with psem:
                            return await self._exec_node(node, node_inputs, attempt)
                    return await self._exec_node(node, node_inputs, attempt)

            task = asyncio.create_task(_guard(node, _run(), contract.timeout_s))
            in_flight[task] = (nid, attempt)

        async def settle_one() -> bool:
            # Slice the wait so cancellation is responsive: a stuck node
            # must not delay cancel until its (long) timeout expires.
            done: set = set()
            while not done:
                done, _ = await asyncio.wait(list(in_flight),
                                             return_when=asyncio.FIRST_COMPLETED,
                                             timeout=0.5)
                if not done and rid in self._cancelled:
                    for t in in_flight:
                        t.cancel()
                    with contextlib.suppress(Exception):
                        await asyncio.gather(*in_flight, return_exceptions=True)
                    done = {t for t in in_flight if t.done()}
                    for t in list(in_flight):
                        if not t.done():
                            done.add(t)  # processed as cancelled below
                    break
            progressed = False
            for task in done:
                if task not in in_flight:
                    continue
                nid, generation = in_flight.pop(task)
                try:
                    result: NodeResult = task.result()
                except asyncio.CancelledError:
                    result = NodeResult(nid, NodeStatus.CANCELLED,
                                        error_code="CANCELLED",
                                        message="node task cancelled")
                except Exception as exc:  # defensive: _guard already converts
                    result = NodeResult(nid, NodeStatus.FAILURE,
                                        error_code=type(exc).__name__,
                                        message=str(exc)[:200])
                if generation != attempts.get(nid, 0):
                    # Stale worker: a newer attempt already launched (retry /
                    # correction / resume). The stale result is dropped and the
                    # attempt row is marked superseded — never applied.
                    audit_hash = self._audit(
                        "graph.stale_rejected", graph, rid, nid, generation,
                        allowed=False, reason="superseded attempt completed late",
                        evidence={"current_attempt": attempts.get(nid, 0)})
                    self._event(store, rid, "graph.node_retry",
                                {"node_id": nid, "stale_attempt": generation,
                                 "current_attempt": attempts.get(nid, 0),
                                 "dropped": True, "audit_hash": audit_hash})
                    store.put_node_run(_attempt_nrid(rid, nid, generation), rid, nid,
                                       "cancelled", generation, "", {},
                                       f"stale:{rid}:{nid}:{generation}",
                                       time.time(), time.time())
                    progressed = True
                    continue
                result.attempt = generation
                _cap_result(result)
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
                audit_hash = self._audit("graph.cancel", graph, rid, allowed=True,
                                         reason="operator cancel")
                self._event(store, rid, "graph.cancelled", {"audit_hash": audit_hash})
                return _final(graph, rid, RunStatus.CANCELLED, results, "cancelled", started)
            if _over_budget(budget, _tokens(results), _cost(results), time.time() - started):
                for t in in_flight:
                    t.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.gather(*in_flight, return_exceptions=True)
                in_flight.clear()
                store.set_run_status(rid, RunStatus.FAILED.value, "budget_exceeded")
                audit_hash = self._audit("graph.budget_exceeded", graph, rid,
                                         allowed=False, reason="graph budget exceeded",
                                         evidence={"tokens": _tokens(results),
                                                   "cost_usd": _cost(results)})
                self._event(store, rid, "graph.failed", {"error": "budget_exceeded",
                                                         "audit_hash": audit_hash})
                return _final(graph, rid, RunStatus.FAILED, results,
                              "budget_exceeded", started)

            progressed = False
            for nid in ready_nodes(graph, sched):
                node = nodes[nid]
                block = blocked_by_failure(graph, sched, nid)
                if block:
                    # Failure containment: skip, never launch. A lane that
                    # never produced (skipped predecessor) is UPSTREAM_SKIPPED
                    # and stays benign if its preds are benign; a genuinely
                    # failed predecessor is UPSTREAM_FAILED and fails honesty.
                    skipped_pred = any(
                        sched.statuses.get(e.from_node) == NodeStatus.SKIPPED
                        for e in graph.edges if e.to_node == nid and not e.condition)
                    failed_pred = any(
                        sched.statuses.get(e.from_node) in FAILED_NODE_STATUSES
                        for e in graph.edges if e.to_node == nid and not e.condition)
                    code = "UPSTREAM_SKIPPED" if skipped_pred and not failed_pred \
                        else "UPSTREAM_FAILED"
                    sched.statuses[nid] = NodeStatus.SKIPPED
                    results[nid] = NodeResult(nid, NodeStatus.SKIPPED,
                                              error_code=code,
                                              message="an unconditional dependency failed"
                                              if code == "UPSTREAM_FAILED" else
                                              "waited on a lane that never produced")
                    store.put_node_run(_attempt_nrid(rid, nid, 0), rid, nid,
                                       "skipped", 0, "", _result_to_dict(results[nid]),
                                       f"{rid}:{nid}", time.time(), time.time())
                    self._event(store, rid, "graph.node_failed",
                                {"node_id": nid, "error": "upstream failed; skipped"})
                    progressed = True
                    continue
                if node.type == NodeType.APPROVAL:
                    await drain()
                    # Fail closed: only an explicit True grants. Truthy
                    # non-bools ("false", 1, [...]) must never approve.
                    decision = self._approvals.get(rid, {}).pop(nid, None)
                    granted = decision is True
                    if decision is None:
                        store.set_run_status(rid, RunStatus.AWAITING_APPROVAL.value)
                        seq += 1
                        store.put_checkpoint(rid, seq, _snapshot(sched, results))
                        audit_hash = self._audit("graph.approval_requested", graph, rid,
                                                 nid, allowed=False,
                                                 reason="human decision required")
                        self._event(store, rid, "graph.paused",
                                    {"node_id": nid, "reason": "approval_required",
                                     "audit_hash": audit_hash})
                        return _final(graph, rid, RunStatus.AWAITING_APPROVAL,
                                      results, f"awaiting approval: {nid}", started)
                    audit_hash = self._audit(
                        "graph.approval_granted" if granted else "graph.approval_denied",
                        graph, rid, nid, allowed=granted,
                        reason="resume-channel decision")
                    sched.statuses[nid] = NodeStatus.SUCCESS if granted else NodeStatus.CANCELLED
                    results[nid] = NodeResult(nid, sched.statuses[nid],
                                              output={"approved": granted,
                                                      "audit_hash": audit_hash})
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
                # Dead-end fixpoint: skip provably-dead pending nodes (all
                # preds terminal yet still not runnable, i.e. unmatched
                # conditional lanes), then re-evaluate — newly unblocked
                # unconditional nodes must launch, not be skipped with them.
                if not self._skip_dead(graph, sched, results, store, rid):
                    break
                self._event(store, rid, "graph.join_wait",
                            {"skipped_untaken": True})
                progressed = True
                continue

        sinks = [i for i in nodes if not any(e.from_node == i for e in graph.edges)]
        # Verdict honesty: success requires every sink to have succeeded, or
        # to be benignly skipped. A skip is benign when the lane never
        # activated: LANE_UNTAKEN, or UPSTREAM_SKIPPED behind only benign
        # preds (recursive — a terminal that correctly never ran must not
        # fail the run). Failed sinks -> FAILED; denied/other-skipped -> CANCELLED.
        failed = [s for s in sinks
                  if sched.statuses.get(s, NodeStatus.PENDING) in FAILED_NODE_STATUSES]
        benign = _benign_set(graph, sched, results)
        incomplete = [s for s in sinks
                      if sched.statuses.get(s, NodeStatus.PENDING)
                      in (NodeStatus.CANCELLED, NodeStatus.SKIPPED, NodeStatus.PENDING,
                          NodeStatus.RUNNING)
                      and s not in benign]
        if failed:
            status = RunStatus.FAILED
        elif incomplete:
            status = RunStatus.CANCELLED
        else:
            status = RunStatus.SUCCEEDED
        store.set_run_status(rid, status.value, "; ".join(failed))
        audit_hash = self._audit("graph.run_terminated", graph, rid,
                                 allowed=(status == RunStatus.SUCCEEDED),
                                 reason=status.value,
                                 evidence={"failed": failed, "incomplete": incomplete})
        self._event(store, rid,
                    "graph.completed" if status == RunStatus.SUCCEEDED else "graph.failed",
                    {"sinks": sinks, "failed": failed, "audit_hash": audit_hash})
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
                res = NodeResult(node.id, NodeStatus.SUCCESS, output=out,
                                 duration_s=time.time() - t0, attempt=attempt)
                _cap_result(res)
                return res
            # AGENT / VERIFIER / ROUTER-classifier: injected model runner.
            result = await self._runner(node, node_inputs)
            if not isinstance(result, NodeResult):
                return NodeResult(node.id, NodeStatus.FAILURE,
                                  error_code="BAD_RUNNER",
                                  message="runner must return NodeResult")
            result.node_id = node.id  # runner must not spoof another node's id
            result.attempt = attempt
            result.duration_s = result.duration_s or (time.time() - t0)
            if node.type == NodeType.VERIFIER:
                if not isinstance(result.output, dict):
                    result.output = {}
                verdict = normalize_verdict(result.output)
                result.output = {**result.output, "decision": verdict.decision,
                                 "reason_codes": verdict.reason_codes,
                                 "evidence": verdict.evidence}
            _cap_result(result)
            return result
        except asyncio.TimeoutError:
            return NodeResult(node.id, NodeStatus.TIMEOUT, error_code="TIMEOUT",
                              message="node exceeded timeout",
                              duration_s=time.time() - t0, attempt=attempt)
        except Exception as exc:  # runtime failure -> typed failure status
            return NodeResult(node.id, NodeStatus.FAILURE,
                              error_code=redact(type(exc).__name__)[:64],
                              message=redact(str(exc))[:500],
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
                self._audit("graph.retry_refused", graph, rid, node.id, result.attempt,
                            allowed=False, reason="timeout retry not opted in")
            elif contract.retry_policy.unsafe_side_effects or not contract.idempotent:
                audit_hash = self._audit("graph.retry_refused", graph, rid, node.id,
                                         result.attempt, allowed=False,
                                         reason="unsafe or non-idempotent side effects")
                self._event(store, rid, "graph.node_failed",
                            {"node_id": node.id, "error": result.message,
                             "retry": "refused-unsafe", "audit_hash": audit_hash})
            else:
                shift = min(max(result.attempt - 1, 0), 10)
                delay = min(contract.retry_policy.backoff_base_s * (2 ** shift),
                            contract.retry_policy.backoff_max_s)
                audit_hash = self._audit("graph.retry_allowed", graph, rid, node.id,
                                         result.attempt, allowed=True,
                                         reason=f"attempt {result.attempt + 1}",
                                         evidence={"backoff_s": delay,
                                                   "error": result.error_code})
                self._event(store, rid, "graph.node_retry",
                            {"node_id": node.id, "attempt": result.attempt + 1,
                             "backoff_s": delay, "audit_hash": audit_hash})
                await self._sleep(delay)
                # Re-queue ONLY the failed unit; successes are preserved.
                sched.statuses[node.id] = NodeStatus.PENDING
                store.put_node_run(_attempt_nrid(rid, node.id, result.attempt),
                                   rid, node.id, "pending",
                                   result.attempt, "", {}, f"retry:{rid}:{node.id}",
                                   time.time(), 0.0)
                return True
        results[node.id] = result
        sched.statuses[node.id] = result.status
        label = _label_for(node, result.output)
        if label:
            sched.taken[node.id] = label
            if node.type == NodeType.GATE:
                pass  # audited gate emit below (single event, with hash)
            elif node.type == NodeType.ROUTER:
                raw = _control.classify_label(result.output if isinstance(result.output, dict)
                                              else {})
                unknown = raw not in node.routes
                audit_hash = self._audit(
                    "graph.route_unknown" if unknown else "graph.route_selected",
                    graph, rid, node.id, result.attempt, allowed=not unknown,
                    reason=f"label {raw!r} -> {label!r}",
                    evidence={"label": raw, "target": label})
                self._event(store, rid, "graph.route_selected",
                            {"node_id": node.id, "label": label,
                             "unknown": unknown, "audit_hash": audit_hash})
            else:
                self._event(store, rid, "graph.edge_evaluated",
                            {"node_id": node.id, "label": label})
        if node.type == NodeType.VERIFIER:
            verdict = result.output.get("decision", "REJECT") \
                if isinstance(result.output, dict) else "REJECT"
            audit_hash = self._audit(f"graph.verification_{verdict.lower()}", graph,
                                     rid, node.id, result.attempt,
                                     allowed=(verdict == "ALLOW"),
                                     reason=f"verdict {verdict}",
                                     evidence={"reason_codes": result.output.get("reason_codes", [])
                                               if isinstance(result.output, dict) else []})
            self._event(store, rid, "graph.verification_accepted"
                        if verdict == "ALLOW" else "graph.verification_rejected",
                        {"node_id": node.id, "verdict": verdict,
                         "audit_hash": audit_hash})
        if node.type == NodeType.GATE and label in ("allow", "deny"):
            audit_hash = self._audit("graph.gate_decision", graph, rid, node.id,
                                     result.attempt, allowed=(label == "allow"),
                                     reason=f"gate {label}",
                                     evidence={"function": node.function})
            self._event(store, rid, "graph.edge_evaluated",
                        {"node_id": node.id, "label": label,
                         "audit_hash": audit_hash})
        self._event(store, rid, "graph.node_completed" if result.ok else "graph.node_failed",
                    {"node_id": node.id, "status": result.status.value,
                     "usage": result.usage()})
        store.put_node_run(_attempt_nrid(rid, node.id, result.attempt),
                           rid, node.id, result.status.value,
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
                    audit_hash = self._audit(
                        "graph.retry_allowed", graph, rid, e.to_node, 0,
                        allowed=True, reason="correction edge",
                        evidence={"from": node.id, "label": label})
                    self._event(store, rid, "graph.node_retry",
                                {"node_id": e.to_node, "via": "correction_edge",
                                 "audit_hash": audit_hash})
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
        # Throttle: join_wait is emitted only when the reason changes —
        # unthrottled it spams one SQLite row per drive iteration (disk DoS).
        last = self._join_wait_reason.get(f"{rid}:{node.id}")
        if last != reason:
            self._join_wait_reason[f"{rid}:{node.id}"] = reason
            self._event(store, rid, "graph.join_wait",
                        {"node_id": node.id, "reason": reason})
        return False

    # ── inputs: explicit mapping + artifact refs, never whole transcripts ──
    def _derive_inputs(self, graph: Graph, nid: str, results: dict[str, NodeResult],
                       run_inputs: dict, rid: str) -> dict[str, Any]:
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
        if len(json.dumps(data, default=str)) > MAX_INPUT_BYTES:
            raise ValueError(f"node {nid}: derived inputs exceed size cap")
        return arts.resolve_inputs(data, rid)

    def _skip_dead(self, graph: Graph, sched: SchedulerState,
                   results: dict[str, NodeResult], store: GraphStore,
                   rid: str) -> bool:
        """Skip one fixpoint wave of provably-dead pending nodes.

        A pending node is dead when every predecessor is terminal yet the
        node still is not runnable (unmatched conditional lane). Unconditional
        nodes with settled preds are runnable, never dead. Returns True when
        anything was skipped (caller re-evaluates readiness).
        """
        terminal = (NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.TIMEOUT,
                    NodeStatus.CANCELLED, NodeStatus.SKIPPED)
        preds_of: dict[str, list] = {n.id: [] for n in graph.nodes}
        for e in graph.edges:
            if e.to_node in preds_of:
                preds_of[e.to_node].append(e)
        runnable = set(ready_nodes(graph, sched))
        skipped_any = False
        for nid in sorted(preds_of):
            if sched.statuses.get(nid, NodeStatus.PENDING) != NodeStatus.PENDING:
                continue
            if nid == graph.entrypoint or nid in runnable:
                continue
            preds = preds_of[nid]
            if not preds:
                # No predecessors and not the entrypoint: unrunnable in this
                # scheduler (validator flags it as unreachable). Skip so the
                # drive loop always terminates.
                sched.statuses[nid] = NodeStatus.SKIPPED
                results[nid] = NodeResult(nid, NodeStatus.SKIPPED,
                                          error_code="UPSTREAM_SKIPPED",
                                          message="no incoming edges and not entrypoint")
                skipped_any = True
                continue
            if any(sched.statuses.get(e.from_node, NodeStatus.PENDING) not in terminal
                   for e in preds):
                continue  # a predecessor may still settle
            sched.statuses[nid] = NodeStatus.SKIPPED
            conditional = any(e.condition for e in preds)
            results[nid] = NodeResult(nid, NodeStatus.SKIPPED,
                                      error_code="LANE_UNTAKEN" if conditional
                                      else "UPSTREAM_SKIPPED",
                                      message="no incoming condition fired" if conditional
                                      else "unconditional predecessors settled terminal")
            skipped_any = True
        return skipped_any

    def _event(self, store: GraphStore, rid: str, type: str, data: dict) -> None:
        # Redact at the boundary: secrets never reach SQLite or listeners.
        try:
            data = scrub(data)
        except Exception:
            data = {"redaction_failed": True}
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
    if not isinstance(output, dict):
        return ""
    if node.type == NodeType.ROUTER:
        label = _control.classify_label(output)
        return _control.resolve_route(node.routes, node.default_route, label)
    if node.type == NodeType.VERIFIER:
        # Unknown decisions fail closed in normalize_verdict; the label only
        # selects graph edges, never authority — still, cap its shape.
        d = str(output.get("decision", "reject"))[:64].lower()
        lane = str(output.get("lane", "") or "")[:64]
        primary = {"allow": "accept", "reject": "reject", "retry": "retry",
                   "escalate": "escalate"}.get(d, "reject")
        if primary == "reject" and lane and _NODE_RX.fullmatch(lane):
            return f"reject.{lane}"
        return primary
    if node.type == NodeType.GATE:
        return "allow" if output.get("allowed") is True else "deny"
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


def _benign_set(graph: Graph, sched: SchedulerState,
                results: dict[str, NodeResult]) -> set[str]:
    """Nodes whose non-execution is correct: SUCCESS, LANE_UNTAKEN, or
    UPSTREAM_SKIPPED behind only benign unconditional preds (fixpoint).
    Anything else skipped (e.g. denied gates) stays incomplete."""
    benign = {nid for nid, st in sched.statuses.items() if st == NodeStatus.SUCCESS}
    preds_of: dict[str, list] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        if e.to_node in preds_of and not e.condition:
            preds_of[e.to_node].append(e.from_node)
    changed = True
    while changed:
        changed = False
        for nid, st in sched.statuses.items():
            if nid in benign or st != NodeStatus.SKIPPED:
                continue
            code = results.get(nid, NodeResult(nid)).error_code
            if code == "LANE_UNTAKEN":
                benign.add(nid)
                changed = True
            elif code == "UPSTREAM_SKIPPED" and all(
                    p in benign for p in preds_of.get(nid, [])):
                benign.add(nid)
                changed = True
    return benign


def _snapshot(sched: SchedulerState, results: dict[str, NodeResult]) -> dict:
    return {"statuses": {k: v.value for k, v in sched.statuses.items()},
            "taken": dict(sched.taken),
            "completed": sorted(k for k, r in results.items() if r.ok)}


def _attempt_nrid(rid: str, nid: str, attempt: int) -> str:
    """Per-attempt row id: retries keep history; stale attempts can't collide."""
    return f"{rid}:{nid}#{max(int(attempt), 0)}"


def _nrid(rid: str, nid: str) -> str:
    return f"{rid}:{nid}"


def _finite_timeout(v: Any) -> bool:
    import math
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) and v > 0


def _cap_result(result: NodeResult) -> None:
    """Bound runner-controlled values before they reach budgets/storage."""
    try:
        size = len(json.dumps(result.output, default=str))
    except (TypeError, ValueError):
        result.output = {"error": "unserializable output dropped"}
        size = 64
    if size > MAX_OUTPUT_BYTES:
        result.output = {"truncated": True,
                         "preview": json.dumps(result.output, default=str)[:4096]}
    if not isinstance(result.artifact_ids, list):
        result.artifact_ids = []
    result.artifact_ids = [str(a)[:160] for a in result.artifact_ids[:64]]
    for field in ("input_tokens", "output_tokens"):
        v = getattr(result, field)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            setattr(result, field, 0)
        else:
            setattr(result, field, max(0, int(v)))
    if isinstance(result.cost_usd, bool) or not isinstance(result.cost_usd, (int, float)):
        result.cost_usd = 0.0
    else:
        result.cost_usd = max(0.0, float(result.cost_usd))
    if not isinstance(result.message, str):
        result.message = str(result.message)[:500]
    result.message = redact(result.message[:500])
    if not isinstance(result.error_code, str):
        result.error_code = type(result.error_code).__name__
    result.error_code = result.error_code[:64]


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
    """Harden DB-restored rows: corrupt values coerce, never crash, never trust."""
    if not isinstance(d, dict):
        raise ValueError("corrupt node result row")
    try:
        status = NodeStatus(d.get("status", "success"))
    except (ValueError, AttributeError):
        status = NodeStatus.FAILURE
    output = d.get("output", {})
    if not isinstance(output, dict):
        output = {"value": output}
    arts = d.get("artifacts", [])
    if isinstance(arts, str):
        arts = [arts]
    if not isinstance(arts, list):
        arts = []

    def _num(v, default=0):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return default
        import math
        return v if math.isfinite(v) else default

    r = NodeResult(node_id=nid, status=status, output=output,
                   artifact_ids=[str(a)[:160] for a in arts[:64]],
                   model=str(d.get("model", ""))[:256],
                   provider=str(d.get("provider", ""))[:128],
                   input_tokens=max(0, int(_num(d.get("input_tokens", 0)))),
                   output_tokens=max(0, int(_num(d.get("output_tokens", 0)))),
                   cost_usd=max(0.0, float(_num(d.get("cost_usd", 0.0)))),
                   duration_s=max(0.0, float(_num(d.get("duration_s", 0.0)))),
                   attempt=max(0, int(_num(d.get("attempt", 1), 1))),
                   error_code=str(d.get("error_code", ""))[:64],
                   message=str(d.get("message", ""))[:500])
    _cap_result(r)
    return r
