---
name: architecture-decision-engineering
description: Turn architectural ambiguity into an explicit, testable decision before any implementation — decision ownership, precedence, conflict matrices, failure and replay semantics, boundedness, rollback, rejected alternatives and implementation boundaries. Use when asked to "decide what should happen", "resolve the ambiguity", "who wins when these disagree", "write the ADR", "establish the contract", "define the precedence", or whenever two mechanisms can produce contradictory decisions and no rule says which takes precedence. Also use when a design question keeps being re-litigated, or when a change would create a second authority for an existing decision.
agent_created: true
---

# Engineer the decision before the change

Ambiguity does not disappear when implementation starts. It becomes a behaviour
nobody chose — decided by whichever branch runs first, or by a race.

## The one rule that matters

> Never resolve unresolved architectural ambiguity by guessing.

If precedence, ownership, boundedness or replay semantics are undefined, the
correct output of this session is a **decision artifact**, not a diff. A session
that ends with "the contract is X, and here is the evidence" has succeeded even
if no code was written.

## Before deciding anything — check who already owns it

An existing component may already own the decision. Creating a second one is the
defect this whole family of skills exists to prevent.

```text
observation -> classification -> verdict -> routing
   -> recovery -> enforcement -> completion -> persistence
```

For the rung your change touches, name the current owner. Then choose one:

| Situation | Move |
|---|---|
| One owner exists and is correct | **extend it** — do not build a peer |
| One owner exists but is not consulted on the live path | wire the existing owner; do not reimplement its logic |
| Two owners exist | consolidate first, or the change will entrench both |
| No owner exists | define one, and say explicitly which rungs it does **not** own |

**Never let a mechanism acquire authority by accident.** A model, a user, a
plugin or a tool must not become authoritative over permissions, completion,
routing, retry counts, escalation, policy or safety unless a decision grants
that authority in writing.

## Procedure

### 1. Enumerate the decision axes

List every question the change must answer, as a question, not as a design. For
a change to how something completes, axes typically include:

```text
who may declare the outcome
what inputs feed that authority
what happens when the inputs disagree
what an inconclusive/unknown input means
whether the decision is bounded, and by what
when the decision is evaluated relative to other decisions
what is persisted, and whether it is replayable
what happens when the feature is off
what happens when an input is unavailable or raises
```

An axis with no answer is a stop condition, not a detail.

### 2. Precedence — ordered states, not a score

When more than one condition can hold, define an **ordered relation** and make it
total. Prefer explicit state relations over any numeric severity, weight,
confidence or priority score: a score hides the rule and cannot be tested against
a case.

```text
row 0  already-recorded terminal state   -> frozen, never rewritten
row 1  operator cancellation             -> CANCELLED
row 2  escalation required               -> ESCALATED
row 3  hard failure                      -> FAILED
row 4  progress/stall condition          -> STALLED
row 5  inconclusive evidence             -> UNVERIFIED
row 6  success conditions all hold       -> SUCCEEDED
```

Two properties to state explicitly:

- **Freezing.** Once a terminal outcome is durable, later observations must not
  rewrite it. Name the mechanism that enforces this.
- **Reachability.** For each row, say which inputs make it reachable, and which
  make it *unreachable*. A row that can never fire is a documented gap, not a
  safeguard.

Worked shapes for precedence and conflict tables are in
`references/precedence-examples.md`.

### 3. The conflict matrix must be total

Build the table of every combination that can co-occur and give each one a
deterministic answer. **There must be no blank cell.**

| Condition A | Condition B | Required decision | Basis |
|---|---|---|---|
| success condition holds | stall condition holds | the stall row wins | row 4 over row 6 |
| hard failure | success evidence arrives later | failure stands | row 3; terminal evidence cannot override |
| inconclusive | success evidence | unverified | row 5 — never silently a pass |
| cancellation | any recovery request | cancelled | row 1; a recovery request cannot override it |

If a combination cannot be expressed with the states you have, that is a finding:
either add the state explicitly, or record that the architecture cannot represent
it and stop. Do not paper over it with prose.

### 4. Failure semantics

For every new input, decide what happens when it is:

```text
absent          (not supplied at all)
unavailable     (the component that produces it is missing or disabled)
raising         (it throws)
stale           (it reflects an earlier moment)
duplicated      (the same input arrives twice)
```

