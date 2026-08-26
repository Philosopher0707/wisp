"""Background-subagent lifecycle tools — wait/list/result/send/cancel.

Extracted from ToolExecutor as part of the monolith extraction program.
Bodies are verbatim adaptations: only `self._get_background_manager()` /
`self._tool_error` became injected deps. Tests keep driving the executor
methods, which are one-line delegates.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SubagentDeps:
    """What each lifecycle tool needs from its host executor."""

    resolve_manager: Callable[[], Any | None]
    tool_error: Callable[[str, str], str]


async def wait(deps: SubagentDeps, func_args: dict[str, Any]) -> str:
    """subagent_wait — block until background agents settle.

    The parent calls this when it actually needs results (synthesis
    point). Polls manager state; no output between settles, so the
    CLI stays quiet while it waits.
    """
    manager = deps.resolve_manager()
    if manager is None:
        return deps.tool_error(
            "subagent_wait",
            "Subagent orchestrator not available — wire it via CompositionRoot",
        )

    ids = [str(i) for i in (func_args.get("agent_ids") or [])]
    terminal = ("completed", "failed", "cancelled")
    if not ids:
        ids = [
            e.id for e in manager.list_entries()
            if e.status not in terminal
        ]
    if not ids:
        # Nothing running: report whatever has already settled so the
        # caller still gets a picture instead of an empty shrug.
        ids = [e.id for e in manager.list_entries()]
    if not ids:
        return json.dumps({
            "status": "ok",
            "tool": "subagent_wait",
            "data": {
                "settled": [],
                "still_running": [],
                "note": "No background agents tracked; nothing to wait on.",
            },
            "metadata": {},
        }, ensure_ascii=False)

    try:
        timeout = float(func_args.get("timeout_seconds", 600))
    except (TypeError, ValueError):
        timeout = 600.0
    # Never out-wait the parent turn: the engine's wall-clock would
    # unwind the whole turn mid-poll. Clamp to remaining budget.
    try:
        from wisp.core.stateless import get_turn_deadline

        deadline_abs = get_turn_deadline()
        if deadline_abs is not None:
            remaining = deadline_abs - time.monotonic() - 5.0
            timeout = min(timeout, max(remaining, 1.0))
    except Exception:
        pass
    timeout = min(max(timeout, 1.0), 3600.0)
    deadline = time.monotonic() + timeout

    while True:
        entries = [manager.get(i) for i in ids if manager.get(i) is not None]
        pending = [
            e for e in entries
            if e.status not in ("completed", "failed", "cancelled")
        ]
        if not pending or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.25)

    settled = []
    still_running = []
    for i in ids:
        e = manager.get(i)
        if e is None:
            settled.append({
                "agent_id": i, "label": i, "role": "",
                "ok": False, "elapsed_seconds": 0.0,
                "error": "unknown agent id",
            })
            continue
        rec: dict[str, Any] = {
            "agent_id": e.id,
            "label": e.label,
            "role": getattr(e.contract, "role", ""),
            "elapsed_seconds": round(e.elapsed(), 1),
        }
        if e.status == "completed":
            rec["ok"] = True
            # Carry a summary so the parent can synthesize without an
            # extra subagent_result round-trip per child.
            summary = ""
            if e.history:
                summary = str(e.history[-1].get("summary", ""))
            if not summary and e.result is not None:
                summary = str(getattr(e.result, "output", "") or "")
            rec["summary"] = summary[:240]
        elif e.status == "failed":
            rec["ok"] = False
            rec["error"] = (e.error or "subagent reported failure")[:200]
        elif e.status == "cancelled":
            rec["ok"] = False
            rec["error"] = "cancelled"
        else:
            still_running.append({"agent_id": e.id, "label": e.label})
            continue
        settled.append(rec)

    ok_n = sum(1 for s in settled if s.get("ok"))
    note = f"{ok_n}/{len(settled)} settled"
    if still_running:
        note += (
            f"; {len(still_running)} still running after {timeout:.0f}s — "
            "call subagent_wait again or subagent_result individually"
        )
    else:
        note += ". Fetch full outputs with subagent_result."
    return json.dumps({
        "status": "ok",
        "tool": "subagent_wait",
        "data": {
            "settled": settled,
            "still_running": still_running,
            "note": note,
        },
        "metadata": {"agent_ids": ids},
    }, ensure_ascii=False)


async def list_agents(deps: SubagentDeps, func_args: dict[str, Any]) -> str:
    manager = deps.resolve_manager()
    if manager is None:
        return deps.tool_error("subagent_list", "Background agents not available")
    entries = manager.list(include_finished=bool(func_args.get("include_finished", True)))
    return json.dumps({
        "status": "ok",
        "tool": "subagent_list",
        "data": {"agents": entries, "count": len(entries)},
        "metadata": {},
    }, ensure_ascii=False)


async def result(deps: SubagentDeps, func_args: dict[str, Any]) -> str:
    manager = deps.resolve_manager()
    if manager is None:
        return deps.tool_error("subagent_result", "Background agents not available")
    agent_id = func_args.get("agent_id", "")
    if not agent_id:
        return deps.tool_error("subagent_result", "subagent_result requires an 'agent_id' argument")
    snapshot = await manager.result(agent_id, wait_seconds=float(func_args.get("wait_seconds", 0) or 0))
    if not snapshot.get("ok"):
        return deps.tool_error("subagent_result", snapshot.get("error", "unknown"))
    return json.dumps({
        "status": "ok",
        "tool": "subagent_result",
        "data": snapshot,
        "metadata": {"agent_id": agent_id, "status": snapshot.get("status")},
    }, ensure_ascii=False)


async def send(deps: SubagentDeps, func_args: dict[str, Any]) -> str:
    manager = deps.resolve_manager()
    if manager is None:
        return deps.tool_error("subagent_send", "Background agents not available")
    agent_id = func_args.get("agent_id", "")
    message = func_args.get("message", "")
    if not agent_id or not message:
        return deps.tool_error("subagent_send", "subagent_send requires 'agent_id' and 'message'")
    outcome = await manager.send(agent_id, message)
    if not outcome.get("ok"):
        return deps.tool_error("subagent_send", outcome.get("error", "send failed"))
    return json.dumps({
        "status": "ok",
        "tool": "subagent_send",
        "data": {
            "agent_id": outcome["agent_id"],
            "status": outcome["status"],
            "note": "Continuation running. Collect with subagent_result.",
        },
        "metadata": {"agent_id": agent_id},
    }, ensure_ascii=False)


async def cancel(deps: SubagentDeps, func_args: dict[str, Any]) -> str:
    manager = deps.resolve_manager()
    if manager is None:
        return deps.tool_error("subagent_cancel", "Background agents not available")
    agent_id = func_args.get("agent_id", "")
    if not agent_id:
        return deps.tool_error("subagent_cancel", "subagent_cancel requires an 'agent_id' argument")
    outcome = manager.cancel(agent_id)
    if not outcome.get("ok"):
        return deps.tool_error("subagent_cancel", outcome.get("error", "cancel failed"))
    return json.dumps({
        "status": "ok",
        "tool": "subagent_cancel",
        "data": outcome,
        "metadata": {"agent_id": agent_id},
    }, ensure_ascii=False)
