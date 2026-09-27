# PHASE_INTERRUPT_HANDLING_AUDIT.md — Ctrl+C in the REPL, and the 51 tests that cannot run

> Generated 2026-09-27 at `0e7bb4e`. **The live design is correct and well-wired. The test suite for
> its previous design is dark, and has been.**

---

## §1 — The stated policy

`wisp/cli/repl.py:20-22`:

> *"Signal policy: SIGINT cancels the live turn (first press), restores the default disposition so a
> second press force-quits; SIGWINCH refreshes the cached terminal width."*

Two branches, in `make_repl_sigint_handler` (`wisp/entry.py:283-320`):

| state | behaviour |
|---|---|
| **turn running** | `task.cancel()`, print *"Interrupted — cancelling turn… (Ctrl+C again to force quit)"*, then **restore the default handler** so a second press hard-quits |
| **idle at prompt** | `raise KeyboardInterrupt` — prompt_toolkit's documented *"Ctrl+C clears input"* contract |

The docstring also records a **fixed** bug, which is the reason the design is worth stating: *"The
previous cooperative handler set a flag that nothing consumed, so the first press only printed a
message while the turn kept running."*

## §2 — The wiring, traced end to end, and it is correct

| step | site |
|---|---|
| handler **installed** once, after the closure exists | `entry.py:611-613` |
| turn task **set** | `entry.py:559` — `_current_turn_task = loop.create_task(coro)` |
| handler **reads it live** | `entry.py:528` — `lambda: _current_turn_task` |
| **`CancelledError` caught** | `entry.py:575`, comment: *"Task was cancelled (likely Ctrl+C or approval [c]ancel)"* |
| **`KeyboardInterrupt` caught** | `entry.py:568` |
| task **cleared** | `entry.py:593` — **in `finally`**, so no exit path leaves it stale |
| handler **re-armed** | `entry.py:598` — **in `finally`** |

**The re-arm is the subtle one and it is handled.** The handler de-arms itself on the first press
(restoring the default so a second press force-quits) — so without a re-arm, the *next* turn's Ctrl+C
would hard-quit the process instead of cancelling the turn. `598` re-arms it on every turn, in `finally`.

**Two windows deliberately ignore SIGINT, and both are sound:**

- `entry.py:371-378` — during loop teardown. Bounded (`_drain_pending_tasks(..., timeout=3.0)`),
  restored in `finally`.
- `entry.py:739-753` — during session save. Bounded (*"<3.5s"*), restored in `finally`, and carries its
  rationale: *"a Ctrl+C landing mid-cleanup used to kill the save, leave asyncio tasks half-reaped, and
  spray 'Task exception was never retrieved' tracebacks after the goodbye message."* Another **fixed** bug,
  recorded where the fix is.

**Verified by test:** `tests/test_teardown_interruption.py` + `tests/test_terminal_guard.py` —
**21 tests, all passing.**

## §3 — THE FINDING: a 51-test interruption suite that cannot collect

`tests/test_input_and_interrupts.py` is **untracked** (the user's WIP, `CONTEXT.md` §8) and **aborts
collection**, which also aborts collection for the whole tree:

```
tests/test_input_and_interrupts.py:18: in <module>
    from wisp.transport.cli import (
E   ImportError: cannot import name '_has_unclosed_brackets' from 'wisp.transport.cli'
```

**51 `def test_` functions. None of them run.**

It imports **12 names** from `wisp/transport/cli.py`. Measured: **6 survive, 6 are gone.**

| present | gone |
|---|---|
| `_input_line` | `_has_unclosed_brackets` |
| `_handle_sigint` | `_continuation_prompt` |
| `_install_signal_handler` | `_interrupt_count` |
| `_restore_signal_handler` | `_turn_in_progress` |
| `_transport_instances` | `_last_prompt_interrupt_time` |
| `CLITransport` | `_MAX_INPUT_CHARS` |

**The three handler-state names are the diagnosis.** `_interrupt_count`, `_turn_in_progress` and
`_last_prompt_interrupt_time` are exactly the *cooperative handler's* state — the design §1's docstring
says was **replaced** because *"the first press only printed a message while the turn kept running"*.
`_has_unclosed_brackets` is not present anywhere in `wisp/` or `tests/`.

**So this is not a stale import — it is a test suite for the previous interruption design.** Its subject
moved; the suite did not, and because it was never tracked, nothing noticed.

## §4 — What is covered, and what is not

**Covered:** the live design's teardown and terminal-guard behaviour (21 passing tests), and the wiring
traced in §2.

**Not covered, and worth saying plainly:** the **first-press cancel** path itself — *"turn running →
`task.cancel()`"* — has no test in the tracked tree. The suite that would have covered it is §3's, and
it is dark. The `entry.py:283-320` handler's two branches are the interruption contract, and only the
teardown half is exercised.

## §5 — What I did not do

- **I did not touch `tests/test_input_and_interrupts.py`.** It is the user's untracked WIP and
  `CONTEXT.md` §8 says *"do NOT commit or delete"*. Its repair is a decision about whether those 51
  tests are **stale** (delete or rewrite against the new surface) or **mid-work** (finish the rewrite).
  That call is the author's.
- **I did not rewrite the handler.** It is correct; §2 is the evidence.
- **I did not add a first-press test.** It would be the right next step, but it needs the harness
  `test_input_and_interrupts.py` was building — signals, a live turn, a fake terminal — and guessing at
  that harness is how a test becomes a liability. Named, not attempted.
