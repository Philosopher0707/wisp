# PHASE M4 REPORT — Durability as a Correctness Precondition

| Field | Value |
|---|---|
| Item | **M4** — revisit ADR-0004 for the durable records |
| Baseline | M3 complete |
| Status | **`COMPLETE`** — and it closed a real hole M2 had opened |
| Files changed | 2 modified (`core/session.py`, `core/session_repo.py`) |
| Tests added | `tests/test_durability_preconditions.py` (24) |
| Decision | **ADR-0027** — supersedes ADR-0004's reversal condition |

---

## 1. Executive summary

ADR-0004 declared every durable write best-effort, and stated its own reversal condition:

> *"Once a phase requires durable state as a **correctness** precondition (P2's proposal boundary is the
> likely candidate), silent best-effort is no longer acceptable for that path and must become fail-loud.
> **Revisit at P2.**"*

P2 landed, and so did P3–P6. But **the decisive change was M2** — and it was not the one ADR-0004
anticipated. M2 promoted the journal from *secondary* to *primary* by making reconstruction
journal-first. That is what turns a permitted silent write failure from a lost observation into a
**silently truncated session**.

**Revisiting the ADR surfaced a live defect.** With a `TOOL_RESULT` write lost:

| Observation | Before M4 |
|---|---|
| replayed messages | `["user", "assistant", "assistant"]` — an assistant `tool_calls` block with **no tool reply** |
| `unknown_events` | **0** — it counts unrecognised event *kinds*, not missing ones |
| `reconstruction_source()` | **`"journal"`** — journal-first returns the broken transcript, authoritatively |

A strict provider rejects that message shape. So the failure mode M2 was designed to avoid — returning a
*worse* session than the blob — arrived by a **second route**, and nothing reported it.

---

## 2. The decision (ADR-0027)

**Do not make the writes fail-loud.** ADR-0004's core concern stands: a turn must not die because a disk
write failed, and making the journal fatal would convert an observability feature into an outage — the
exact outcome ADR-0004 was written to prevent.

Instead, make the **invariant checkable**:

1. **`Session.gap_detected`** — the journal's sequence is contiguous (`_journal_turn_events` stamps in
   order with no holes), so a hole means a write was lost. Contiguity is measured from the minimum
   present, so applying a single event is not a gap.
2. **`reconstruction_source()` refuses a gapped journal** and falls back to the blob.
3. **`reconstruct()` reports `_gap`** on the result.

**Why this is the right shape.** ADR-0004's own precedent is `persist_skipped_total` — a *canary*, not a
crash. The problem was never that a write could fail; it was that **nothing downstream could tell**.
`gap_detected` is that signal, and unlike a counter it is a **property of the record itself**, so it
travels with the data and cannot be forgotten by a caller that never read the counter.

### 2.1 The classification, for the record

| Record | Loss costs | Policy |
|---|---|---|
| Turn body (`ASSISTANT_MESSAGE` / `TOOL_CALL` / `TOOL_RESULT`) | the session — it is now the primary record | best-effort **write**, gap-checked **read** |
| `PROPOSAL` / `OUTCOME` (P2) | the authorization audit — compliance, not correctness | best-effort, canary |
| `VERDICT` (P3) | stage-3a measurement (nothing consumes it until 3b) | best-effort, canary |
| `TASK_GRAPH` / `NODE_TRANSITION` (P4) | graph↔transcript divergence | best-effort, canary |
| `RECOVERY` / `ESCALATION` (P6) | resumability — the escalation *is* the state | best-effort, canary; open item **M16** |

---

## 3. Implementation

| File | Change |
|---|---|
| `wisp/core/session.py` | `_seen_sequences` field; recorded in `apply()`; cleared in `replay()`; `gap_detected` property |
| `wisp/core/session_repo.py` | `reconstruction_source()` refuses a gapped journal; `reconstruct()` reports `_gap` |

### 3.1 A bug my own test caught

