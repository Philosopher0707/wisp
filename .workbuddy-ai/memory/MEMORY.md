# Wisp — durable project notes

Long-lived facts for this repo. **Not project source** — nothing here is imported by `wisp/`.
Daily logs are `YYYY-MM-DD.md`; the compaction handoff is `../../CONTEXT.md`.

---

## The one thing to read first

`CONTEXT.md` §0. It carries the current status, the commits, the open items and the environment gotchas.
Everything below is the short version.

**State at the last compaction:** `HEAD` = the M16 commit, **17 commits** on top of the Phase 10
baseline `83b10af`. The Persistent Graph Loop migration (P0–P9) is **fully traversed**; M2, M3, M4 and
M16 are **complete**. **557 migration tests pass. The full-suite failure set is 129, byte-identical to
the stable baseline.**

---

## What is actually delivered, and what is not

**Eight mechanisms** built, tested, and reachable from their packages:

`core/proposal.py` · `core/acceptance.py` · `core/task_graph.py` · `core/recovery.py` ·
`core/stagnation.py` · `core/context_trust.py` · `auth/principal.child_principal()` ·
`SessionRepository.reconstruct()`

**None is driven by the live turn loop.** That is not the same thing as a working Persistent Graph Loop,
and the reports say so rather than implying otherwise.

**The durability track is closed** (M2/M3/M4/M16) — and M4 and M16 each **found a live defect while
closing it**, which is the argument for revisiting decisions rather than restating them.

**The sole remaining keystone is M9** — *the message list as a projection of the graph*. Five items
(M11–M15) are the same change ("make the live turn loop use the mechanism") and all are blocked on it.
M9 was deliberately not attempted: it inverts the dependency between the messages and the graph, and a
half-finished inversion is worse than none.

---

## The discipline this repo demands

These are not preferences. Each was learned by getting it wrong.

1. **`env -u PYTHONPATH` on every command.** The WorkBuddy `sitecustomize.py` shim blocks pytest's temp
   `mkdir` → false failures.
2. **A single-run failure count is not a baseline.** Two identical runs read 129 and 130. Compare against
   `.workbuddy-ai/memory/baseline-failures-stable.txt` (the **intersection of two runs**) and require
   **both** `comm` directions to be empty.
3. **Never `git stash` to get a baseline.** The tree carries pre-existing uncommitted work in the same
   files; stashing reverts them to HEAD and discards it. Snapshot outside the repo instead.
4. **Write the safety net RED-first** before touching a gate. `tests/test_gate_order_corpus.py` was
   written and made green against the **unmodified** implementation.
5. **Reachability is mandatory.** The Phase 0 audit found **eight** complete, tested, unreachable
   subsystems. Every new path needs a test that drives a **production entry point**.
6. **A gap that is documented should also be asserted.** Unwired items carry **tripwire tests** that fail
   the moment someone wires them (M15, M2's five consumers).
7. **Use the Grep tool, not the shell.** BSD `grep --include` silently matches nothing here, and plain
   `grep -n "a\|b" file` returned empty with exit 1 for a pattern that plainly exists.
8. **Report the set, never "the suite passes".** 129 failures are pre-existing and environmental.
9. **A fallback must not discard what it can still read.** M4 chose the blob for the *transcript*; M16
   found that choice was also throwing away the *records*, which are independent of it. When you pick a
   source, say which question you are answering.

---

## The environment (nothing here is installable)

| Missing | Consequence |
|---|---|
| **`jsonschema`** | **No tool executes at all.** `_validate_tool_args` turns the `ModuleNotFoundError` into a validation-failure string the caller treats as a hard `SCHEMA_INVALID` denial. A missing dependency becomes a total tool outage, with the failure misdirected at the tool (F8). |
| `httpx` | 11 starlette `TestClient` files error. |
| `tiktoken` | P8's token budgets cannot be measured faithfully. |
| `ruff`, `mypy` | Every phase's lint/type criterion is marked **unmet**, not skipped. |
| `aiohttp`, `prompt_toolkit`, `cryptography`, `numpy` | Assorted. |

No network (SSL cert verification fails), so `pip install` cannot fix any of it.

---

## Do not touch

- **The user's pre-existing WIP.** `CONTEXT.md` §8 lists it. Notably
  `wisp/multi_agent/_circuit_breaker.py` is **untracked and theirs** — it is a duplicate of the wired
  `infra/circuit_breaker.py`, and it is **documented, not deleted** (F23).
- **`.workbuddy-ai/`** — agent workspace data. The baseline failure set lives here because `/tmp` did not
  survive a reboot and lost P0–P6's.

---

## Where the full record is

| Question | Read |
|---|---|
| Current status, commits, open items | `CONTEXT.md` §0, §3, §12 |
| Phase ledger, findings F1–F23, change log | `WISP_MIGRATION_STATUS.md` |
| Why a decision was made | `WISP_ARCHITECTURE_DECISIONS.md` (ADR-0001 … ADR-0028) |
| What a phase actually did, and its honest limits | `PHASE_<X>_REPORT.md` |
| The plan of record | `WISP_MIGRATION_PLAN.md` |
| Per-day narrative | `.workbuddy-ai/memory/YYYY-MM-DD.md` |