Choose **fail-open** or **fail-closed** deliberately, and say why. Two rules:

- Prefer fail-open when the failure is an *observability* gap, and fail-closed
  when it is a *safety* boundary. An observability gap that becomes a stuck run
  is a worse failure than the gap.
- Whatever you choose, make the absence **visible**. "Not evaluated" must be a
  distinguishable durable fact, not a silence.

### 5. Persistence and replay

Decide what is durable, and separate two things that are easy to conflate:

```text
durable decision inputs     -> must be recorded, and are authoritative
transient execution state   -> must NOT be recorded as if it were an input
```

Then state the invariant:

```text
reconstruct(recorded inputs) == live decision made from the same inputs
```

If a durable input is optional today, say whether it becomes mandatory when the
decision is authoritative. **A reconstructed run that cannot reproduce its own
decision must fail loudly, not guess a plausible default.**

Do not persist implementation details merely because they exist. If a fact does
not feed the decision, it is telemetry — and it must not be recorded as if it
were authority.

### 6. Boundedness

Every retry, replan, recovery, recursion, polling loop, fanout and intervention
needs an explicit bound. Name them separately — **they are not interchangeable:**

```text
logical retry budget      (how many times the work is re-attempted)
physical provider budget  (how many times the transport is re-tried)
iteration budget          (how many rounds the loop may take)
time budget               (wall clock)
recursion budget          (depth)
concurrency budget        (parallel width)
```

For the bound you introduce, state: the maximum, the owner, when it resets, and
what happens at exhaustion. Prefer reusing an existing bound **if it expresses
the required semantics without changing its meaning**; otherwise add one and say
why no existing bound fits.

A bound must not be able to turn its own honest surrender into a harder failure.
Check what happens when the bound is reached at the edge of another limit.

### 7. Rollback and reversal condition

State how to turn the behaviour off, and what would make you reverse the decision.
A decision without a reversal condition cannot be revisited on evidence — and
revisiting decisions on evidence is how phases find live defects rather than
confirming designs.

### 8. Rejected alternatives

List the alternatives you considered and why each lost. This is not ceremony: it
is what stops the same design being re-proposed next session. Always include
"do nothing" and "the simplest thing that could work".

### 9. Implementation boundary and non-goals

Write two explicit lists:

```text
WHAT THE NEXT PHASE MAY CHANGE   (files, interfaces, flags — be specific)
WHAT IT MUST NOT TOUCH           (the protected authorities, and why)
```

The non-goals list is part of the decision. Without it, the implementation phase
inherits an unbounded mandate.

## Mechanical repair or architecture decision?

The single most consequential classification you will make. A **mechanical repair**
restores a contract that already exists; an **architecture decision** creates one.
Getting this wrong in the cheap direction ships new policy with no record.

**Never infer the contract from the defect.** A component that rejects your input
does not thereby define the rule you violated. Prove the contract exists, in this
order:

```text
1. does the INTERFACE declare it?          read the abstract base / protocol /
                                           schema — not the implementation
2. is the apparent precedent a POLICY,     an implementation that handles the same
   or a POINT FIX?                         concern for one caller is not a contract
3. does the precedent's stated RATIONALE   if the comment is wrong, the code it
   actually hold?                          justified is unsupported, not authoritative
```

If all three fail, there is **no contract** — and a repair that makes the caller
conform is not restoration, it is legislation. That is the decision.

**The corollary that catches people: check whether a working fix exists that does
not change semantics.** If every behaviour-changing option alters what a user-facing
setting means, the classification is architectural *even if the code change is one
line*. Size is not the test; semantic blast radius is.

### The wrong-metadata trap

Before you reach for discovery, confirm that the discoverable quantity is the one
you need. Interfaces frequently expose a **near-miss**: a number of the same type,
in the same unit, about the same subsystem — and a different thing.

```text
you need:        the maximum OUTPUT tokens
the API returns: the CONTEXT window
```

These are not close enough to substitute. The failure mode is specific and nasty:
the near-miss is often **larger**, so clamping to it **leaves the original failure in
place** while looking like a fix — and it silently *raises* the value for every case
that previously worked. A fix that cannot be validated against the original symptom
is not a fix.

So the discovery question is two-part, and the second part is the one people skip:

- Is the limit discoverable? **And is it discoverable *before* the operation that
  needs it?** A limit you can only learn from the error is not discovery — it is a
  retry policy wearing discovery's clothes.

