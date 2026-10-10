"""The REPL's face of the objective-level loop (design: docs/harness/repl-converge-design.md).

`core/convergence.py` owns the loop and `wisp/autonomous.py` wires it to a runtime. This module is the third piece: it decides which REPL prompts are worth
looping, runs the loop on the REPL's own runtime and event loop, shows progress as it happens, and puts an honest account of the result into the
conversation. It adds no judgement of its own: every verdict is the controller's, and the controller consumes the harness's measurements.

A runner (`wisp.cli.repl.ReplRunner`, or anything with the same attributes) must provide: `runtime`, `loop`, `renderer`, `out`, `config`, `transport`,
`session` and `_turn_task`. The last is what Ctrl-C cancels, exactly as for an ordinary turn.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from wisp.core.convergence import AttemptRecord, CriteriaDeclarationRejected, explain_acceptance
from wisp.core.goal import GoalState

DEFAULT_ATTEMPTS = 3
MAX_ATTEMPTS = 10
CONTEXT_MESSAGES = 6
CONTEXT_CHARS = 300
NOTE_CHARS = 1200
_USAGE = "Usage: /converge <objective> | /converge resume | /converge status"


@dataclass(frozen=True)
class Assessment:
    """Whether the host can say what would show an objective is done, and in what words."""

    verifiable: bool
    why: str
    conditions: tuple[str, ...] = ()


def routing_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Ordinary prompts are routed to the loop unless `WISP_REPL_CONVERGE=off` (an operator's kill switch; `/converge` still works)."""
    env = os.environ if environ is None else environ
    return env.get("WISP_REPL_CONVERGE", "auto").strip().lower() not in ("off", "0", "false", "no")


