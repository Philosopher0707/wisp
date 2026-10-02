# Case study — how this methodology emerged

**This document is illustrative, not normative.** The four skills are written to
be repository-agnostic. Nothing here is required to use them, and none of these
identifiers appear in the skills' rules. Read this only to see the shapes in
concrete form.

## The setting

A local-first coding agent with an enterprise governance layer, mid-way through a
long, ledger-tracked migration that aimed to make an *existing* execution loop
graph-driven. A written plan of record existed. A phase ledger recorded status. A
growing set of architecture decision records (ADRs) recorded decisions.

The recurring discovery was **not** "a subsystem is missing". It was:

> The mechanism exists, is tested, has a decision record — and nothing on the
> live path calls it.

At one point a reconnaissance pass found **four** independently-built,
independently-tested authority mechanisms that each had **no production
consumer**: an acceptance verdict, a recovery ladder, a stagnation router, and a
failure classifier. They all dead-ended at the same place — the live turn loop
consumed no verdict at all, and completion derived from terminal evidence alone.
One module's own docstring said the verdict was exposed to *"nothing"*.

That is the shape the `architecture-forensics` skill exists to find, and the
reason its first rule is *find the production consumer*.

## Five findings worth internalising

### 1. A claim can be the wrong target, not merely unimplemented

A phase was recorded as the keystone, with five other items "blocked" on it. On
contact with the repository, the strong reading was not just unimplemented — it
was **wrong**: the structure the plan wanted to project the transcript from
carried no payload, so making it the source would have created a second copy of
the transcript that could disagree with the first.

The lesson: when a plan claim fails, check whether it is *unimplemented* or
*wrong*. They need different responses, and the second is easy to misread as the
first.

### 2. "Blocked on X" is usually an overstatement

The five items said to be blocked on the keystone turned out to need nothing from
it. Three were independent of it *and of each other*. The real work was several
small pieces, not one large one — and the ledger's framing had made it look
larger and more sequential than it was.

### 3. An architecture decision can contradict itself

An ADR was written to resolve an authority conflict. The next phase's
reconnaissance found the ADR said two incompatible things in two different
sections: one clause said a gate *may withhold* completion (bounded, surrendering
honestly); another said a clean success *may coexist* with the stall state. Under
a hard veto the second is impossible.

The resolution was not to supersede a clause but to notice that **bounded delay
makes both true**: the gate withholds, then surrenders, and the coexistence is
exactly what the surrender produces. The decisive evidence was in the ADR itself
— it already required the bound, and its own rejected-alternatives table already
forbade the veto's consequence (converting a progress signal into a failure).

Lesson: before superseding a clause, check whether the clauses are consistent
under a reading you have not considered. The `architecture-decision-engineering`
conflict matrix is the tool for that.

### 4. A guard can forbid the fix it was written to protect

A ratchet asserted that a structure carried no payload, by listing forbidden
field names. It included the one field the *same phase's* report said was needed
to make the structure drivable — and it was evadable by naming anyway.

The fix was not to delete the ratchet but to make it stronger and more honest:
classify **every** field, declare the payload kind with no members, and assert
totality. A guard that pins a property cannot be defeated by a rename.

### 5. Two producers of one structure is a defect, even when they agree

A journal and a live path were building the same reply with different keys.
Nothing broke — one consumer had a fallback and an adapter normalised it, which is
precisely why the divergence survived. The same pattern later produced a **live
versus replay** disagreement: the arbitration input was computed one way, the
recorded evidence another, and a reconstruction from the record produced a
different state than the live run.

Lesson: assert the *equality* of the two computations, not the behaviour that
happens to tolerate their difference. The `replay-durability` checklist exists for
this.

## Three implementation lessons

### The interface has two sides

Adding an optional parameter to a function is compatible at the **call site** and
incompatible at the **implementation**. Twelve unrelated tests failed with
`TypeError` because test doubles implemented the old signature. The fix was to
pass the new argument **only when it was needed**, so the default path emitted
the old call unchanged.

Focused tests could not have caught this — they all drove the real implementation.
Only the full suite could. Hence `reliability-phase-engineering`'s rule about
enumerating every implementation of a changed interface.

### A bound must not be able to turn its own surrender into a harder failure

A gate withheld completion for a bounded number of attempts. Withholding on the
**last** iteration would have ended the loop, run the budget wrap-up, and
converted the honest surrender into a fatal budget error. So the bound needed a
third condition: *a further attempt must actually exist.*

### The predicate was a latch, which bounded what the change could achieve

The enforcement was implemented faithfully — and measurement showed it could
never change the outcome, because the predicate it consulted latched and never
reopened: a flat observation always produced the same state digest, which always
tripped the oscillation trap, whose verdict list was never cleared.

The report said so plainly rather than implying the intervention was effective.
That is the `PHASE_REPORT` template's *honest limits* section, and it is the
single most valuable paragraph in any phase report.

## Why the methodology is shaped this way

Every stage corresponds to a failure that actually happened:

| Stage | Failure it prevents |
|---|---|
| Forensic recon | building on a plan claim that was stale, over-broad, or the wrong target |
| Authority mapping | a mechanism that exists and nothing calls |
| Decision engineering | implementing an ambiguity, so behaviour is decided by whichever branch runs first |
| Implementation | widening a pinned interface, unbounded loops, accidental authority |
| Tripwires | a boundary that is documented but not asserted, and drifts back |
| Adversarial verification | a false success, a second authority, an unbounded loop |
| Replay / durability | a decision that changes across a restart |
| Evidence report | a report that reads better than the code |
