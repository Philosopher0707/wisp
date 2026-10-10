# R5: "same observation, nothing changed" (design; P1 and P2 built, observe by default)

Status: designed 2026-10-07; P1 (decision) and P2 (delivery) built 2026-10-10 on `feat/reasoning-r5`, from `origin/main` 01fd8c7.

**Name.** This rule is called R5 in the code and in the field log. The roadmap in `reasoning-core-design.md` also lists an "R5" and "R6" that are refactors of existing behaviour and were never given a rule id in code (`RULES` was R1-R4); this is the first rule with the id R5.
Owner request: after a real REPL session in another project (`tbrr`) looped, "suggest how we could make it work".

## 1. The failure (a real session, not a scripted one)

The agent ran `pytest tests/test_security_regressions.py -v`, then `... -v | cat`, then `... -v | tail -20`. Each returned `7 passed, 5 xfailed`, exit 0, the same text. The project's `pyproject.toml` has `addopts = "-q --strict-markers"`; `-q` and `-v` cancel, so `-v` can never print test names (reproduced in a temp project: `-v` prints dots, `-vv` prints names, `-rx` prints the xfail reasons). The answer was already in the first run: the test file's own docstring says the five are `xfail(strict=True)` because the defect is open.

No rule fired. R2 counts repeated **failures** (an exit code other than 0, or an error result); every call here succeeded. R3 counts repeated refusals. Nothing in the core looks at *a successful call that returned nothing new*. The roadmap row "search limits, token budgets per turn, probe detection" ("not designed") is the same gap.

## 2. What R5 detects

A repeat is **no progress** when the same normalised observation appears again and nothing that could change it has happened in between.

- **Observation = a digest of the tool's output text**, not of the command. In the real session the commands differed (`-v`, `| cat`, `| tail -20`); the output did not.
- **Normalisation** removes only what varies between identical runs: durations (`in 0.04s`, `906ms`), ISO or `YYYYMMDD_HHMMSS` timestamps, and the trailing-whitespace and blank-line differences. It does **not** touch counts (`7 passed` and `8 passed` stay different) or paths.
- **Evidence that the header is not an input (checked, not assumed):** a real turn through the engine hands `observe_tool_result` the bare stdout, `'collected 12 items\n7 passed, 5 xfailed in 0.04s\n'`. The `# cmd / # exit / duration_ms / ts` lines in the REPL are added later by the renderer. (Verified on the PTY tier; the other tiers go through the same `_tool_result_output` but were not run.)
- **"Nothing changed" = the mutation epoch is the same.** Any file mutation the ledger already recognises (`write_file`, `edit_file`, a shell write) increments an epoch. The key of a repeat is `(epoch, digest)`. Edit, then re-run the same test: a new epoch, never a repeat.
- Per turn only (`State` is per turn), like every other counter.

## 3. Thresholds and exemptions

| Tool class | Nudge on occurrence | Why |
|---|---|---|
| shell and test tools (`run_bash`, `exec_sandbox`, `run_tests`) | 2nd identical output | a second identical verification run with no edit between is almost never new information |
| every other tool (`read_file`, `grep`, `glob`, ...) | 3rd | re-reading is common and cheap |
| polling tools (`subagent_wait`, `subagent_list`, `subagent_result`, `subagent_send`, `subagent_cancel`) | never | the same "still running" answer is correct there |

One nudge per `(epoch, digest)`; a further identical result after the nudge **escalates** one recovery rung (`LOCAL_REPLAN`, the same rung R2 uses). Past that the rule returns CONTINUE (RC5: a turn can always end).

The note is generic and never quotes the model: *"Harness note: this returned the same output as before and nothing has changed since. Use what you already have to answer, or take a different action."* It does not say anything about pytest; a project's own config is not the harness's to second-guess.

## 4. Phases

| Phase | Deliverable | Status |
|---|---|---|
| P0 | persona `RepeatsTheSameOutput` and two controls (`EditsThenRerunsTheSame`, `PollsAStableStatus`), baseline row | **not built**: an earlier version of this table said it was; it is not in `tests/reasoning/personas.py`. The cases are covered by `tests/reasoning/test_r5.py` instead |
| P1 | digest + normaliser (pure), `State` counters, `decide_stale` (R5), the runtime hook on the **success** branch of `observe_tool_result`, `R5` accepted in the per-rule setting | built |
| P2 | delivery: `observe_tool_result` holds the R5 decision for the seam `tool_result`; after a round's results the engine calls `reasoning.take_enforced("tool_result")` once (`core/stateless.py`, one call site, pinned by an AST test) and, if the rule is enforced, appends `nudge_message(note)`. One note per round; an escalation is not replaced by a later nudge | built |
| P3 | enforce R5 **by default**, on evidence from the journal of real sessions | not decided: the owner's call |

R5 is **not** in `DEFAULT_ENFORCED_RULES` (`R1=enforce,R4=enforce`), so with nothing set it observes: the decision is journaled and the model is told nothing (witnessed through a real turn). To enforce it now, name it explicitly; an explicit setting replaces the default, so list the others too:

```
WISP_REASONING_CORE_RULES=R1=enforce,R4=enforce,R5=enforce     # WISP_REASONING_JOURNAL=<file> records every decision
```

`WISP_REASONING_CORE=observe` is still the kill switch for everything. **R2 and R3 are still decision-only**: P2 holds and delivers R5's note only, because delivering R2's escalation means acting on a recovery rung, which is a larger change than a note.

## 5. Risks and what this does not do

- **False positive:** a legitimate repeat (a flaky check re-run on purpose, polling a file an external process will change). Mitigations: the epoch, the exemptions, the per-class threshold, one nudge. Not measured on real sessions; that is what observe is for.
- **Miss:** a loop whose output contains something that varies and is not normalised (a random id, a counter). Accepted: the rule is a floor, not a wall.
- **It cannot fix the project's configuration.** In the real session the cause was `-q` in `addopts`; that is the project's setting.
- **Journal volume:** one row per decision, only when a decision is not CONTINUE.

## 6. What is not verified

How often real sessions produce a no-progress repeat (no corpus; the stores hold sessions but reading them needs the owner's consent); the digest on the Docker and host tiers (same code path, not run); whether `run_tests` output has volatile fields beyond durations. The delivery was driven through the real engine with a scripted provider (`tests/reasoning/test_seam.py::TestR5ThroughARealTurn`), not through a real model, and not through the REPL.