def attempts_from_env(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        value = int(env.get("WISP_REPL_CONVERGE_ATTEMPTS", DEFAULT_ATTEMPTS))
    except ValueError:
        value = DEFAULT_ATTEMPTS
    return max(1, min(value, MAX_ATTEMPTS))


def assess(objective: str, workspace: str) -> Assessment:
    """Can the host verify this objective? Only if the user STATED a checkable end ("make the suite pass") or named a symbol to define.

    The words come from the derivation's own reasons (`stated` / `unstated` / `undetermined`), not from a keyword list of ours.
    """
    try:
        derivation = explain_acceptance(objective, workspace)
    except CriteriaDeclarationRejected as exc:
        return Assessment(False, f"the criteria declaration in the objective was rejected: {str(exc)[:200]}")
    except Exception as exc:  # noqa: BLE001 — an unreadable workspace is "cannot verify", not a crash
        return Assessment(False, f"acceptance could not be derived ({type(exc).__name__})")
    conditions = tuple(c.description for c in derivation.criteria if c.required)
    if any(reason == "stated" for _cid, reason, _span in derivation.reasons):
        return Assessment(True, "the objective states that the tests must pass", conditions)
    if any(c.required and c.criteria_id.startswith("symbol:") for c in derivation.criteria):
        return Assessment(True, "the objective names a symbol that must exist", conditions)
    return Assessment(False, "nothing in the objective says what would show it is done", conditions)


# ── state outside the model ──────────────────────────────────────────────────

def journal_dir(workspace: str) -> Path:
    return Path(workspace) / ".wisp" / "converge"


def new_journal(workspace: str) -> Path:
    return journal_dir(workspace) / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jsonl"


def latest_journal(workspace: str) -> Path | None:
    found = sorted(journal_dir(workspace).glob("*.jsonl"))
    return found[-1] if found else None


def _objective_sidecar(journal: Path) -> Path:
    return journal.with_suffix(".objective.txt")


def conversation_context(session: Mapping[str, Any]) -> str:
    """The last few user and assistant messages, trimmed: a reference for attempts that start in a fresh session, never an instruction."""
    lines: list[str] = []
    for msg in list(session.get("messages") or [])[-CONTEXT_MESSAGES:]:
        if msg.get("role") not in ("user", "assistant"):
            continue
        text = " ".join(str(msg.get("content") or "").split())[:CONTEXT_CHARS]
        if text:
            lines.append(f"{msg['role']}: {text}")
    return "\n".join(lines)


# ── words ────────────────────────────────────────────────────────────────────

def verdict_text(goal_state: GoalState, attempts: tuple[AttemptRecord, ...], reason: str, conditions: tuple[str, ...]) -> list[str]:
    """What the user reads. `proven` appears only for GOAL_MET; every other state says what was not shown."""
    n = len(attempts)
    plural = "attempt" if n == 1 else "attempts"
    last = attempts[-1] if attempts else None
    unmet = [f"      - {u}" for u in (last.unmet if last else ())][:5]
    if goal_state is GoalState.GOAL_MET:
        what = "; ".join(conditions[:3]) or "every required criterion"
        return [f"✓ proven by the harness after {n} {plural}: {what}"]
    if goal_state is GoalState.GOAL_STAGNATED:
        head = f"✗ not proven: no measurable progress after {n} {plural}."
    elif goal_state is GoalState.ESCALATED_TO_HUMAN:
        return [f"✋ needs you: {reason[:300] or 'the loop has nothing legal left to try'}"] + (["    still unmet:"] + unmet if unmet else [])
    elif goal_state is GoalState.GOAL_FAILED:
        head = f"✗ not proven: the last attempt failed ({(last.observation.failure_message if last else '')[:160] or 'no detail'})."
    else:
        head = f"✗ not proven: the harness could not confirm the objective after {n} {plural}."
    return [head] + (["    still unmet:"] + unmet if unmet else [])


def _journal_lines(journal: Path) -> list[str]:
    return [f"  journal: {journal}", "  /converge resume continues from the last attempt"]


# ── running ──────────────────────────────────────────────────────────────────

def _workspace(config: Any) -> str:
    value = config.get("workspace", ".") if isinstance(config, dict) else getattr(config, "workspace", ".")
    return str(value or ".")


def _say(runner: Any, text: str = "") -> None:
    try:
        runner.out.write(text + "\n")
        runner.out.flush()
    except Exception:  # noqa: BLE001 — a closed terminal must not end the loop
        pass


def run_repl_converge(runner: Any, objective: str, *, attempts: int | None = None, resume: bool = False, journal: Path | None = None, record: bool = True) -> Any:
    """Run `objective` to an honest end on the REPL's own runtime. Returns the `ConvergenceResult`, or None when it was refused or interrupted.

    `record` puts the prompt and a bounded note into the session so the next prompt knows what happened; the REPL saves the session as for any turn.
    """
    from wisp.autonomous import converge_on_objective
    from wisp.coding import record_turn

    workspace = _workspace(runner.config)
    budget = max(1, min(int(attempts or attempts_from_env()), MAX_ATTEMPTS))
    assessment = assess(objective, workspace)
    if not assessment.verifiable and not resume:
        _say(runner, f"✗ nothing to prove: {assessment.why}.")
        _say(runner, "  Say what should pass, in words like 'fix the failing tests' or 'make the test suite pass', or send it as an ordinary prompt.")
        return None
    path = journal or new_journal(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not resume:
        _objective_sidecar(path).write_text(objective, encoding="utf-8")
    _say(runner, f"loop: {assessment.why}; up to {budget} attempts, the harness measures after each (Ctrl-C stops)")
    for condition in assessment.conditions[:3]:
        _say(runner, f"  must show: {condition}")

    def attempt_start(request: Any) -> None:
        _say(runner, f"▶ attempt {request.attempt + 1}/{budget} ({request.rung})")

    def attempt_done(rec: AttemptRecord) -> None:
        changed = len(rec.observation.changed_files)
        unmet = ", ".join(rec.unmet[:3]) or "nothing"
        _say(runner, f"  attempt {rec.index + 1} measured: {rec.verdict}; unmet: {unmet}; files changed: {changed}; {round(rec.duration_s)}s")

    renderer = getattr(runner, "renderer", None)
    on_event = (lambda ev: renderer.render_event(runner.out, ev)) if renderer is not None and hasattr(renderer, "render_event") else None
    coroutine = converge_on_objective(
        objective, workspace, model=getattr(runner.config, "model", None) or None, max_attempts=budget,
        root=SimpleNamespace(runtime=runner.runtime), journal_path=path, resume=resume,
        approval_handler=getattr(runner.transport, "approve", None), on_event=on_event, on_attempt_start=attempt_start, on_record=attempt_done,
        context=conversation_context(runner.session))
    if renderer is not None:
        renderer.reset()
        renderer.wait_start(runner.out)
    task = runner.loop.create_task(coroutine)
    runner._turn_task = task
    result = None
    try:
        result = runner.loop.run_until_complete(task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        _say(runner, "■ stopped. The workspace is left as the last attempt left it; nothing was rolled back.")
        for line in _journal_lines(path):
            _say(runner, line)
        if record:
            record_turn(runner, objective, f"[loop] stopped by the user. {_journal_lines(path)[0].strip()}")
        return None
    except Exception as exc:  # noqa: BLE001 — a loop that cannot start is reported, not raised into the REPL
        _say(runner, f"✗ the loop could not run: {type(exc).__name__}: {str(exc)[:300]}")
        if record:
            record_turn(runner, objective, f"[loop] could not run: {type(exc).__name__}: {str(exc)[:300]}")
        return None
    finally:
        runner._turn_task = None
        if renderer is not None:
            renderer.wait_stop(runner.out)
            renderer.flush(runner.out)
    lines = verdict_text(result.goal_state, tuple(result.attempts), result.reason, assessment.conditions) + (_journal_lines(path) if not result.converged else [])
    for line in lines:
        _say(runner, line)
    if record:
        record_turn(runner, objective, ("[loop] " + " ".join(s.strip() for s in lines))[:NOTE_CHARS])
    return result


def handle_command(runner: Any, line: str) -> None:
    """`/converge <objective>`, `/converge resume`, `/converge status`."""
    argument = line.partition(" ")[2].strip()
    workspace = _workspace(runner.config)
    if not argument:
        _say(runner, _USAGE)
        return
    if argument == "status":
        journal = latest_journal(workspace)
        _say(runner, f"latest journal: {journal}" if journal else "no loop has run in this workspace")
        return
    if argument == "resume":
        journal = latest_journal(workspace)
        sidecar = _objective_sidecar(journal) if journal else None
        if journal is None or sidecar is None or not sidecar.exists():
            _say(runner, "nothing to resume: no journal with a recorded objective in this workspace")
            return
        run_repl_converge(runner, sidecar.read_text(encoding="utf-8"), resume=True, journal=journal)
        return
    run_repl_converge(runner, argument)
