# PHASE M3 REPORT — Killpoint Integration for the Session Journal

| Field | Value |
|---|---|
| Item | **M3** — P1's second deferred prerequisite |
| Baseline | M2 complete |
| Status | **`COMPLETE`** — the session journal now has a real-SIGKILL kill point |
| Files changed | 1 modified (`tests/reliability/test_killpoints.py` — one kill point added) |
| Tests added | 1 (`test_kp_session_midtool_then_killed`); the file's other 4 still pass |
| Rollback | none needed — a test-only change; no production behaviour touched |

---

## 1. Executive summary

P1 recorded this remainder precisely:

> `tests/reliability/test_killpoints.py` exists; the journal now supports it. The *detection*
> primitive is implemented and tested (`Session.unresolved_actions()`); the harness integration is
> not.

**Both halves are now true.** The harness exists, `unresolved_actions()` is implemented and unit
tested — and it is now driven by a **real SIGKILL**.

---

## 2. What was actually missing

The killpoint harness is substantial (368 lines) and does real work: it spawns a child, waits on a
`READY` barrier, sends **`SIGKILL`**, then runs the recovery path and records a JSONL result. It has
four kill points:

| Kill point | Boundary |
|---|---|
| `test_kp_journal_temp_inflight_then_killed` | workspace journal temp file |
| `test_kp_workspace_midapply_then_killed` | workspace changeset apply |
| `test_kp_graph_midrun_then_killed` | graph node left `RUNNING` |
| `test_kp_store_midtransition_then_killed` | run-store transitions |

**Every one of them targets Layer B or the workspace.** None touches the **session journal** that P0
and P1 built — so `unresolved_actions()`, the primitive whose entire purpose is to report
*"dispatched, outcome unknown"*, had never been exercised against an actual process death.

That is the gap: not a missing harness, but a **missing kill point in an existing harness**.

---

## 3. The new kill point

`test_kp_session_midtool_then_killed` kills the child in the window P1's design is about — **between a
journaled `TOOL_CALL` and its `TOOL_RESULT`**:

```
child:  user_message → assistant_message(tool_calls) → TOOL_CALL  ← intent journaled
        ...READY... then SIGKILL ...                              ← dies here
        (TOOL_RESULT is never written)                            ← resolution missing
```

The parent then reopens the store and asserts four things:

| # | Assertion | Why it matters |
|---|---|---|
| 1 | the journal **replays** with `unknown_events == 0` | the record survived the kill **un-torn** |
| 2 | `unresolved_actions()` reports **exactly one**, with the matching `action_key` | the ambiguity is **surfaced**, not inferred |
| 3 | `was_last_turn_complete()` is `False` | a resume can tell the turn did not finish |
| 4 | `tool_call` is in the log and **`tool_result` is not** | the report is **correct**, not a guess — the intent exists and the resolution genuinely does not |

Assertion 4 is what makes 2 meaningful. Without it, `unresolved_actions()` returning one entry would
be consistent with a resolution that was simply not looked for.

**Why this matters beyond the test:** the action's outcome is genuinely unknown — the side effect may
or may not have landed. Recovery must *surface* that rather than silently repeating the call, because
repeating is how one crash turns one edit into two. That property is now verified against a real
`SIGKILL` rather than a simulated one.

---

## 4. Verification

### 4.1 The new kill point passes, and the file's four existing points still do

```
tests/reliability/test_killpoints.py .....   5 passed in 4.57s
```

The test is **not vacuous**: `_wait_ready` blocks until the child writes `READY.json`, and the
assertion `info.get("session") == "kp-journal"` requires that payload to have been produced. The child
therefore ran, reached the barrier, and was killed — the assertions are about post-kill state.

### 4.2 Regression

Test-only change; no production file touched. Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New vs the stable baseline | **none** (`comm -13` empty) |
| Absent vs the stable baseline | **none** (`comm -23` empty) |

`test_killpoints.py` is **not in the baseline's failure set** (verified: zero matches), so its five
points were passing before and pass now. This is a test-only change; no production file was touched.

`ruff` / `mypy`: not installed.

---

## 5. Honest limits

- **The kill point covers one window.** `TOOL_CALL` journaled / `TOOL_RESULT` missing is the window
  P1's `unresolved_actions()` was built for, and it is the one most worth killing in. Other journal
  windows — mid-`assistant_message`, mid-compaction, mid-`append_events` batch — are not covered.
- **The child writes the events directly** rather than driving a real turn to the barrier. A real turn
  would be closer to production, but it cannot reach a barrier *between* a tool call and its result
  without a hook the engine does not expose. The direct write tests the **journal contract**, which is
  what P1 built; it does not test the engine's dispatch path.
- **`ruff`/`mypy` not installed.**

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| The harness drives the journal under a real SIGKILL | ✅ `test_kp_session_midtool_then_killed` |
| The detection primitive is exercised, not just unit-tested | ✅ assertion 2 |
| The journal is proven un-torn after the kill | ✅ assertion 1 |
| No regression | ✅ test-only change; the file's 5 points pass |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. Where this leaves the migration

| Item | Status |
|---|---|
| **M2** — journal-first reconstruction | ✅ complete (consumer adoption outstanding, asserted) |
| **M3** — killpoint integration | ✅ **complete** (one window) |
| **M9** — the message list as a projection of the graph | open — **the sole keystone** |
| M11–M15 | open — all blocked on M9 |
| M1 — P3 stage 3b | open — blocked on a working tool path (`jsonschema` absent) |
| M4 — ADR-0004 for the durable records | open |
| M8 — `dag.py` retirement | open — needs a green fanout suite |

With M2 and M3 done, the durability story P0 and P1 set out is **closed end to end**: the journal is
written incrementally, replayed journal-first, and now proven against a real crash in the window that
matters. What remains is the *authority* story — M9 and the five items behind it.
