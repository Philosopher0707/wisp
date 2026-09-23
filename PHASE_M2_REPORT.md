# PHASE M2 REPORT — Journal-First Reconstruction with Blob Fallback

| Field | Value |
|---|---|
| Item | **M2** — P1's first deferred prerequisite, and one half of the M9/M2 pair that unblocks M11–M15 |
| Baseline | P9 complete (the plan is fully traversed) |
| Status | **`COMPLETE`** — the reconstruction is built and tested; consumer adoption is per-surface (§7) |
| Files changed | 1 modified (`core/session_repo.py` — two added methods) |
| Tests added | `tests/test_session_reconstruction.py` (19) |
| Rollback | none needed — the methods are additive; no consumer calls them yet |

---

## 1. Executive summary

P1 asked to *"replace the whole-session snapshot as the only durable record with an append-only
journal; the snapshot becomes a materialized view."* P1 deliberately deferred it, and recorded why:

> `UnifiedStore.load_session` (the blob) is read by five production consumers — `__main__.py`,
> `supervisor.py`, `sdk.py`, `acp_session.py`, `server/routes/sessions.py` — while
> `SessionRepository.load_session` (replay) is a different function with a different shape. … it carries
> a real hazard: **pre-P0 sessions have no turn body in the log**, so a naive switch would make old
> sessions reconstruct *worse* than the blob does.
>
> The safe shape is **journal-first with blob fallback**, plus a migration check.

**That is what this is.** `SessionRepository.reconstruct()` and `reconstruction_source()` implement
exactly that shape, and the hazard is the first thing the tests exercise.

---

## 2. The hazard, and the predicate that avoids it

A pre-P0 session's log holds a user message and a `DONE` marker — **and no turn body**. Replaying it
yields `[{"role": "user", ...}]`, which is a *non-empty* message list.

So the obvious check — `if replayed.messages:` — **picks the journal and returns a session truncated to
one message.** That is the precise failure the P1 report predicted.

**The first implementation made that mistake**, and its own pre-P0 test caught it. The correct predicate
is whether a **turn body** was journaled:

```python
any(str(m.get("role")) != "user" for m in replayed.messages)
```

A P0+ turn always produces at least one assistant message; a pre-P0 session never does. That single
predicate is the difference between the migration working and silently destroying history — and it is
now stated in the code with the fact that it was arrived at the hard way.

---

## 3. Implementation

| Method | Behaviour |
|---|---|
| `reconstruction_source(session_id)` | `"journal"` \| `"blob"` \| `"none"` — **the migration check** |
| `reconstruct(session_id)` | journal-first, blob fallback; returns a dict shaped like `UnifiedStore.load_session` |

**Why `SessionRepository` is the right home:** it already holds `self._store` (the blob) *and* owns the
journal, so it is the only object with both sources in hand. Adoption becomes a one-line change per
consumer rather than a rewrite.

**Journal-first is worth doing because the journal carries more.** A `Session` replayed from the log has
everything the blob does — `model`, `workspace`, `messages`, `compaction_history`, `created_at`,
`updated_at` — **plus** the audit records the blob never had: proposals, outcomes, verdicts, the task
graph, node transitions, recovery decisions and escalations.

**One blob-only field remains:** `title`. The journal never carried it, so it is taken from the blob and
`test_the_title_survives_from_the_blob` pins that.

**Shape compatibility is explicit.** `BLOB_KEYS` is asserted as a subset of the result on both paths, so
a consumer can switch by replacing `store.load_session(sid)` with `repo.reconstruct(sid)`. The added
`_source` key is additive and is what lets a caller — or a test — see which path answered.

---

## 4. Verification

### 4.1 New tests — 19, all passing

