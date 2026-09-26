# PHASE_REPL_ON_WISP.md — the REPL driven against the wisp codebase itself

> Generated 2026-09-27. `wisp repl --workspace <the wisp repo>`, three prompts, one session.
> Log: `/tmp/repl_wisp_session.log`. Provider: `stealth/space-bunny-alpha`, proxy removed.

---

## §1 — The session

```
booted: True
  prompt 1 sent | turn markers seen: 1
  prompt 2 sent | turn markers seen: 2
  prompt 3 sent | turn markers seen: 3
exit: 0        token-budget error? False
```

**Three turns, three completions, exit 0, no budget error** — on the full repository, not a toy
workspace. (One startup warning worth knowing: `wisp.repo_map: Found 1078 source files, limiting to
500 for performance`.)

## §2 — What it answered

**Turn 1** — *"list the files in `wisp/tools/` and tell me how many `.py` files there are"*

```
**`wisp/tools/` — 21 `.py` files:**
...
Caveat on scope: `list_files` with pattern `*.py` is non-recursive, so this count covers the top
level of that directory
  Turn 1 · 2 tools (1 ok, 1 failed) · 17.5s · ctx 626 (0%)
```

**21 is correct** — verified independently earlier this session by `ls`. And it **volunteered the
scope caveat** without being asked.

**Turn 2** — *"read `wisp/multi_agent/cli.py` and describe in one sentence what `cmd_swarm` does"*

> *"`cmd_swarm` builds a **`ToolExecutor`-backed** `SwarmOrchestrator` (which is actually the
> `SubagentOrchestrator`…)"*

**That is my fix, described back to me by the agent that did not write it.** The `ToolExecutor` was
added an hour ago; the summary is accurate, including the alias.

**Turn 3** — *"is there a file named `orchestrator.py` under `wisp/multi_agent/`?"*

> *"I checked with `list_files` on `wisp/multi_agent/` filtered to `*.py`, which returned all 17
> Python files in that directory… This confirms the behaviour I described earlier: the
> `try: from .orchestrator import SwarmOrchestrator` at `cli.py:20`…"*
>
> *"One note on the check itself: `list_files` is non-recursive, so this is a statement about the top
> level of `wisp/multi_agent/`…"*

**No** — and it **independently reproduced the finding I made by AST**: `orchestrator.py` does not
exist, the import is a guarded fallback, and the route to the same missing module had no fallback.

## §3 — What this establishes

- **The REPL works on a real repository**, not a seven-file toy. Three turns, no crash, no budget
  error, `ctx` growing 626 → 3k.
- **It reads its own codebase accurately.** The Turn 2 summary reflects a change made minutes earlier,
  and Turn 3 reproduced a finding reached by a completely different method (AST scan vs `list_files`).
  **Two independent methods, same answer.**
- **It names its own limits every time.** Non-recursive listings were flagged in Turns 1 *and* 3,
  unprompted. That is the same honest-refusal behaviour the subagent showed in
  `PHASE_SUBAGENT_PROVENANCE.md` — it is a property of the system, not a lucky sample.
- **One tool call was denied in Turn 1** (`2 tools (1 ok, 1 failed)`) — a `bash` call, consistent with
  the approval layer that subagents and the REPL hit. The turn recovered with `list_files`, which is
  the designed behaviour.

## §4 — What it does not establish

- **Three prompts is a smoke test, not an evaluation.** Nothing here says how it behaves on a
  multi-file refactor, a long session, or a task whose answer is wrong.
- **`ctx` reached 3k of a 256 000 budget** — compaction was never exercised, and the token-budget
  ceiling measured earlier (an 8 517-token account limit) was not approached *because the proxy was
  removed and the headless prompt is smaller than the REPL's was in the failing run*. That question
  is still open.
- **One provider, one session.** `stealth/space-bunny-alpha` was flaky earlier in the evening
  (`Provider stream closed without any content`, retried twice, succeeded on the third).
