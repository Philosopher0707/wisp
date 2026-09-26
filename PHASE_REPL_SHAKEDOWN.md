# PHASE_REPL_SHAKEDOWN.md — the REPL, driven hard, with a working model

> Generated 2026-09-27. **Everything below was executed**, against `stealth/space-bunny-alpha`
> (OpenRouter), with this sandbox's `HTTP(S)_PROXY` removed from wisp's environment — see
> `PHASE_OPENROUTER_CONFIG_CORRECTION.md`. Workspace: `/tmp/grill`, seven `.py` files, one deliberately
> broken.

---

## §1 — The interruption branch that could not be tested before: **VERIFIED**

The earlier audit could not reach it, because every turn died before a signal could land. Now a turn
runs, so it can be interrupted mid-flight. The turn was confirmed **running** (a live spinner with
`read_files_batch` in flight) before the signal:

```
| read_files_batch paths=['pkg/bad.py', 'pkg/mod1.py', 'pkg/mo...

Interrupted — cancelling turn… (Ctrl+C again to force quit)

||  Turn interrupted. Session saved.
   Resume: wisp repl -S cc5b9313-226d-4289-886a-81b07f3c9dc1
```

| assertion | result |
|---|---|
| survived the first press (did **not** quit) | **True** |
| printed the cancel notice | **True** |
| session saved, with a resume id | **True** |
| clean exit afterwards | exit code **0** |

**Both branches of the SIGINT contract are now verified by execution**, not by reading: idle → clean exit
(`PHASE_INTERRUPT_MANUAL_TEST.md` §1); turn running → **cancel and survive** (§1 here).

## §2 — The coding loop: works, and in a sensible order

Headless, one task — *"`pkg/bad.py` has a syntax error. Fix it, then verify the file compiles."*

```
ok        : True
errors    : []
tool calls: read_file → read_files_batch → run_bash → list_files
            → edit_file → run_bash → lsp_diagnostics → run_bash
```

Eight calls, and the shape is right:

- **look before touching** — `read_file`, then `read_files_batch` for sibling context;
- **look for a suite** — `list_files` (it reported *"No test suite or config here"*, correctly);
- **one edit**, then **three verification steps** — `run_bash`, `lsp_diagnostics`, `run_bash`.

That last group is `verification_loop` (ON by default) doing its job: the edit is not the end of the turn.

**Outcome, checked independently:**

```
def broken(a, b):
    return a + b          # was: return a +
```

compiles; all 7 files parse; **only `bad.py` was modified** — the other six untouched.

## §3 — Slash commands: work, with a clean unknown-command path

| command | result |
|---|---|
| `/help` | lists the commands (`/doctor`, `/exit`, …) |
| `/doctor` | responds |
| `/model` | responds |
| `/clear` | responds |
| `/nosuchcommand` | **"Unknown command: /nosuchcommand. Type /help for available commands."** |

The unknown-command message names the remedy — the same standard the AUTO_EDIT denial was fixed to meet.

## §4 — What the shakedown does not cover

- **One model.** `stealth/space-bunny-alpha` only. Nothing here says anything about another model.
- **A two-file task and a seven-file workspace.** No large-repo behaviour, no long session, no
  compaction.
- **No `GAMED` result.** `scripts/wisp_coding_benchmark.py`'s hardcode trap is still unexercised — the
  right next run, now that a model answers.
- **The proxy caveat.** Every run here needed `HTTP(S)_PROXY` removed from wisp's environment. With them
  set, requests return `502 upstream connect failed`. That is this sandbox, not wisp — but it means
  **anyone reproducing this must reproduce that step too**, and a reader who does not will see a 502 and
  conclude the REPL is broken.