### A decision that changes no behaviour is a result, not a null

When you finish the option analysis and find that the behaviour you want **already
holds**, resist the pull to invent work. The correct outcome may be a ratified
decision whose behavioural sub-phase is **empty** — and that is a *strong* result,
because it means the system was already right and what was missing was the record.

Such a decision earns its place by doing two things no behaviour change can:

- **Assigning ownership.** A boundary with no owner is not "fine by default"; it is
  a place where the next contributor improvises. Naming the owner — and the exact
  condition under which they may act — is the deliverable.
- **Forbidding the tempting wrong fixes.** The alternatives you rejected are the
  real output. If three plausible repairs are all wrong, and each is wrong for a
  *different* reason, writing those reasons down is what stops them being tried
  again next quarter.

Two habits that make this land:

- **Check each rule against the code before writing it.** Split your rules into
  *already holds* and *missing*. If the "missing" set is empty, say so explicitly —
  an ADR that claims to authorise behaviour it does not need is worse than no ADR.
- **Say why the empty sub-phase is empty.** Otherwise a later reader assumes the
  work was skipped. "R1–R4 and R7–R10 describe behaviour that already holds" is a
  finding; silence is an omission.

The tell that you are doing this right: the decision is **trivially reversible**,
because reverting it restores exactly what was there before.

### "Who owns X?" — count the implementations before you name one

The moment a decision says *"there shall be one owner of X"*, you have made a factual
claim that there is not already more than one. **Verify it.** A decision phase is where
duplicate authority is easiest to miss, because the recon upstream was looking for
*behaviour* (who breaks) and not for *ownership* (who else claims the job).

Search for the **responsibility**, not the name. Two implementations of one job rarely
share an identifier:

```text
grep the concept   ->  "canonicalize provider events"   -> two hits, different names
grep the name      ->  "_normalize_event"               -> one hit. You conclude "single owner". Wrong.
```

What the second implementation tells you, once found:

- **Compare the two directly.** Extract the tables/lists/whitelists each one carries and
  diff them. If they are identical, you have a maintenance hazard; if they **differ**, you
  have a live defect — the narrower one silently drops whatever the wider one kept.
- **Then test the narrower one on real input.** Do not reason about which fields matter.
  Construct the object, run it through, and read the output. A canonicalizer that maps a
  payload-carrying event to an empty payload is a defect, and it is invisible in source
  review because the omission looks like a short list rather than a bug.
- **Establish reachability honestly.** A duplicate that no call path reaches today is
  **latent**, not fixed, and not urgent — say which. "Unreachable" is a finding; "not a
  problem" is not the same claim.
- **Prefer the structural closure.** A rule that makes the duplicate *impossible*
  ("`X` shall remain a canonicalizer of A/B inputs only") beats a rule that patches the
  duplicate's list, which will drift again.

This is the decision-phase analogue of the recon's own rule: the recon finds what the
system *does*; the decision finds what the system *claims to own*. They are different
questions, and answering the first well does not answer the second.

## Stop conditions

Stop and record rather than deciding, when:

- the decision needs a product or policy answer you cannot derive from the code,
- the decision would require changing a protected authority, and no one has
  authorised that,
- the source materially contradicts the contract you are writing,
- two of your own rows contradict each other (resolve before proceeding),
- the conflict matrix has a cell you cannot fill.

## Output

- `assets/templates/ARCHITECTURE_CONTRACT.md` — the full contract.
- `assets/templates/ADR.md` — the durable decision record, with its reversal
  condition.

Both live in the `architecture-evolution` skill directory. Append the ADR to
whatever decision log the repository already keeps; if it keeps one, do **not**
create a second log — that is a duplicated authority in the records.

## Failure modes this skill exists to prevent

| Symptom | Cause |
|---|---|
| The same design question argued every session | no decision record, or no reversal condition |
| Two mechanisms disagreeing in production | precedence never defined |
| A "score" nobody can explain | severity ranking substituted for a rule |
| A decision that changes across a restart | transient state treated as a durable input |
| A stuck run caused by a missing input | fail-closed chosen for an observability gap |
| A retry loop that never terminates | no bound named |
| Scope creep during implementation | no non-goals list |
| A second authority quietly created | ownership not checked first |
| A new policy shipped as a "mechanical fix" | the contract was inferred from the defect, not read |
