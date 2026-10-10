# The REPL and the objective-level loop (design, 2026-10-10)

**Question.** Wisp has a loop that keeps trying until the repository itself says the objective is met (`wisp converge`), a durable graph engine, an experimental phase loop, and a plain agent turn. Which of them does the interactive REPL use, which should it use, and how is that wired and proven?

## 1. What was researched

### 1.1 Karpathy's loop (primary source: `karpathy/autoresearch`, `program.md`; the rest is secondary reporting, marked)

| Idea | What the source says | Where it comes from |
|---|---|---|
| **The verifier is outside the agent** | The agent may edit one file (`train.py`) and may not touch `prepare.py`, which holds the evaluation (`evaluate_bpb`, "the ground-truth metric"). One metric, `val_bpb`. | `program.md` (read) |
| **A fixed budget per attempt** | Each run trains for 5 minutes of wall clock; a run over 10 minutes is killed and counted as a failure. Attempts are comparable because the budget is the same. | `program.md`, README (read) |
| **Keep or revert** | Metric improved: keep the commit and advance the branch. Equal or worse: reset to where it started. Crash: log and move on. Every attempt is a row in `results.tsv`: commit, metric, memory, `keep`/`discard`/`crash`, a description. | `program.md` (read) |
| **Persistent state outside the model** | The log and the git history carry the search; the model's context does not. | `program.md`; secondary reporting on "loop engineering" names it as the second of three parts: verifier, state, stop conditions |
| **A simplicity rule** | Removing code at equal or better results is a win; a tiny gain that adds mess may not be worth keeping. | `program.md` (read) |
| **Do not stop to ask** | Once the loop starts, the agent keeps going until the human interrupts. | `program.md` (read) |
| **Verifiability** | LLMs automate what you can verify; that is why they are strong at code (you can run it) and uneven elsewhere. Give an agent a checkable goal and it loops until it meets it. | secondary reporting of Karpathy's talks (not read in the original) |
| **Keep the AI on a leash; autonomy slider** | Work in small chunks you can check; raise autonomy only where reliability has been shown; let the user choose how much independence to give. | secondary reporting (not read in the original) |
| **A weak verifier lies** | A loop is only as honest as its check; one reported case overfit its benchmark. | secondary reporting, one case |

What this asks of a harness: **a verifier the agent cannot write, a bounded number of attempts, state that survives the model, an honest stop (met / not met / needs a human), and autonomy that is chosen, not assumed.**

### 1.2 What Wisp already has (read in the source on 2026-10-10)

| Loop | Where | Reached from the REPL? |
|---|---|---|
| **A. The agent turn** | `WispAgentCore.turn` + `AgentRuntime.run_turn` | Yes. Every prompt. One turn, then it stops. |
| **B. The graph engine** | `wisp/graph/*`, entered by the REPL's strategy gate (`wisp/coding.py`) for "graph-worthy" prompts; nodes are subagents running loop A | Yes, for multi-part prompts (now routed by words, not substrings, and reported honestly: PR "REPL graph wiring"). Its reviewer is a model, not a harness measurement. |
| **C. The phase loop** | `wisp/core/graph/loop.py`, `phases.py` | No (tests only; its own docstring disowns it). |
| **D. The objective-level loop** | `wisp/core/convergence.py` (controller, criteria derivation, probes, journal, recovery ladder), wired by `wisp/autonomous.py`, entered by `wisp converge` | **No.** Its own docstring: every `run_turn` caller (CLI, REPL, SDK, server, TUI) dispatches exactly one turn and returns. |

Loop D is Karpathy's loop in this codebase: the host derives acceptance criteria from the objective, measures a **baseline** before any attempt, runs an attempt in a **fresh session**, **measures again with the harness** (`CommandProbe`, `SymbolProbe`), decides with `acceptance.evaluate`, names the failure with `classify_failure`, chooses what may be tried next with `RecoveryLadder`, and journals every attempt so a crash can **resume** without repeating mutations. `GOAL_MET` means "the repository says so"; exhaustion is a state (`GOAL_STAGNATED`, `GOAL_UNVERIFIED`, `ESCALATED_TO_HUMAN`), never a success. The model's prose is not an input to any decision.

