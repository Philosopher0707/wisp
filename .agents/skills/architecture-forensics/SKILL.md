---
name: architecture-forensics
description: Determine what an existing system actually does before proposing or implementing an architectural change — producers, consumers, authorities, state ownership, persistence, replay, failure paths, concurrency, configuration gates, and the mechanisms nothing calls. Use when asked to "map the architecture", "who owns this decision", "is this actually wired up", "what calls this", "why does this happen", "find the real implementation", or before any change that alters which component decides an outcome. Also use when documentation and behaviour disagree, or when a mechanism exists and is tested but appears to have no consumer.
agent_created: true
---

# Forensic recon — what actually happens

The purpose is not to describe the system. It is to establish **which claims are
true**, so a later decision rests on evidence instead of on a document.

## The one rule that matters

> Source behaviour is verified before architectural descriptions are trusted.

Treat every plan, ledger, ADR, README and docstring as a **hypothesis**. In a
mature codebase a surprising fraction are stale, over-broad, or describe the
wrong component. A claim you narrowed is a deliverable, not an embarrassment —
it is the difference between the change doing the right thing and the change
doing what was written down.

## Establish the baseline first

Before reading code, capture the ground you will be judged against:

1. **Snapshot the tree outside the repository.** Never `git stash` to get a
   clean tree — if pre-existing uncommitted work lives in the files you will
   touch, stashing discards someone else's work and makes unrelated failures
   look like yours.
2. **Record the starting state**: revision, branch, tracked modifications,
   untracked files. Treat all pre-existing work as immutable.
3. **Establish a stable failure set** if a suite exists — the intersection of
   two runs, not one run. A single run is not a baseline.

See `evidence-first-refactor-phase` for the mechanics of all three.

## Procedure

### 1. Find the entry points, then follow them

Do not start from the module the request names. Start from what actually runs:
the CLI command, the request handler, the job, the scheduler, the constructor
that production builds. Write down the call path from that entry point.

A change to a module no live path reaches changes nothing. Confirm the module is
on the path **before** analysing it.

### 2. Inventory by mechanism, not by name

Grepping for a function name finds the name, not the concept. Predicates get
renamed and one name can mean several things. Search for **what the code reads**:

```bash
# everything that decides an outcome from a status field
grep -rn --include="*.py" 'get("status"' .
grep -rn --include="*.py" -E '== *"(ok|error|failed|succeeded|completed)"' .
# everything that gates on a configuration key
grep -rn --include="*.py" 'getattr(config' .
```

Include inline checks inside larger functions — those are the ones that get
missed, and they are usually the ones on the live path.

### 3. Interrogate every mechanism

For each mechanism that matters, answer all seven. If you cannot answer one,
that is the finding.

```text
Who produces it?
Who consumes it?          <- the production consumer, not the test
Who acts on it?           <- reading it is not acting on it
Who persists it?
Can it be replayed?
Can another mechanism override it?
Who owns the decision?
```

The full question bank, with search recipes, is in
`references/interrogation-bank.md`.

### 4. Separate observation from authority

Lay the mechanisms out as a ladder and name the owner of each rung:

```text
observation -> classification -> verdict -> routing
   -> recovery -> enforcement -> completion -> persistence
```

Two failure shapes to look for:

- **A mechanism with no consumer.** It exists, is tested, has a decision record —
  and nothing on the live path calls it. This is the most common finding and the
  most consequential, because the record says it is done.
- **Two owners for one rung.** More than one component decides the same thing.
  They agree today by luck; they will drift. Hand this to
  `canonicalize-duplicated-authority`.

### 5. Verify by execution, not by reading

Reading tells you two things *look* different. Only execution tells you they
*disagree*. Build the smallest harness that drives the real entry point and
print what each mechanism concludes about the same input. Most claimed
divergences dissolve; the real ones are invisible to reading.

Record the command you ran and its output. An unexecuted finding is marked
unverified.

### 6. Hunt contradictions explicitly

When documentation and source disagree:

```text
REPORT THE CONTRADICTION
-> do NOT silently reconcile it
-> determine which contract is authoritative
-> record the correction
```

Two recurring shapes: the claim is **narrower or wider** than reality (the
system records something as prose rather than as data), and the **named
component is wrong** (the plan names module A, the live path uses module B).

### 7. Find the seams, not the wishes

A seam is a place where an existing boundary already carries the information a
change would need. Prefer naming an existing seam over proposing a new
subsystem. For each candidate, record what already crosses it, what owns it, and
what would have to be added.

## Verification discipline

| Rule | Why |
|---|---|
| A negative search result is a **hypothesis** | an empty grep is usually a bad pattern, not an absence. Re-check with a different form before concluding anything from it |
| Use `-e`/`-E` for alternation | some shells and `grep` builds silently return nothing for `a\|b` and exit non-zero. An absence you did not verify is not evidence |
| Prefer AST over text for structural claims | a whole-file text search matches the comment that *describes* the problem and reports a false positive. Parse the file and inspect nodes |
| Never conclude "unused" from one search | look for dynamic imports, registries, entry-point tables, and string-keyed dispatch before declaring something dead |
| **Instrument when one execution is ambiguous** | a single run shows *an* outcome, not *why*. Wrapping the suspect method to log every call, in a throwaway probe, is usually what turns "I think it works like this" into a sequence you can read. Do it in a script, never in a production file |
| **A probe that reuses a store reads a previous run's state** | a fixed temp path or a shared database makes the second run observe the first run's records — which looks exactly like a wrong result. Use a fresh directory per case, and re-run before believing a surprising reading |
| State the command and its output | a finding without evidence is an opinion |

## Output

Produce two artifacts, using the shared templates:

- `assets/templates/FORENSIC_RECON.md` — what was inspected, what was verified
  by execution, what contradicted, what is unresolved.
- `assets/templates/AUTHORITY_MAP.md` — the ladder, one row per mechanism, with
  the owner and the consumer named.

Both live in the `architecture-evolution` skill directory.

End by answering the question the next stage needs: **is the authority model
sufficient, or is there an unresolved decision?** Say which, and what evidence
would settle it.

## Failure modes this skill exists to prevent

| Symptom | Cause |
|---|---|
| A subsystem "built" that nothing calls | the consumer was never looked for |
| A confident finding that evaporated on execution | conclusions drawn from reading |
| "Nothing uses this" that turned out to be used | a negative search treated as proof |
| A change to a module off the live path | the entry point was assumed, not traced |
| A plan claim silently absorbed instead of corrected | documentation trusted over source |
| A duplicated decision layer left in place | only the predicate was compared, not the wiring |
