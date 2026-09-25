# PHASE POST-M13 — ADR-0039 CANONICALIZATION OWNERSHIP RECONCILIATION

**Status:** RATIFIED · **ADR:** 0040 · **Phase:** PM-22 · **Date:** 2026-09-25 · **HEAD:** `7c15626`
**Production changes:** 0 · **Test changes:** 0 · **Default changes:** 0

---

## 1. Mission Result

```text
RATIFIED  →  ADR-0040   (OPTION B)
```

> **`wisp.core.events.canonical_event` is the formally ratified single provider-event
> canonicalization authority. `WispAgentCore._normalize_event` is retained as a
> compatibility/delegation facade, and `events.normalize_event` as a compatibility entry point with
> no independent provider-object canonicalization authority.**

Authority is defined by **ownership of the canonical schema and the provider projection**, not by
which historical facade invokes it. The implementation-discovered boundary is ratified; ADR-0039
R2's *subject* is amended and nothing else in ADR-0039 changes.

---

## 2. Provenance

| | |
|---|---|
| HEAD | `7c15626` "docs(m11): record the M11 phase; findings F29-F31; repair the commit table" |
| branch | `main` |
| commits this phase | **0** |
| staged | **0** |
| modified tracked | 51 (was 44 before PM-21; the 7 new are PM-21's production + test files) |
| untracked | 74 |

The ADR-0039 implementation is present (`CANONICAL_EVENT_FIELDS` in `events.py`;
`_normalized_provider_stream` in `stateless.py`). All pre-existing WIP preserved — nothing reset,
stashed, cleaned, checked out or staged.

---

## 3. Re-verified Facts

All eight hold. Two initially read FAIL and both were **my scanner's bugs**, not the code's — recorded
here because the distinction matters:

| | Fact | Result | Evidence |
|---|---|---|---|
| **A** | `events.canonical_event` owns the whitelist and the provider-object projection | PASS | `events.py:160` (`CANONICAL_EVENT_FIELDS`), `:180` (`canonical_event`) |
| **B** | `WispAgentCore._normalize_event` delegates | PASS | `return canonical_event(event)` |
| **C** | `events.normalize_event` delegates, owns no whitelist | PASS | no `safe_fields` assignment; body calls `canonical_event(event)` |
| **D** | Exactly one `CANONICAL_EVENT_FIELDS` definition | PASS | AST scan: **1 definition**, 2 readers, both in `events.py` |
| **E** | F42 stays closed | PASS | `ToolCallBatch.calls` and `StreamComplete.done_reason` both survive |
| **F** | Every consumer obtains canonical events | PASS | main loop (via the guard), wrap-up, compaction, planner |
| **G** | No second provider-object whitelist | PASS | AST scan for any collection with ≥4 canonical field names: **exactly one**; and exactly **one** function mapping a provider object to a canonical dict |
| **H** | Replay/persistence unchanged | PASS | no typed event class referenced in `runtime.py` |

**The two false FAILs — worth recording as method, not noise.** D and G first reported FAIL because
(a) `CANONICAL_EVENT_FIELDS` is an `ast.AnnAssign` (`x: T = …`), which a scan looking only for
`ast.Assign` cannot see, and (b) a shell `grep -E` with `[str]` is a *character class*, not a literal.
Both are the same class of error the project's own discipline warns about: **a scanner that reports
"absent" is not evidence of absence until it has been shown to find the thing that is there.**

---

## 4. Current Implementation

```text
                    PROVIDERS
                 typed OR dict
                       │
                       ▼
        wisp/core/events.py
        ┌────────────────────────────────────┐
        │ CANONICAL_EVENT_FIELDS   (events.py:160)  <- the ONE whitelist
        │ canonical_event          (events.py:180)  <- the ONE projection
        └────────────────┬───────────────────┘
                         │
              ┌──────────┴───────────┐
              ▼                      ▼
  WispAgentCore._normalize_event    events.normalize_event
  (stateless.py)                    (events.py)
  FACADE: returns canonical_event   COMPATIBILITY: AgentEvent/dict as-is;
                                    provider objects delegate
              │                      │
              ▼                      ▼
   _normalized_provider_stream    runtime persistence,
   → main loop / wrap-up          transport/headless
   compaction, planner (direct)
```

`wisp.core.events` is a **leaf module** (zero `wisp.*` imports). `wisp.core.stateless` has **14**
`wisp.*` imports and depends on it.

---

## 5. Option A — Preserve ADR-0039's literal ownership

`WispAgentCore._normalize_event` would own canonicalization; `canonical_event` would become a
subordinate detail, or the implementation would move back into `stateless.py`.

| Question | Finding |
|---|---|
| Does moving ownership back improve clarity? | **No.** It would make the *facade* the authority while the *implementation* sits elsewhere — the ambiguity this ADR exists to remove, merely inverted. |
| Does it couple generic representation to core state? | **Yes.** The canonical schema is provider-neutral data shape; `stateless.py` is a ~2,400-line agent-runtime module. |
| Does it risk duplicating logic? | **Yes, concretely.** `core/compaction.py` and `graph/planner.py` are outside the core and cannot hold a per-turn `WispAgentCore`. They would have to reach a bound method of an object they do not own — or grow their own copy, which is F42 again. |
| Any consumer-visible benefit? | **None.** No caller observes which module holds the whitelist. |
| Would it change already-tested code? | **Yes.** It would move a whitelist and a projection that 24 new tests and the whole `tests/reliability/` directory now cover. |
| Is `events.py` a more natural home for the schema? | **Yes.** It is a leaf, and **21 production modules** already depend on it — including every event producer and consumer. |

**Verdict: rejected.** "The original ADR said so" is not sufficient justification, and on inspection
the original wording assumed a single consumer inside a single core — which the F40 implementation
disproved.

---

## 6. Option B — Ratify the implementation-discovered ownership

| Question | Finding |
|---|---|
| Separation of generic representation from the agent runtime | **Improved.** The schema lives in a provider-neutral leaf; the runtime depends on it. |
| Compatibility with existing callers | **Preserved.** All three entry points keep working; only their authority is distinguished. |
| F42 closure | **Structural.** One whitelist; R4 forbids a second. |
| Future provider support | A new provider emits typed or dict and does nothing else (R7). |
| Testability | The canonicalizer is a pure function of one argument, testable with no core. |
| Bypass resistance | The only caller of the raw stream is the normalization boundary (asserted); R5 removes the "second canonicalizer" ambiguity. |
| Replay stability | **Unchanged** (Fact H). |
| Dependency direction | **Correct** — downward into a leaf. |
| Facade ambiguity | **The one real cost.** Mitigated by R5 and by docstrings on both functions. |

**Verdict: selected.**

---

## 7. Decision Matrix

Tradeoffs only — **no scores, no rankings.**

| Dimension | Option A: `_normalize_event` owns | Option B: `canonical_event` owns |
|---|---|---|
| Conceptual ownership | the facade is the authority while the implementation sits elsewhere | ownership matches the implementation |
| Dependency direction | inverts: a leaf's schema would live in a 14-import runtime module | downward into a leaf (`events.py`, zero `wisp.*` imports) |
| Coupling | canonical schema coupled to agent-core state | schema provider-neutral |
| F42 prevention | needs a new rule to forbid the copy that the coupling invites | one whitelist; R4 makes a second a violation |
| Compatibility | unchanged entry points | unchanged entry points |
| Testability | canonicalizer reachable only with a core (or a second copy) | pure function of one argument |
| Future-provider safety | a provider added later may still need to reach core state | provider emits typed or dict and nothing else |
| Future-consumer safety | out-of-core consumers must reach a core instance or copy | they import the leaf |
| Implementation churn | moves a whitelist + projection; rewrites tests | **zero** — the code already is this |
| Rollback | — | cheap: move one whitelist back, invert the delegation |
| Architectural clarity | record and code would agree on the *name* but disagree on the *shape* | record and code agree on both |

---

## 8. Authority Definition

| Term | Definition | Component |
|---|---|---|
| **Implementation authority** | owns the canonical field whitelist, the provider-object interpretation, the typed→canonical mapping, the totality contract and the canonical output shape | `events.canonical_event` (R1) |
| **Compatibility facade** | delegates; owns no whitelist; owns no provider interpretation; cannot diverge independently | `WispAgentCore._normalize_event` (R2) |
| **Compatibility entry point** | the pre-existing `AgentEvent`/dict normalization path, retained for its callers; provider objects delegate | `events.normalize_event` (R3) |
| **Consumer contract** | consumers receive canonical events only; none interprets a raw provider event | ADR-0039 R4 (unchanged) |
| **Terminal authority** | which types mean "the provider declared the stream finished" | `provider_stream.TERMINAL_TYPES` (unchanged) |

**Yes — `WispAgentCore._normalize_event` is, after reconciliation, merely a facade.**

---

## 9. F42 Implication

F42 was two whitelists drifting apart. The ratified invariant is **one whitelist, one projection, two
delegating entry points**:

```text
CANONICAL_EVENT_FIELDS  (one)
        │
        ▼
canonical_event         (one)
        ▲
        ├── _normalize_event      (delegates)
        └── events.normalize_event (delegates for provider objects)
```

> **The canonicalization authority is defined by ownership of the canonical schema and provider
> projection, not by which historical facade invokes it.**

F42 is therefore evidence, not merely a bug: the second whitelist was never *declared*, so nothing
could detect it. R4 makes a second whitelist a **violation**; R5 removes the ambiguity that let one be
created unnoticed.

---

## 10. Dependency Direction

| Module | `wisp.*` imports |
|---|---|
| `wisp/core/events.py` | **none — leaf** |
| `wisp/core/stateless.py` | **14**, including `wisp.core.events` |

**21 production modules** depend on `wisp.core.events` — `provider_stream`, `runtime`,
`approval_gate`, `proposal`, `recovery`, `compaction`, `planner`, `tool_executor`, `sdk`,
`supervisor`, `acp_session`, `contracts/envelope`, `tools/context`, and four transports.

`events.py` can remain a low-level, provider-neutral representation module while `stateless.py`
depends on it — and it already is one. **No new module was introduced; no import was refactored.**

---

## 11. Compatibility

| Entry point | Role | May own a provider whitelist? |
|---|---|---|
| `canonical_event` | **authoritative implementation** | **yes — and it is the only one** |
| `WispAgentCore._normalize_event` | compatibility/delegation facade | **no** |
| `events.normalize_event` | existing event-normalization compatibility path | **no** |

All three remain valid and callable. Only their authority is distinguished. Verified: Fact B, Fact C,
Fact D.

---

## 12. Replay / Security

**Replay — UNCHANGED.** `canonical_event` produces a flat canonical dict; no typed event class is
referenced in `runtime.py`; the persisted schema is unchanged. **No migration. No replay adapter.**

**Security — UNCHANGED.** This phase changes an ownership *label*, not behaviour. Canonicalization
remains a whitelist projection: it may remove keys, never add, never execute provider data, never
invoke a tool, and never touch authorization, approval, `ToolExecutor`, the sandbox, tool-argument
validation, model authority or completion authority. The adversarial check from PM-21 stands — a
hostile provider object whose `.get()` and `__call__` raise yields
`{'type': 'unknown', 'text': 'payload'}` with neither method invoked.

---

## 13. ADR Handling

**Action: a new sequential ADR — ADR-0040 — that amends ADR-0039's R2 subject and supersedes its
"`AgentEvent`/dict inputs only" clause. ADR-0039's text is left untouched.**

The convention was established before writing, not assumed:

- every ADR in the log carries `**Status:** ACCEPTED`; **no** in-place "AMENDED BY" marker exists
  anywhere;
- amendments are recorded as **new numbered ADRs** whose index row names the relationship —
  ADR-0036 "amends ADR-0035", ADR-0037 "completes ADR-0036".

```text
highest existing ADR      0039
sections before append    39
index rows before append  39
collision check           ADR-0040 / ADR-0041 -> no match anywhere
number used               ADR-0040
appended append-only      before "## Decision index"
sections after append     40
index rows after append   40
max heading after append  ## ADR-0040
CONTEXT.md range          ADR-0001 … ADR-0040  (2 occurrences)
```

**No existing ADR was modified.**

---

## 14. Required Documentation Alignment

The decision record itself is updated (ADR-0040 + index row + the `CONTEXT.md` ADR range), as the
convention requires. The following still name the **old** owner and are listed rather than silently
rewritten:

| Document | What it says | Required alignment |
|---|---|---|
| `PHASE_POST-M13_F40_PROVIDER_EVENT_CONTRACT_ADR.md` | status block: `NORMALIZATION_OWNER: WispAgentCore._normalize_event` | **historical record of what was decided then.** A one-line dated pointer to ADR-0040 is sufficient; the text itself should not be rewritten. |
| `WISP_ARCHITECTURE_DECISIONS.md` — ADR-0039 R2 | `Exactly one canonicalizer … WispAgentCore._normalize_event` | **left as written** (append-only); ADR-0040's index row and R1 name the amendment. |
| `CONTEXT.md` — the PM-20 row | "one canonicalization owner" | describes the *decision*, not the owner; no change needed, but the PM-22 row now states the owner explicitly. |
| `WISP_MIGRATION_STATUS.md` — the PM-20 row | "one canonicalization owner" | same. |
| `PHASE_POST-M13_F40_PROVIDER_EVENT_NORMALIZATION_IMPLEMENTATION.md` | status block: `NORMALIZATION_OWNER: wisp.core.events.canonical_event` | **already correct** — Option B wording. |
| `PHASE13_H1_TERMINALITY_CONTEXT_FORENSIC.md`, `REPOSITORY_INTELLIGENCE_REPORT.md`, `docs/archive/GRILL_REPORT.md` | incidental `_normalize_event` mentions | describe the method as it existed at the time; **no change**. |

One pointer was added to the PM-20 report's status block, because a reader consulting the decision
report would otherwise take the superseded owner name as current.

---

## 15. Production Changes

```text
0
```

No source file was modified. The code already **is** Option B; no cosmetic change was made to make it
"look" ratified.

---

## 16. Test Changes

```text
0
```

---

## 17. Final Status

```text
=== STATUS: RATIFIED ===

DECISION        :  OPTION B
AUTHORITY       :  wisp.core.events.canonical_event        (ADR-0040 R1)
FACADE          :  WispAgentCore._normalize_event          (ADR-0040 R2)
COMPAT ENTRY    :  events.normalize_event                  (ADR-0040 R3)
WHITELIST       :  CANONICAL_EVENT_FIELDS — exactly one definition (R4)
INVARIANT       :  one implementation, one whitelist, zero provider-local
                   canonicalizers, zero consumer-local canonicalizers (R5, R7)
ADR             :  0040 — new sequential ADR, amends ADR-0039 R2's subject;
                   ADR-0039's text untouched
FACTS A–H       :  8/8 hold
REPLAY          :  UNCHANGED
SECURITY        :  UNCHANGED
PRODUCTION_CHANGES :  0
TEST_CHANGES       :  0
DEFAULT_CHANGES    :  0
NEXT            :  optional — the F43 (guard recovery) and F44 (completion
                   authority) decisions, and the §14 documentation pointer
```

---

## 18. Evidence

| Artefact | Contents |
|---|---|
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0040**, appended append-only + index row |
| this report | the facts, the option analysis, the matrix, the ADR determination and the alignment list |

Measurements this phase (all read-only): the AST fact-scan for definitions, readers, whitelist
collections and provider-object mapping sites; the import graph for the two modules and the
21-module dependent set; the ADR-convention inspection.
