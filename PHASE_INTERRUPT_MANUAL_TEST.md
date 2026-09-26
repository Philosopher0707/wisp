# PHASE_INTERRUPT_MANUAL_TEST.md — Ctrl+C driven for real, and what it turned up

> Generated 2026-09-27 at `0a9f0ad`. **Manual test, not a read.** Two runs against a live
> `wisp repl` process with real `SIGINT`. One branch verified; the other **could not be reached**, and
> the reason is a defect larger than the one I was testing for.

---

## §1 — Idle SIGINT: VERIFIED

A `wisp repl` process booted to the prompt, then sent `SIGINT` with no turn running.

```
exit code: 0
bye  Exiting. Session saved.
   Resume: wisp repl -S 6ffcf3e0-3be6-4e28-8c9c-a288f73a3d13
```

**Correct.** The idle branch (`entry.py:283-320`) raises `KeyboardInterrupt`, which exits cleanly, saves
the session, and prints a resume hint — matching the banner's own promise, *"Ctrl+C exit"*. No traceback,
no orphaned tasks, exit code 0.

## §2 — SIGINT during a turn: NOT REACHED

Two attempts were made. Both **killed the process on the first press**, which is the *idle* behaviour —
so on the face of it, the turn-cancel branch failed.

**It did not. The turn was already over.** Captured output from the second run:

```
error[E1102]: Incomplete provider round (API error 402: {"error":{"message":
  "Prompt tokens limit exceeded: 17706 > 8517. To increase, visit
   https://openrouter.ai/workspaces/default/keys/…"}}) — no usable output was produced
  → round: incomplete
  Turn 1 · 0 tools · 0 files · 1.2s · ctx 12 (0%)
```

**The turn failed in 1.2 seconds**, before the model produced anything. By the time `SIGINT` was sent the
REPL was idle, and exiting cleanly was **correct behaviour**, not a bug.

**So the turn-cancel branch remains untested — and I am not going to claim otherwise.** What blocks the
test is §3.

## §3 — ⚠️ CORRECTED — see `PHASE_OPENROUTER_CONFIG_CORRECTION.md`

> **The claim below was too strong and is retracted.** The same configuration completes turns in both
> headless and REPL mode (`ok: true`, `Turn 1 · 1 tools · 5.3s`). The variable not isolated was this
> sandbox's HTTP proxy. The budget arithmetic is real; "every turn fails" is not. Kept unedited below
> so the reasoning that produced it is still auditable.

## §3 — (as written) THE FINDING: the REPL cannot complete a turn on this configuration

`Prompt tokens limit exceeded: 17706 > 8517`. Measured, with `tiktoken` over the real schema list:

| | |
|---|---|
| `TOOL_SCHEMAS` — 42 tools, serialised | 28,057 chars = **6,922 tokens** |
| the key's allowed budget | **8,517 tokens** |
| **the schemas' share of that budget** | **81.3 %** |

**The tool schemas alone consume 81% of the budget before the system prompt is written.** That leaves
~1,595 tokens for the system prompt, the workspace context, and the conversation — and the rejected
prompt was **17,706 tokens**, more than double the limit. The turn stats say `ctx 12 (0%)`, so the
*conversation* was 12 tokens: **essentially the whole prompt is fixed per-turn overhead.**

Largest single schemas:

| tool | tokens |
|---|---|
| `fanout` | 552 |
| `spawn` | 421 |
| `spawn_background` | 362 |
| `orchestrate_dag` | 301 |
| `edit_file_multi` | 218 |

This is **the F39 shape on a second provider**. F39 — *"The Ollama client sends `num_predict` without
negotiating the model's real limit, so a class of models fails outright"* — was closed by ADR-0038 for the
**Ollama** client. The OpenRouter path sends a prompt more than twice its budget and surfaces the
provider's 402, with no attempt to fit and no local diagnosis.

**Consequence:** on this configuration **every turn fails**, which is why the sessions in this thread
errored rather than answered. It also means the interruption audit's untested branch cannot be tested
until this is addressed — there is never a live turn to interrupt.

## §4 — What I did not do

- **I did not change the schema list, the budget logic, or the provider.** Fitting a prompt to a budget
  is a design decision — trim descriptions, advertise fewer tools (the mode filter already exists and
  drops only 7), or negotiate and fail with a *local* diagnosis. Choosing among those is not a fix, it is
  a decision, and it belongs in an ADR.
- **I did not claim the turn-cancel branch works.** §2 is the reason.
- **I did not treat the first run's clean exit as a bug.** It looked like one; the captured output says
  otherwise. Recorded because the same wrong conclusion was available and attractive.

## §5 — What is now known about interruption

| branch | status |
|---|---|
| idle at prompt → exit, session saved | **verified manually** |
| turn running → cancel, REPL survives | **not reached** — every turn dies in ~1.2 s (§3) |
| second press → force quit | not reached |
| SIGINT during teardown/save | not reached; both windows bounded and restored in `finally` (read) |
| tracked tests | 21 passing (`test_teardown_interruption.py`, `test_terminal_guard.py`) |