### 1.3 What the derivation can and cannot verify (run on 2026-10-10)

`explain_acceptance` on real prompts: `fix the failing tests`, `make the test suite pass` and `The tests in test_x.py are failing. Fix ...` derive a command criterion with reason **stated** (the user said the suite must be green). `add a function named mean to totals.py` derives a symbol criterion. `explain how totals.py works`, `refactor ...`, `rename ...` and `implement the login api ... across the module` derive only an **undetermined** guard. That split is Karpathy's verifiability thesis made operational: some objectives have a checkable end, others do not, and only the first kind should be looped.

## 2. The gap

The REPL has the agent turn and the graph, and neither has a verifier the model cannot write. A prompt such as `fix the failing tests` runs one turn, the model says "done", and nothing but the model's own account says whether the tests pass. The loop that could check it exists, is tested, and is not connected. (Audit finding, `WISP_PERSISTENT_GRAPH_LOOP_ALIGNMENT_AUDIT.md` 1.1, 1.2: the target sits in the gap between A and B, and the dominant defect is "written-but-unwired controls".)

## 3. Design

### 3.1 Principles

1. **Verifiable work is looped; everything else is not.** Routing follows the derivation's own `stated` reason (or an explicit symbol criterion), never a keyword list.
2. **The verifier is the harness.** The REPL adds no judgement of its own; it calls `converge_on_objective`, which already consumes `acceptance.evaluate`, `classify_failure` and `RecoveryLadder`.
3. **Autonomy is chosen.** `/converge <objective>` always loops (when something checkable can be derived; otherwise it says why it cannot). Auto-routing of ordinary prompts is on by default only for `stated` objectives and has a kill switch: `WISP_REPL_CONVERGE=off`.
4. **Bounded.** At most 3 attempts by default (`WISP_REPL_CONVERGE_ATTEMPTS`, clamped 1 to 10); each attempt is an ordinary turn with the existing iteration bound; the baseline and probe have their own timeouts. Ctrl-C stops the loop between or inside an attempt.
5. **State outside the model.** A JSONL journal under `<workspace>/.wisp/converge/`; `/converge resume` continues without re-running attempts. The REPL conversation receives the prompt and a short, bounded note (state, attempts, what is met and unmet, changed files, journal), so the next prompt knows what happened.
6. **Honest words.** The line the user reads says `proven` only for `GOAL_MET`. Everything else says what was not shown: `not proven (stagnated after 3 attempts)`, `no checkable acceptance`, `needs you`.
7. **No silent destruction.** Rollback (Karpathy's "reset") stays off in the REPL: restoring a snapshot would overwrite the user's own concurrent edits. `wisp converge --allow-rollback` remains the explicit tool for that.

### 3.2 Flow

```
REPL prompt
  |- slash command  -> dispatcher (as today); /converge is handled by the REPL itself, like /multiline
  `- strategy gate (wisp/coding.py)
       |- question                                  -> agent turn
       |- WISP_REPL_CONVERGE != off and derivation says "stated" -> CONVERGE  (new)
       |- graph hints (word-matched) >= threshold    -> graph (loop B)
       `- otherwise                                  -> agent turn

CONVERGE (wisp/autonomous_repl.py)
  derive criteria (host) -> baseline measure (harness) -> attempt k in a FRESH session
    (attempt prompt = objective + acceptance conditions + harness evidence; conversation context is passed as a bounded reference block)
  -> harness measures again -> evaluate -> classify -> ladder picks the next rung -> journal row
  -> stop on GOAL_MET | attempts exhausted | escalation | Ctrl-C
  -> print the verdict, put prompt + note into the REPL session
```

### 3.3 What is reused and what is new

Reused unchanged: `ConvergenceController`, `explain_acceptance`, `CommandProbe`, `SymbolProbe`, the recovery ladder, the journal format and `resume`. New and small: `wisp/autonomous_repl.py` (assess, run, resume, format), `converge_on_objective` gains three optional arguments (`approval_handler`, `on_event`, `context`) so an attempt can use the REPL's own approval prompt and renderer, the strategy gate's third branch, and the `/converge` command in the REPL loop.

### 3.4 Failure modes and what the design does about them

| Failure | Handling |
|---|---|
| The check is weak (a red suite that is not the objective) | `stated` only; baseline-relative criteria for everything else; the derivation reason is journaled. Not solved in general: a weak test suite makes a weak verifier. |
| A fresh session loses the conversation | A bounded context block (last six messages, trimmed) is added to each attempt prompt as reference. |
| Cost surprise | Visible announcement before attempt 1 (verifier, attempts, how to stop); attempt cap; kill switch. |
| Permission modes that block the agent from running tests | The REPL's own permission mode is passed through; acceptance does not depend on it (the harness measures). |
| Crash or Ctrl-C mid-loop | Journal; `/converge resume`; no automatic rollback. |
| The loop proves "tests pass" but the user wanted more | The verdict says exactly what was measured. It never says the task is "done". |

## 4. Proof plan (done: `tests/test_repl_converge.py`, `tests/test_repl_converge_pty.py`)

1. Unit: the routing table (which prompts loop, which do not), the verdict wording, the session note, the journal path and resume pick.
2. **Integration, end to end, nothing in the loop faked**: a real `AgentRuntime` and engine, real tools, a real pytest probe, the real controller and journal, with a scripted model that (a) fixes the bug on attempt 2 after failing on attempt 1, (b) never fixes it, (c) edits a protected test file to make it pass (the loop must say it did not prove the objective). Asserted: the harness verdict, the number of attempts, the journal, the files, the printed lines, the session note.
3. **Through the real REPL** (a pty, `python -m wisp repl`, the mock provider): the auto-route announcement, `/converge`, an honest "not proven" when the mock changes nothing, the session note present afterwards, and `WISP_REPL_CONVERGE=off` restoring the single turn.
4. Mutation probe of the new code.

## 5. What the end-to-end tests found (2026-10-10)

Building the proof plan before the code was finished turned up four things that no existing test had caught. They are why the integration tests drive the real runtime, a real pytest probe and a real pty, not stand-ins.

| Finding | Evidence | What was done |
|---|---|---|
| **`resume` never resumed.** `ConvergenceController._load_journal` read the `derivation` line (written since ADR-0048 R3, between the baseline and the first attempt) as an attempt, hit a `KeyError`, and the surrounding `break` dropped every attempt after it. A resumed run started again at attempt 0 and repeated mutations the journal said were done. | `/converge resume` ran `attempt 1/3 (INITIAL)` again; the journal then held `index` 0, 0, 1. | Fixed: the loader skips any record kind that is not an attempt. Two regression tests in `tests/reliability/test_next_convergence_controller.py`, RED first. |
| **The derivation grammar stops at a dot.** `_WANTS_FIX_RE` uses `[^.]{0,40}`, so `Fix totals.py so they pass` is not "stated" (the dot in the file name ends the match) while `Fix the failing tests` is. | `explain_acceptance` on both prompts. | Not widened here (it is ADR-0048's grammar); the refusal text now suggests wording that works, and the limitation is recorded. |
| **The detected check assumes a `tests/` directory.** The project's verification command is `python -m pytest tests/ -x -q` whenever a pytest config exists; a project with tests at the root gets a command that exits 4. | `wisp/environment.py::_detect_verification_commands`. | Not changed; with no usable command the objective is simply not verifiable and nothing is looped. |
| **The harness runs the first `python` on `PATH`.** On this machine that was another environment without the plugins pytest imports, so the probe could not run and the loop correctly concluded `goal_failed` (it proves nothing rather than guessing). | the first run of the scenario tests. | Tests pin the interpreter. Which interpreter a project's check should use is the open "interpreter-level harness" item. |

Also observed, and pinned by a test: a scripted model that "passes" by editing `tests/test_totals.py` to `assert True` is **not** proven. The derivation declares the suite and its configuration as inputs of the check (`command_inputs_unchanged`), so a changed input is untrustworthy, not progress.

## 6. Not done, stated

- No rollback in the REPL; no cost accounting per loop (the dashboard PR would show it).
- Loop C (`core/graph/loop.py`) stays unwired; this design does not use it.
- A graph run still has a model reviewer; making the graph's verification node a harness probe is a separate change.
- The auto-route has no measured false-positive rate on real sessions.