| Class | Proves |
|---|---|
| `TestThePreP0Hazard` (4) | a pre-P0 session is **not truncated**; the source check prefers the blob; a journal with only a user message is not enough; a pre-P0 session with no blob is `None` |
| `TestJournalFirst` (4) | a journaled session reconstructs from the journal and reports it; **the journal carries the audit records the blob never had**; an unknown session is `None` |
| `TestShapeCompatibility` (6) | both paths satisfy `BLOB_KEYS`; `title` survives from the blob; the result is JSON-serializable; `_source` is additive |
| `TestEndToEnd` (1) | a **real turn** produces a session that `reconstruction_source` reports as `journal` and `reconstruct` rebuilds with both roles |
| `TestReachabilityAndHonesty` (4) | the methods are on the repository; it holds both sources; **the five consumers still read the blob** (a tripwire, §7); the fallback is documented in the code |

### 4.2 Regression

This phase modified a production file (`core/session_repo.py`) — but only by **adding two methods**, so
nothing existing calls them. Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New vs the stable baseline | **none** (`comm -13` empty) |
| Absent vs the stable baseline | **none** (`comm -23` empty) |

The added methods have **no caller**, so they cannot change any existing test's behaviour by
construction. The byte-identical set is confirmation, not the argument — the argument is that the change
is additive.

`ruff` / `mypy`: not installed.

---

## 5. Honest limits

- **No consumer has been migrated.** The five surfaces still read the blob. `reconstruct()` is a
  drop-in replacement, but the switch itself is per-surface and has not been made (§7).
- **The blob is still written.** Nothing stops writing it, and nothing should until every consumer
  reads the journal — otherwise a consumer that falls back would find a stale blob.
- **`title` is still blob-only.** Making the journal the sole record needs a `title` source or a
  decision to drop the field.
- **`ruff`/`mypy` not installed.**

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| Journal-first reconstruction exists and is tested | ✅ 19 tests |
| The pre-P0 hazard is handled **and pinned** | ✅ `TestThePreP0Hazard` |
| The migration check exists | ✅ `reconstruction_source()` |
| Shape-compatible with the blob | ✅ `BLOB_KEYS` asserted on both paths |
| Consumers migrated | ❌ **not done** — §7 |
| No regression | ✅ additive; see §4.3 |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. The consumers are not migrated, and that is asserted

The five surfaces named by the P1 report — `__main__.py`, `supervisor.py`, `sdk.py`, `acp_session.py`,
`server/routes/sessions.py` — still call `load_session` on the blob.

`test_the_five_consumers_still_read_the_blob` is a **tripwire**: it counts the un-migrated consumers and
fails when one is migrated, with a message pointing at this report. That is the same pattern used for
M15 in P9, and for the same reason — a gap that is documented *and asserted* is a gap someone closes.

**Why not migrate them here.** Each is a different surface (CLI, supervisor, SDK, ACP, HTTP), and the
switch has a real precondition: until *all* consumers read the journal, a partially-migrated system can
read a **stale blob** for a session whose journal is authoritative. Doing one consumer at a time is
therefore not obviously safe, and doing all five at once is a cross-cutting change across five surfaces
with no shared test harness. It deserves its own pass.

---

## 8. Where this leaves the migration

M2 was one half of the pair the P9 report named as the migration's single remaining prerequisite. With
it done, the remainder is:

| Item | Status |
|---|---|
| **M2** — journal-first reconstruction | ✅ **complete** (consumer adoption outstanding) |
| **M9** — the message list as a projection of the graph | open — the other half |
| M11–M15 | open — all blocked on M9 |

**M9 is now the sole keystone.** M11 (graph drives execution), M12 (ladder consulted), M13 (detector
constructed), M14 (context tagged) and M15 (spawn site wired) are five instances of the same change —
"make the live turn loop use the mechanism" — and M9 is what makes that change safe to make once rather
than five times.

Still outstanding and unrelated to the keystone: M1 (P3 stage 3b, blocked on a working tool path), M3
(killpoint integration), M4 (ADR-0004 for the durable records), M8 (`dag.py` retirement, needs a green
fanout suite).
