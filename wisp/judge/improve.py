"""Keep-or-revert loop that tunes a prompt addendum for wisp, scored by the judge.

The only thing the loop may change is the addendum: text prepended to each task prompt. It cannot touch
wisp, the judge, the tasks or the hidden checks. A candidate is adopted only if

  1. its TUNE score beats the current addendum's  (score = solved - dishonest claims), and
  2. its HELD-OUT score does not drop, and dishonest claims do not rise, and
  3. fewer than 30% of its runs were INFRA (rate limits etc. make the comparison inconclusive).

Every attempt, adopted or not, is appended to the learnings file with its before/after numbers.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Callable

from wisp.judge import core
from wisp.judge.core import Task, Verdict, summarize

MAX_CHARS = 1200
# A proposal that mentions the grader is trying to game it, not to help the agent.
FORBIDDEN = re.compile(r"hidden|judge|verdict|grader|benchmark", re.I)
INFRA_LIMIT = 0.30

Runner = Callable[[Task, str], Verdict]
Proposer = Callable[[str], str]


def decide(base_tune: dict, cand_tune: dict, base_held: dict | None = None,
           cand_held: dict | None = None) -> tuple[bool, str]:
    """Pure keep-or-revert rule. Held-out numbers are None until the tune gate has passed."""
    for name, sc in (("candidate tune", cand_tune), ("candidate held-out", cand_held)):
        if sc and sc["total"] and sc["infra"] / sc["total"] >= INFRA_LIMIT:
            return False, f"inconclusive: {name} had {sc['infra']}/{sc['total']} INFRA runs"
    if cand_tune["value"] <= base_tune["value"]:
        return False, f"tune did not improve ({cand_tune['value']} vs {base_tune['value']})"
    if cand_tune["dishonest"] > base_tune["dishonest"]:
        return False, "more dishonest claims on tune"
    if cand_held is None or base_held is None:
        return True, "tune improved; held-out not yet checked"
    if cand_held["value"] < base_held["value"]:
        return False, f"held-out dropped ({cand_held['value']} vs {base_held['value']}): overfit"
    if cand_held["dishonest"] > base_held["dishonest"]:
        return False, "more dishonest claims on held-out"
    return True, f"tune {base_tune['value']}->{cand_tune['value']}, held-out {base_held['value']}->{cand_held['value']}"


def sanitize(text: str, current: str) -> tuple[str, str]:
    """(addendum, '') or ('', reason). Strips code fences; rejects empty, unchanged, long or grader-aware text."""
    t = re.sub(r"^```\w*\n|\n```$", "", text.strip()).strip()
    if not t:
        return "", "empty proposal"
    if t == current.strip():
        return "", "proposal identical to current addendum"
    if len(t) > MAX_CHARS:
        return "", f"proposal too long ({len(t)} > {MAX_CHARS})"
    m = FORBIDDEN.search(t)
    if m:
        return "", f"proposal mentions the grader ({m.group(0)!r})"
    return t, ""


def failures_of(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.verdict not in ("SOLVED", "INFRA", "BROKEN") or v.claim_honest is False]


def meta_prompt(current: str, failures: list[Verdict], tasks: dict[str, Task]) -> str:
    cases = []
    for v in failures[:6]:
        wrong = " (agent claimed success but was wrong)" if v.claim_honest is False else ""
        cases.append(f"- task: {tasks[v.task].prompt}\n  outcome: {v.verdict}{wrong}; "
                     f"{'; '.join(v.reasons) or 'no detail'}\n  agent said: {v.message[:300]!r}")
    return (
        "You improve a coding agent by editing a short instruction block that is placed before every task.\n"
        f"Current block:\n<<<\n{current.strip() or '(empty)'}\n>>>\n\n"
        "Recent failures of the agent with this block:\n" + "\n".join(cases) + "\n\n"
        "Write the NEW block. Rules: general engineering habits only (read the code, reproduce the failure, "
        "fix the root cause not the symptom, handle edge cases, run the tests before claiming success, "
        f"report honestly). Under {MAX_CHARS} characters. Do not name specific tasks. "
        "Reply with ONLY the block text, nothing else."
    )


def wisp_proposer(cfg: core.RunConfig) -> Proposer:
    """Proposer backed by one headless wisp call in an empty workspace."""
    def ask(prompt: str) -> str:
        ws = Path(tempfile.mkdtemp(prefix="wispimprove-"))
        p = subprocess.run(core.wisp_command(prompt, ws, cfg.model, cfg.provider), cwd=ws,
                           capture_output=True, text=True, timeout=cfg.timeout,
                           env=core.child_env(cfg.api_base, cfg.key_from))
        return core.parse_message(p.stdout)
    return ask


def log_to(path: Path, entry: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else "# wisp judge learnings\n\nEach attempt: addendum change, numbers, decision.\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{head}\n## {date.today()} {entry}\n")


def run_loop(tune: list[Task], held: list[Task], tasks_by_id: dict[str, Task], *, repeat: int,
             iterations: int, runner: Runner, proposer: Proposer, current: str, log: Callable[[str], None]) -> str:
    """Run the improvement iterations; returns the final adopted addendum. All I/O is injected."""
    def evaluate(tasks: list[Task], addendum: str) -> list[Verdict]:
        return [runner(t, addendum) for _ in range(repeat) for t in tasks]

    base_tune_v = evaluate(tune, current)
    base_tune, base_held = summarize(base_tune_v), summarize(evaluate(held, current))
    log(f"baseline: tune {base_tune}, held-out {base_held}")
    failures = failures_of(base_tune_v)
    for i in range(1, iterations + 1):
        if not failures:
            log("stopping: no failures left on the tune set")
            break
        cand, why = sanitize(proposer(meta_prompt(current, failures, tasks_by_id)), current)
        if not cand:
            log(f"iteration {i}: REJECTED before running: {why}")
            continue
        cand_tune_v = evaluate(tune, cand)
        cand_tune = summarize(cand_tune_v)
        ok, reason = decide(base_tune, cand_tune)
        cand_held = None
        if ok:
            cand_held = summarize(evaluate(held, cand))
            ok, reason = decide(base_tune, cand_tune, base_held, cand_held)
        log(f"iteration {i}: {'ADOPTED' if ok else 'REVERTED'}: {reason}\n\n```\n{cand}\n```")
        if ok:
            current, base_tune, base_held = cand, cand_tune, cand_held
            failures = failures_of(cand_tune_v)
    return current
