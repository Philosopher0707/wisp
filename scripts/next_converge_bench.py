#!/usr/bin/env python3
"""Run a benchmark task THROUGH the convergence controller.

The existing `wisp bench` runs one turn per (model, task) and verifies the
workspace afterwards — which measures a *turn*, not convergence. This runs
the same deterministic verifier as the acceptance criterion, but inside the
loop: after each attempt the harness re-measures, and a failed attempt is
followed by a strategy the recovery ladder chooses.

The verifier is the authority in both modes, so the two are directly
comparable: same task, same workspace, same pass/fail rule, different
control flow.

Usage:
    env -u PYTHONPATH WISP_OLLAMA_URL=... WISP_MAX_TOKENS=65536 \
        .venv/bin/python scripts/next_converge_bench.py \
        --model nemotron-3-ultra:cloud --tasks create-function \
        --max-attempts 2 --out .workbuddy-ai/memory/next/converge-run.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class VerifyProbe:
    """Acceptance evidence from the benchmark's own deterministic verifier.

    The producer name says exactly who measured. It is not the agent, not
    the model and not the turn's guard — which is what lets the record
    establish independence rather than assert it.
    """

    PRODUCER = "benchmark.verify"

    def __init__(self, task, criteria_id: str):
        self._task = task
        self._criteria_id = criteria_id

    def measure(self, workspace: str):
        from wisp.core.acceptance import CriterionKind, Evidence, content_digest
        from wisp.core.convergence import Measurement

        try:
            passed, detail = self._task.verify(Path(workspace))
        except Exception as exc:            # verifier bug ≠ model fault
            passed, detail = False, f"verifier raised: {exc}"
        payload = {"passed": bool(passed), "detail": str(detail)[:400]}
        evidence = Evidence(
            evidence_id=f"{self._criteria_id}:{content_digest(payload)[:16]}",
            criteria_id=self._criteria_id,
            producer=self.PRODUCER,
            kind=CriterionKind.DETERMINISTIC,
            content_hash=content_digest(payload),
            observations=tuple(f"{k}={v}" for k, v in sorted(payload.items())),
            metadata={"task": self._task.id},
        )
        line = (f"{self._criteria_id}: "
                f"{'satisfied' if passed else 'NOT satisfied'}"
                + (f" — {payload['detail']}" if payload["detail"] else ""))
        return Measurement(observations={self._criteria_id: payload},
                           evidence=(evidence,), lines=(line,))


def _criterion(criteria_id: str, description: str):
    from wisp.core.acceptance import AcceptanceCriteria, CriterionKind

    def _check(payloads):
        payload = payloads.get(criteria_id)
        if payload is None:
            return True                     # unmeasured → INCONCLUSIVE
        return bool(payload.get("passed"))

    return AcceptanceCriteria(
        criteria_id=criteria_id, description=description,
        kind=CriterionKind.DETERMINISTIC, required=True, check=_check)


async def run_one(task, model: str, workdir: Path, max_attempts: int,
                  timeout_s: float) -> dict:
    from wisp.benchmark.runner import _git_baseline
    from wisp.benchmark.tasks import tasks_by_ids  # noqa: F401  (import check)
    from wisp.config import WispConfig
    from wisp.core.convergence import (
        ConvergenceController, Objective, TurnObservation,
    )
    from wisp.core.events import EventType
    from wisp.core.stateless import WispAgentCore
    from wisp.providers.factory import ProviderFactory

    tool_histograms: list[dict] = []

    ws = workdir / f"converge-{task.id}-{uuid.uuid4().hex[:8]}"
    ws.mkdir(parents=True, exist_ok=True)
    task.setup(ws)
    _git_baseline(ws)

    cfg = WispConfig().replace(
        model=model, workspace=str(ws),
        # An autonomous coding agent must be able to RUN things. The
        # benchmark's own factory leaves the default mode, which blocks
        # run_bash — and a turn that cannot execute a test cannot verify.
        permission_mode="full",
    )
    provider = ProviderFactory().from_config(cfg)
    # The executor MUST be wired: without it every non-READ tool is denied
    # by the read-only fallback in `stateless.py`, so the agent cannot write
    # and the run measures nothing. See `benchmark/runner.py` for the same
    # fix and the measurement that found it.
    from wisp.tool_executor import ToolExecutor
    core = WispAgentCore(config=cfg, provider=provider,
                         tool_executor=ToolExecutor(config=cfg))

    criteria_id = f"task:{task.id}"
    probe = VerifyProbe(task, criteria_id)

    async def run_turn(request):
        from wisp.autonomous import compose_attempt_prompt, observe_turn

        session = {
            "id": f"converge-{task.id}-{uuid.uuid4().hex[:6]}",
            "model": model, "workspace": str(ws),
            "messages": [], "title": f"[converge] {task.title}",
        }
        prompt = compose_attempt_prompt(request)
        events = []

        async def _drive():
            async for ev in core.turn(session, prompt, approval_handler=None):
                events.append(ev)

        try:
            await asyncio.wait_for(_drive(), timeout=timeout_s)
        except asyncio.TimeoutError:
            observation = TurnObservation(
                turn_succeeded=False, terminal_outcome="incomplete",
                failure_message=f"turn exceeded {timeout_s}s",
                failure_recoverable=False, tool_calls=len(events))
        except Exception as exc:
            observation = TurnObservation(
                turn_succeeded=False, terminal_outcome="failed",
                failure_message=f"turn crashed: {exc}"[:200],
                failure_recoverable=False, tool_calls=len(events))
        else:
            observation = observe_turn(events)

        # The tool-name histogram, plus the outcome of every MUTATING call.
        # `changed_files == []` says the workspace did not move; it cannot say
        # whether the agent never called a mutating tool, called one whose
        # arguments were rejected, or called one that failed. Those are three
        # different defects and only the trace distinguishes them.
        MUTATORS = ("write_file", "edit_file", "edit_file_multi", "fs_mutate")
        names: dict[str, int] = {}
        trace: list[dict] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "tool_call":
                key = str(event.get("name", "?"))
                names[key] = names.get(key, 0) + 1
                if key in MUTATORS:
                    trace.append({
                        "call": key,
                        "args": json.dumps(event.get("arguments", {}),
                                           default=str)[:400],
                        "blocked": event.get("_blocked", ""),
                        "denial": event.get("_denial", ""),
                    })
            elif event.get("type") == "tool_result":
                key = str(event.get("name", "?"))
                if key in MUTATORS:
                    trace.append({
                        "result_of": key,
                        "text": str(event.get("result", ""))[:400],
                    })
        tool_histograms.append({"attempt": request.attempt, "rung": request.rung,
                                "tools": names, "mutation_trace": trace})
        return observation

    controller = ConvergenceController(
        run_turn=run_turn, probe=probe, max_attempts=max_attempts)
    started = time.monotonic()
    result = await controller.converge(Objective(
        goal=task.prompt, workspace=str(ws),
        criteria=(_criterion(criteria_id, task.title),),
        max_attempts=max_attempts))
    payload = result.to_dict()
    payload.update({
        "task_id": task.id, "model": model, "workspace": str(ws),
        "wall_clock_s": round(time.monotonic() - started, 1),
        "tool_histograms": tool_histograms,
    })
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="next_converge_bench")
    ap.add_argument("--model", "-m", required=True)
    ap.add_argument("--tasks", "-t", default="")
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--workdir", default=".workbuddy-ai/memory/next/converge")
    ap.add_argument("--out", default=".workbuddy-ai/memory/next/converge-run.json")
    args = ap.parse_args(argv)

    from wisp.benchmark.tasks import tasks_by_ids
    from wisp.config import WispConfig

    cfg = WispConfig()
    print(f"[converge_bench] ollama_url={cfg.ollama_url} model={args.model} "
          f"max_tokens={cfg.max_tokens}", flush=True)

    tasks = tasks_by_ids([t.strip() for t in args.tasks.split(",") if t.strip()])
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for task in tasks:
        print(f"  running {task.id} (max_attempts={args.max_attempts}) ...",
              flush=True)
        row = asyncio.run(run_one(task, args.model, workdir,
                                  args.max_attempts, args.timeout))
        rows.append(row)
        attempts = row.get("attempts", [])
        print(f"    -> {task.id}: converged={row['converged']} "
              f"goal_state={row['goal_state']} "
              f"attempts={[a['rung'] for a in attempts]} "
              f"({row['wall_clock_s']}s)", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"wrote {out}")
    return 0 if all(r["converged"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
