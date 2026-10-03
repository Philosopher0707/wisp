# Craft Gate — forging tier-3 engineering standards into the turn loop

Date: 2026-10-03. Status: approved design (operator), pre-implementation.

## Problem

The harness enforces safety properties (approvals, secrets, sandbox) and one
correctness ritual (an exit-0 verify run after mutation) at runtime. Everything
else — tests ship with code, no placeholders, lint/type clean, layering
discipline — is prompt text the model may silently ignore. CI catches some of
it on push, but the turn loop, where the work happens, checks none of it.

## Decision

One composite `CraftGate` (`wisp/core/craft.py`), consulted once in the
existing pre-`done` chain in `stateless.py`, **after** `guard.rejection()`
returns None. The verification floor keeps its exact behavior and order; a
floor-blocked turn is never double-nudged. Rejected alternatives: five
sequenced gates (authority sprawl — the class ADR-0060 killed) and folding
checks into `VerificationFloorGuard` (corrupts its single job and its frozen
corpus fingerprints, a RED-first violation).

## The five checks

All are pure functions of `(workspace, changed_files)` — testable without a
turn. Ordered cheapest-first inside the gate; the nudge names every failing
check with its evidence.

1. **Placeholders** — regex over added lines:
   `TODO|FIXME|XXX|\.\.\.|pass\s*#|NotImplemented(?!Error\()`. Zero tolerance.
   Runs first, instant verdict.
2. **Changed-line coverage** — `pytest --cov` over changed files; every added
   executable line must execute. Comment/doc-only changes are vacuously
   satisfied (no executable lines). This check *is* the coverage floor — no
   separate percentage, which begs gaming.
3. **Lint/type** — `ruff check` on changed files + `mypy` limited to the
   already-typed spine when spine files are touched. Read-only, bounded 120 s.
4. **Layering** — AST import-direction check against a small table (live path
   never imports disowned layers) + new files land in the owning layer's
   directory per the `AGENTS.md` module map.

## Budget and surrender

Own `max_nudges = 2`, same delay-not-veto semantics as the floor
(ADR-0036). Exhaustion surrenders honestly as `craft-unverified`, journalled
like the floor's surrender — never a silent pass, never a deadlock.
Worst-case added latency per turn: 2 extra model loops + check runtime
(~10–60 s, dominated by coverage).

## Flags and rollout

`WISP_CRAFT_GATE`, default **off** (ADR-0002: one flag per concern; the turn
loop must not change behavior for anyone who didn't opt in). No per-check
kill-switches — that recreates five gates through the back door. Default-on
is a separate decision, taken only after measured green/false-positive rates
on live models.

## Verification (of the gate itself)

- RED-first corpus additions to `tests/test_gate_order_corpus.py`: one
  structured outcome per check, written green against the unmodified
  implementation first.
- AST tripwires: the turn loop consults exactly one craft authority; the
  floor guard's behavior and order byte-identical.
- Reachability test: a production entry point (`AgentRuntime.run_turn`)
  with the flag on produces the journalled craft record — no unreachable
  subsystem (the Phase-0 audit lesson).
- Live-model measurement of false-positive rate before any default-on talk.

## Open items (deliberately deferred)

- Coverage tooling choice (`pytest-cov` availability in minimal images —
  the doctor's "runner resolvable on PATH" lesson applies).
- Whether `craft-unverified` surrenders should nudge the model to say
  UNVERIFIED explicitly (mirrors the floor's item-5 prompt rule).
- Interaction with `acceptance_gate`/`stagnation_gate` when several are on:
  order is floor → craft → stagnation → declared-criteria; the
  never-double-nudge invariant covers the chain.