`_seen_sequences` was first assigned in `replay()` only, so a **directly-constructed `Session`** raised
`AttributeError` on `gap_detected`. `test_an_empty_session_is_not_a_gap` caught it, and the field now
exists on the dataclass — with a comment saying why, because "a fresh object must answer the property
too" is the kind of thing that gets removed by someone tidying up.

---

## 4. Verification

### 4.1 New tests — 24, all passing

| Class | Proves |
|---|---|
| `TestTheGapHole` (6) | the defect itself: a gapped journal is a provider-invalid transcript; replay alone does **not** report it; `gap_detected` does; the journal is not chosen; it falls back to the blob; `_gap` is reported |
| `TestGapDetected` (9) | contiguous is not a gap; a hole is; a single event is not; a non-zero start is not; empty is not; **replay resets the state**; a long contiguous run is not; gaps at the start and end are detected |
| `TestTheInvariantHolds` (2) | **a real turn produces a gap-free journal** and still reconstructs from the journal — the check must not reject production sessions |
| `TestReachabilityAndHonesty` (3) | the property exists; the code cites ADR-0004; the source check cites the gap condition |

`TestTheInvariantHolds` is the one that makes the check safe to ship: `gap_detected` is only useful if a
real turn satisfies the contiguity invariant. If it did not, the check would reject **every** session and
M2's journal-first would be dead on arrival.

### 4.2 Regression

This phase modified two production files, both in the session path. Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New vs the stable baseline | **none** (`comm -13` empty) |
| Absent vs the stable baseline | **none** (`comm -23` empty) |

This phase modified two **production** files in the session path. The changes are additive
(`gap_detected`, `_gap`) plus one new condition in `reconstruction_source()`. The only behaviour change
is that a *gapped* journal now falls back to the blob — the fix itself, and something no existing test
exercised, because nothing in the suite produced a gapped journal.

`ruff` / `mypy`: not installed.

---

## 5. Honest limits

- **The write is still best-effort.** M4 makes loss *detectable*, not impossible. A caller that never
  consults `gap_detected` or `_gap` is no better off than before — which is why `reconstruction_source()`
  consults it rather than leaving it to the caller.
- **The `ESCALATION` record's loss is not fully addressed.** P6 made escalation *durable state*; if that
  write fails, a parked run loses the record of why it is parked. Recorded as item **M16** rather than
  left implicit — it is the one row in §2.1 where the classification is arguably wrong.
- **Contiguity assumes a single writer per session.** Two writers stamping the same session would produce
  interleaved sequences that look gapped. The session lock serializes writers today, so this holds — but
  it is an assumption, and it is now load-bearing.
- **`ruff`/`mypy` not installed.**

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| ADR-0004's reversal condition is addressed | ✅ ADR-0027, cross-referenced from ADR-0004 |
| The records are classified by what their loss costs | ✅ §2.1 |
| The hole is closed **and** pinned | ✅ `TestTheGapHole` |
| The check does not reject real sessions | ✅ `TestTheInvariantHolds` |
| No regression | ✅ see §4.3 |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. Where this leaves the migration

| Item | Status |
|---|---|
| **M2** — journal-first reconstruction | ✅ complete (consumer adoption asserted) |
| **M3** — killpoint integration | ✅ complete (one window) |
| **M4** — ADR-0004 revisited | ✅ **complete** — and it found a live defect |
| **M9** — the message list as a projection of the graph | open — **the sole keystone** |
| M11–M15 | open — blocked on M9 |
| **M16** — the `ESCALATION` record's loss | **new**, from §5 |
| M1 — P3 stage 3b | open — blocked on a working tool path |
| M8 — `dag.py` retirement | open — needs a green fanout suite |

The durability trio (M2, M3, M4) is now closed, and closing it **found a defect rather than confirming a
design**: M2 introduced journal-first, M4 discovered that journal-first could return a broken transcript
when a permitted write failed, and the fix is an invariant rather than a stricter write policy. That is
the shape of a decision worth revisiting — the ADR was right about the write and wrong about the read.
