"""Headless benchmark runner.

Drives one turn per (model, task) pair through a real ``AgentRuntime``
with no transport I/O, then verifies the workspace outcome. The core is
injectable so tests can substitute mock providers; production builds an
Ollama-backed core per model.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from wisp.benchmark.scoring import ModelScorecard, TurnStats, score_events
from wisp.benchmark.tasks import DEFAULT_TASKS, BenchmarkTask


@dataclass
class BenchResult:
    """Outcome of one (model, task) pair."""

    model: str
    task_id: str
    passed: bool = False
    timed_out: bool = False
    error: str = ""
    verify_detail: str = ""
    duration_s: float = 0.0
    stats: TurnStats | None = None

    def status(self) -> str:
        if self.timed_out:
            return "TIMEOUT"
        return "PASS" if self.passed else "FAIL"


def make_ollama_core_factory(config: Any):
    """Build a core factory that targets the given provider config.

    The factory swaps the model per benchmark run while reusing every
    other setting — same base_url, same options, different brain.
    """
    from wisp.core.stateless import WispAgentCore
    from wisp.providers.factory import ProviderFactory

    def factory(model: str) -> WispAgentCore:
        cfg = config.model_copy() if hasattr(config, "model_copy") else config
        try:
            object.__setattr__(cfg, "model", model)
        except Exception:
            cfg.model = model
        provider = ProviderFactory().from_config(cfg)
        return WispAgentCore(config=cfg, provider=provider)

    return factory


async def run_task(
    task: BenchmarkTask,
    model: str,
    core_factory: Callable[[str], Any],
    timeout_s: float = 300.0,
    workdir: Path | None = None,
) -> BenchResult:
    """Run one task against one model in an isolated workspace."""
    result = BenchResult(model=model, task_id=task.id)

    ws = (workdir or Path.cwd()) / f"{task.id}-{uuid.uuid4().hex[:8]}"
    ws.mkdir(parents=True, exist_ok=True)

    try:
        task.setup(ws)
    except Exception as exc:  # setup failure = harness bug, not model fault
        result.error = f"setup failed: {exc}"
        return result

    core = core_factory(model)
    session = {
        "id": f"bench-{task.id}-{uuid.uuid4().hex[:6]}",
        "model": model,
        "workspace": str(ws),
        "messages": [],
        "title": f"[bench] {task.title}",
    }

    async def _drive() -> list[dict]:
        events: list[dict] = []
        # FULL permission mode keeps the run headless: nothing waits on
        # a human, everything auto-approves.
        async for ev in core.turn(session, task.prompt, approval_handler=None):
            events.append(ev)
        return events

    start = time.monotonic()
    try:
        events = await asyncio.wait_for(_drive(), timeout=timeout_s)
    except asyncio.TimeoutError:
        result.timed_out = True
        result.duration_s = time.monotonic() - start
        return result
    except Exception as exc:
        result.error = f"turn crashed: {exc}"[:200]
        result.duration_s = time.monotonic() - start
        return result
    result.duration_s = time.monotonic() - start

    result.stats = score_events(events)
    ok, detail = task.verify(ws)
    if ok and task.verify_events is not None:
        # Capability gate: outcome alone isn't enough when the task is
        # about HOW it was done (delegation, not soloing).
        eok, edetail = task.verify_events(events)
        if not eok:
            ok = False
            detail = f"events: {edetail}"
    result.passed = ok
    result.verify_detail = detail[:200]
    return result


async def run_benchmark(
    models: list[str],
    tasks: list[BenchmarkTask] | None = None,
    core_factory: Callable[[str], Any] | None = None,
    timeout_s: float = 300.0,
    workdir: Path | None = None,
    on_result=None,
) -> list[BenchResult]:
    """Run every (model, task) pair sequentially and collect results."""
    selected = tasks if tasks else list(DEFAULT_TASKS)
    if core_factory is None:
        from wisp.config import load_config

        core_factory = make_ollama_core_factory(load_config())

    results: list[BenchResult] = []
    for model in models:
        for task in selected:
            res = await run_task(task, model, core_factory, timeout_s, workdir)
            results.append(res)
            if on_result:
                on_result(res)
    return results


def aggregate(models: list[str], results: list[BenchResult]) -> list[ModelScorecard]:
    """Group flat results into per-model scorecards, preserving input order."""
    cards: dict[str, ModelScorecard] = {}
    for res in results:
        card = cards.setdefault(res.model, ModelScorecard(model=res.model))
        card.task_rows.append({
            "task_id": res.task_id,
            "status": res.status(),
            "duration_s": round(res.duration_s, 1),
            "tool_calls": res.stats.tool_calls if res.stats else 0,
            "detail": res.error or res.verify_detail,
        })
        card.total_duration_s += res.duration_s
        if res.timed_out:
            card.timed_out += 1
        elif res.passed:
            card.passed += 1
        else:
            card.failed += 1
    ordered = [cards[m] for m in models if m in cards]
    extra = [c for c in cards.values() if c not in ordered]
    return ordered + extra
