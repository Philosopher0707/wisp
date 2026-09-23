# PHASE M16 REPORT — The Escalation Is State, Not Audit

| Field | Value |
|---|---|
| Item | **M16** — the `ESCALATION` record's durability |
| Predecessor | ADR-0027's own reversal condition (M4) |
| Status | **`COMPLETE`** |
| Decision | `WISP_ARCHITECTURE_DECISIONS.md` **ADR-0028** |
| Files changed | 3 production, 1 test file added |
| Tests added | `tests/test_escalation_durability.py` (**40**) |
| Rollback | none needed — both changes are additive to the returned shape and to the write policy |

---

## 1. What M16 was

ADR-0027 classified every durable record by what its loss costs, and left one row explicitly
unresolved:

> `RECOVERY` / `ESCALATION` (P6) — resumability, the escalation *is* the state — best-effort, canary;
> **open item M16**

So M16's stated scope was narrow: decide whether the escalation's *write* should stop being
best-effort. Revisiting it found the policy wrong in **both** directions, and the **read** side — which
nobody had questioned — was the worse of the two.

---

## 2. The read side was the real defect

M4 made `reconstruction_source()` refuse a gapped journal and fall back to the blob. That decision is
correct for what it was about: a gapped journal can replay to an assistant `tool_calls` block with no
reply, which a strict provider rejects.

But it answers **one** question — *which transcript do I trust* — and M4's implementation treated it as
if it answered a second: *which records survived*. Those are independent. The journal-only records do not
depend on the transcript's validity, so a gapped journal whose escalation survived still has that
escalation.

**Verified before fixing:**

```python
# A turn that escalated, then a lost write (seq 2 absent).
repo.append_events("s1", [
    SessionEvent.user_message(0, "go"),
    SessionEvent.assistant_message(1, "", [...tool_calls...]),
    # seq 2 LOST — a permitted best-effort failure (ADR-0004)
    SessionEvent.escalation_event(3, escalation),
])
```

| Observation | Before M16 |
|---|---|
| the escalation in the journal | **present** — `esc-2`, with its full ladder history |
| `gap_detected` | **True** — M4 detected the loss correctly |
| `reconstruction_source()` | `"blob"` |
| the escalation in the reconstructed result | **absent** — the blob has no `escalation` key |

So the fallback that was added to *stop* a worse session being returned **silently discarded the state
of a parked run**, and reported the loss only as `_gap`. A resume reading that result cannot tell why
the run stopped, or whether it should resume at all.

That is a live defect, not a hypothetical: it needs only a permitted write failure plus a gapped
journal, which is exactly the pair ADR-0004 and M4 were written about.

---

## 3. The write side

`AgentRuntime._journal_turn_events` swallowed **every** failure:

```python
except Exception:
    logger.warning("Session %s: failed to journal %d turn event(s)", ...)
```

That is ADR-0004's rule and it is right for a *record of what happened*: the loss is observable as a gap
in the sequence, and a turn that ran correctly must not be reported as failed because a disk write
failed.

It is wrong for a **state transition**. If the write that establishes a state is lost, continuing as
though it landed is not a lost observation — it is a **false record**. `HumanIntervention` is not a
description of a parked run; it *is* the parked run's state, and `resumable` reads it to decide whether
to resume.

---

## 4. The decision (ADR-0028)

Neither change makes a write fail-loud in general. **ADR-0004 stands.** Two targeted changes:

**1. The fallback salvages rather than discards.** `reconstruct()` carries the journal-only records under
`_journal` on **both** paths, and names what a gap endangers in `_journal_records_at_risk`. Which
transcript to trust and which records survived are now treated as two questions.

**2. A state-bearing batch is not swallowed.** `is_state_bearing(events)` is the single authority for
which records are special; `_journal_turn_events` re-raises when a batch contains one, and stays
best-effort otherwise.

### Why `ESCALATION` alone

`PROPOSAL`/`OUTCOME`, `VERDICT`, `TASK_GRAPH`/`NODE_TRANSITION` and `RECOVERY` are records *about* a
turn whose own behaviour is unaffected by their loss. The set is pinned by a test parametrized over
**every** other kind, so the carve-out cannot widen by accident.

`RECOVERY` rows stay best-effort specifically because the full ladder history travels *inside* the
intervention — so their loss is redundant rather than load-bearing.

### Why a raise is safe here

It is not silent, and I checked both paths rather than assuming:

| Path | What happens |
|---|---|
| inside the stream loop | the turn's own `except Exception` turns it into a recoverable **error event the transport sees** |
| the turn-end path (a `finally`) | it reaches the **caller of `run_turn`** |

And a turn is already stopping when an escalation is produced, so this cannot abort work in progress.

---

## 5. What landed

| File | Change |
|---|---|
| `wisp/core/session.py` | `STATE_BEARING_EVENT_TYPES`, `is_state_bearing()`, `JOURNAL_ONLY_RECORDS`, `JOURNAL_ONLY_SHAPES`, `empty_journal_records()`, `Session.journal_records()`, `Session.has_escalation` |
| `wisp/core/session_repo.py` | `reconstruct()` carries `_journal` on both paths and `_journal_records_at_risk` on both; the blob path salvages instead of discarding |
| `wisp/core/runtime.py` | `_journal_turn_events` re-raises for a state-bearing batch, stays best-effort otherwise |
| `tests/test_escalation_durability.py` | **new** — 40 tests |

### One authority per question

Three separate authorities, each tested:

- **Which records are special** — `STATE_BEARING_EVENT_TYPES`, and an AST test asserts `runtime.py`
  never names the event type directly. A second decision about the carve-out would be a second authority
  for it, which is the defect class this migration exists to remove.
- **Which records the blob cannot supply** — `JOURNAL_ONLY_RECORDS`, with a test that asserts the blob
  genuinely lacks every one. The salvage is worthless if the blob already carries them, and that
  assumption is now asserted rather than assumed.
- **The shape of those records** — `JOURNAL_ONLY_SHAPES` + `empty_journal_records()`, so a caller reads
  `_journal` unconditionally instead of branching on whether the journal was available.

---

## 6. The test that is the point

`test_a_surviving_escalation_is_not_discarded` reproduces the defect exactly: gapped journal, surviving
escalation, blob present. It asserts all four facts together —

- the transcript is still the **blob's** (M4's decision, unchanged)
- `_gap` is **True**
- the escalation **survives** with its `intervention_id` and `reason`
- `_journal_records_at_risk` names the full journal-only set

Before the fix the third assertion fails and the fourth key does not exist. That is the whole of M16 in
one test.

---

## 7. Regression

Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New vs the stable baseline | **none** (`comm -13` empty) |
| Absent vs the stable baseline | **none** (`comm -23` empty) |

This phase modified **three production files** in the session and runtime paths. The changes are:

- **additive to the returned shape** (`_journal`, `_journal_records_at_risk` on both paths) — every
  pre-existing key is unchanged, and `BLOB_KEYS` is still a subset;
- **additive to `Session`** (`journal_records()`, `has_escalation`);
- **one branch** in `_journal_turn_events`, reachable only when a batch contains an escalation — which
  nothing produces yet, because the ladder is not wired to the turn loop (M12).

So the byte-identical failure set is *expected*, and it confirms the expected thing rather than proving
a behavioural equivalence. The behavioural change is covered by the new suite, which drives
`reconstruct()` and `_journal_turn_events` directly.

Neighbouring suites re-run explicitly: `test_session_reconstruction.py`,
`test_durability_preconditions.py`, `test_recovery_ladder.py`, `test_acceptance_verdict.py`,
`test_proposal_boundary_records.py`, `test_task_graph_materialization.py` — **232 pass**.

`ruff` / `mypy`: not installed.

---

## 8. Honest limits

- **Nothing writes an escalation yet.** M12 — the recovery ladder is not consulted by the turn loop — is
  still open, so the write policy is correct-but-unexercised in production. It is tested through
  `_journal_turn_events`, which is a production entry point, by injecting a failing store. The policy
  exists **now** so M12 cannot wire it wrong.
- **`_journal_records_at_risk` names the whole set, not the lost one.** When the journal has a gap,
  nothing can say *which* event was lost — that is what a lost event means. Naming all seven is the
  honest report; claiming to know which is not.
- **The blob still carries no journal-only record.** The salvage reads the journal for them; it does not
  add them to the blob. Adding them would create a second copy that can disagree with the journal, which
  is the problem the migration exists to remove.
- **The escalation's *answer* path is untouched.** Answering an intervention goes through the transport,
  not the journal. ADR-0028's reversal condition names that as the next thing to analyse if it changes.
- **M12 remains the reason this is not exercised end to end.** Same prerequisite as M11, M13, M14, M15:
  **M9**.
