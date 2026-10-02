# The interrogation bank

Detailed questions and search recipes for `architecture-forensics`. Load this
when the inventory step needs more than the seven core questions in SKILL.md.

## The seven core questions, expanded

For every mechanism, answer all seven. An unanswered one **is** the finding.

### 1. Who produces it?

- Where is the value computed, and what does it read?
- Is it computed once, or recomputed at each call site?
- Are there **two** producers that could drift? (Compare the fields they write, not the function names.)
- Does the producer run on the live path, or only under a flag / in tests?

```bash
grep -rn --include="*.py" -E '<field> *=|"<field>":' .   # who writes this?
```

### 2. Who consumes it?

- The **production** consumer. Not the test, not the docstring, not the type.
- Is the consumer on the live path from a real entry point?
- Does the consumer **act** on it, or only read it? (Reading is not acting.)

```bash
grep -rn --include="*.py" '<symbol>' . | grep -v '/tests/'
```

If the only hits are the definition, its own test, and prose, the mechanism is
**unconsumed** — record that, and say which document claims otherwise.

### 3. Who acts on it?

The gap between "read" and "act" is where most architectural gaps live. A value
can be computed, logged, persisted and displayed while nothing changes behaviour
because of it.

Ask: *if this value were always the same, would any behaviour change?* If no, it
is an observation, not a control.

### 4. Who persists it?

- Which store, which record, which schema version?
- Is the record **journal-only**, **blob-only**, or both?
- If a reconstruction reads only one of the two, which facts does it lose?
- Is the record's loss a correctness problem or only an audit problem?

### 5. Can it be replayed?

- Is the decision a pure function of durable facts, or does it read live state?
- If it reads live state (a detector, a cache, a clock, a connection), replay
  **cannot** reproduce it — say so explicitly.
- Which inputs are optional today, and does that make replay ambiguous?

### 6. Can another mechanism override it?

- Trace the **ordering**: which is consulted first, and does the first one
  short-circuit?
- Is the ordering asserted anywhere, or only implied by statement order?
- Two mechanisms at the same site with no asserted order is a race.

### 7. Who owns the decision?

- Name the module. If two names come up, that is a duplicated authority.
- What does the owner explicitly **not** own? (An owner with no stated limits
  usually has more authority than intended.)

## Search recipes

### By mechanism, not by name

```bash
# everything deciding an outcome from a status-like field
grep -rn --include="*.py" -E 'get\("(status|state|result|outcome)"' .

# everything comparing against a literal verdict
grep -rn --include="*.py" -E '== *"(ok|error|failed|succeeded|completed|done)"' .

# everything gating on configuration
grep -rn --include="*.py" -E 'getattr\((self\.)?config' .

# inline predicates inside larger functions — these get missed
grep -rn --include="*.py" -E 'return (not )?[a-z_]+ *[<>]=? *[0-9]+' .
```

### Entry points

Find what actually runs before analysing anything:

```bash
# CLI / console entry points
grep -rn -E 'if __name__ == .__main__.|entry_points|console_scripts' .
# request handlers / job registrations
grep -rn --include="*.py" -E '@(app|router|bp)\.(get|post|route)|register_job|@task' .
```

### Call sites of a symbol

```bash
grep -rn --include="*.py" '\.<method>(' . | grep -v '/tests/'
```

Then ask of each hit: is it on a path from a real entry point?

## Hazards that produce false findings

| Hazard | What happens | Do instead |
|---|---|---|
| Alternation in a shell grep | `grep "a\|b" file` can silently match nothing and exit non-zero | use `-e` / `-E`; re-check any absence |
| A whole-file text search for a symbol | matches the **comment describing the problem** (including your own docstring) | parse the file (AST) and inspect nodes |
| Concluding "unused" from one search | misses dynamic imports, registries, entry-point tables, string-keyed dispatch | search for the symbol as a **string** too |
| Synthetic inputs | produce confident, wrong findings | take inputs from the real producer |
| Reading instead of executing | two things that look different may agree; two that look identical may disagree | drive both and compare outputs |
| Tool output that skips dot-directories | a search for something in a hidden directory reports absence | check whether the search tool traverses hidden paths; if unsure, use two tools |
| A resource-limited environment | a long run is killed mid-flight with no summary | split into chunks, union the results, and state the weaker method |

## Contradiction catalogue

When source and documentation disagree, classify the disagreement — the class
determines the fix:

| Class | Shape | Resolution |
|---|---|---|
| **Stale** | the doc described an older behaviour | correct the doc |
| **Narrower/wider** | "no record of X" when X is recorded as prose, not data | re-scope the claim, then decide |
| **Wrong target** | the plan names module A; the live path uses module B | re-target; mutating A changes nothing |
| **Aspirational** | the doc describes intended behaviour as if implemented | mark unimplemented; do not build on it |
| **Self-contradicting** | one document says both, in different sections | **stop**: this is a decision, not a doc fix |
| **Mis-categorised** | an infrastructure failure is reported as a domain verdict — the handler's *category* does not match the failure's *cause* | the **producer** is wrong, not the doc: separate the two categories at the point of reporting |
| **Wrong argument** | the component and its documentation agree; the **call site** hands it a representation the contract does not describe | the **call site** is wrong — fix the adapter, not the component |

### The mis-categorised refusal

The sharpest form of this class: an exception handler written to report one thing
catches a different thing and reports it as the first. The tell is that **two
inputs with opposite meanings produce the same output** — a valid request and an
invalid one, refused with identical text.

It is invisible to inspection, because the handler looks correct in isolation: a
missing dependency really does raise inside the guarded block. And it is invisible
to tests, because the refusal is *expected* in the broken environment — every test
that "passes" has been asserting the wrong reason.

Find it by executing the discriminator, never by reading:

```text
feed the validator a KNOWN-VALID input
feed it a KNOWN-INVALID input
compare the two outputs

identical text   ->  the validator did not run; the verdict is not about the input
```

Then follow the **category** downstream, because that is where the cost is. A
refusal that is a domain verdict is usually classified as one: routed by a retry
policy, written into an audit taxonomy, and surfaced to a caller that treats it as
a content problem. The missing dependency is cheap to fix; every consumer that
trusted the label is not.

Two questions that generalise past validation:

- **Does the handler's `except` clause span more than the thing it reports on?**
  A bare `except Exception` around an `import` makes the module's absence
  indistinguishable from the input's invalidity.
- **Can the system say "I could not check"?** If absence of a verdict and a
  negative verdict are the same value, every failure of the checker becomes an
  accusation against the checked thing.

### The correct component with the wrong argument

A sibling of the mis-categorised refusal, and the one that survives every unit
test. Here the component is **faithful**: drive it with the representation its
docstring describes and it answers correctly, every time. The defect is that the
**one production call site** feeds it a different representation — a wrapper, an
envelope, a serialised form — and the component cannot tell.

The tell is structural, not textual:

```text
the component's contract says:      <representation A>
the call site actually passes:      <representation B>
the component's check is shape-specific  (startswith, a key lookup, a type test)
A and B agree on the common case, and disagree exactly on the case that matters
```

Find it by **classifying one input twice** — once as the component documents it,
once as the call site delivers it — and comparing:

```text
verdict(A) != verdict(B)   ->  the defect is the ADAPTER, not the component
verdict(A) == verdict(B)   ->  look elsewhere
```

Why it hides so well:

- **Unit tests pin the contract, so they all pass.** Every test author read the
  docstring and built representation A. The suite is green and stays green; the
  missing coverage is an **integration** test, not a unit test.
- **The call site is not lying.** It forwards the producer's output verbatim, so
  a reader checking "did it mangle the value?" concludes no.
- **"Just pass the raw output" is not a fix** when the raw output *is*
  representation B. Confirm this before proposing it: if the value at the call
  site is byte-identical to the producer's output, the fix must **unwrap**, not
  re-plumb.

Two questions that generalise:

- **Does the component's contract name its counterparty?** A comment saying
  "owned jointly by this and `<producer>`" is a written contract — and it makes
  the call site's violation a **mechanical** repair, not a decision.
- **Is the fact available in a typed form the consumer ignores?** If the producer
  already emits a structured field (`exit_code: 3`) alongside the text the
  consumer parses, the information was never lost — only unread. That distinction
  separates a wiring fix from a data-model change, and only the second needs a
  decision record.

### Strip your own layer out of the path

Before you attribute a fault to your system, **re-run the request with your system
removed** — talk to the dependency directly, with the same inputs, and look at what
comes back. It is the cheapest attribution test there is, and it splits the two
answers that look identical from inside:

```text
your code -> dependency -> malformed result      (fault could be either)
             dependency -> malformed result      (fault is BELOW you)
your code -> dependency -> correct result        (fault is YOURS)
```

Why this is worth a dedicated step:

- **The symptom is identical either way.** A corrupted field, a truncated payload,
  a missing key — from inside your process you cannot tell "I broke it" from "it
  arrived broken".
- **It changes who owns the fix**, and therefore whether you may touch anything at
  all. A defect below your boundary is a finding to *report*; one inside it is a
  repair you may be authorised to make.
- **It is usually a handful of lines** — one HTTP call, one subprocess, one direct
  library invocation — against a full turn or a full pipeline you would otherwise
  be debugging.

Two habits that make the answer trustworthy:

- **Use the real inputs, not a synthetic minimum.** A minimal request can succeed
  while the real one fails (a context-size limit, a schema the dependency only
  validates at scale). Re-run with the actual payload before concluding.
- **Distinguish a *capability* limit from a *protocol* limit.** "The model cannot do
  this" and "the model refused because we sent something invalid" produce the same
  empty result but need opposite responses. The error class separates them — a
  domain-level refusal (a schema verdict, an empty answer) versus a transport-level
  rejection (a status code with a reason). Read the body; never infer it from the
  absence of output.

### When you find the failure, ask what happens *after* you fix it

A first defect can **mask** a second one later in the same expression, and the masking
is the reason nobody has seen the second. The first failure aborts before the next
branch is ever reached, so that branch is **untested ground** — not proven, merely
unvisited.

Ask explicitly: **if I fixed the failure I just found, what would the very next line
do with the value it now receives?** Then check that line's condition against the
actual producer output.

- **Symptoms do not bound the repair.** "The loop crashes on `.get()`" describes
  where it stopped, not what is wrong. The scope of the fix is defined by the
  contract the code should honour, which you only learn by walking past the crash.
- **The usual hiding place is a vocabulary.** A consumer that accepts one spelling
  (`"done"`) while the producer emits another (`"complete"`) is invisible until the
  crash in front of it is removed. Enumerate the accepted values at **every**
  consumer of one field and diff the sets — a disagreement between consumers is a
  defect even before you find the input that triggers it.
- **Prove it with a two-row table.** Drive the same path with the old value and the
  new one. If the second row still fails, the repair you were about to write is
  incomplete — and you have the evidence before writing it.
- **Say so in the report.** "Two-part defect; the second masked by the first" is a
  materially different finding from "one crash", and it changes what the next phase
  is authorised to do.

### A scan that reports "absent" is not evidence of absence

Every negative finding rests on a scanner — a grep, an AST walk, a query. **Before you
record "there is only one X" or "nothing else does Y", prove the scanner can find
something you already know is there.** Otherwise you are reporting your instrument's
blind spot as a property of the system.

Two real misses, both from the same shape — *the pattern looked right and matched
nothing*:

- **An AST walk that only handled `ast.Assign` missed an annotated declaration.**
  `X: frozenset[str] = {...}` is an `ast.AnnAssign`; a scan for `ast.Assign` finds zero
  definitions and cheerfully reports "absent". Any scan over declarations must handle
  `Assign`, `AnnAssign` and `AugAssign`.
- **A shell `grep -E` treated a literal as a character class.** `frozenset[str]` is a
  *class* matching one of `s`, `t`, `r` — so a search for the literal finds nothing and
  exits 1. `\s` and `\b` are not portable either. Prefer the structured tool, and when
  you must use the shell, `-F` for a literal.

The discipline:

1. **Run a positive control.** Point the scanner at an input you know it should match.
   If the control is empty, the scan is broken — not the system.
2. **Say what the scan *can* see.** "No second whitelist exists" is weaker than "an AST
   walk for any collection containing ≥4 canonical field names returns exactly one hit,
   and the same walk returns that hit when given a deliberate decoy".
3. **Cross-check a negative with a second method.** Text count *and* AST; two greps with
   different flags; the tool *and* the shell. Agreement between two independent
   instruments is the evidence; one instrument is a hypothesis.
4. **Report the instrument, not just the result.** A later reader must be able to tell
   whether "absent" means "the system lacks it" or "my pattern missed it" — those are
   very different claims, and the second is common.

This matters most for the *decisive* fact in a decision. When a single negative finding
carries the argument — "there is exactly one authority" — an unvalidated scanner is the
whole basis for ratifying an architecture.

### A mis-classified value is usually an ordering bug — and the repo may already have named it

When the same semantic condition produces **different outcomes depending on how it was
spelled**, the instinct is to fix the list. Check the **order of the checks** first.

The shape to look for:

```python
if value not in SOME_LIST:      # (1) vocabulary decides
    meaningful = True
if value in ANOTHER_LIST:       # (2) semantic check
    if carries_payload(value):
        meaningful = True
    break
```

Step (2) can only ever **add** to what step (1) decided, so a value that step (1) wrongly
accepted can never be corrected — and a value present in one list but missing from the
other takes a different path for reasons that have nothing to do with the data. Adding
the missing entry *derives around* the hazard: it fixes today's spelling and leaves the
next one to be forgotten. **Reorder so the semantic check runs first, and the list stops
needing to contain that value at all** — the duplication is then eliminated rather than
maintained.

Two questions that settle it:

- **Which check can only add, and which can remove?** A classification that cannot revoke
  an earlier verdict must run *after* the one that can.
- **If I add a new value to the semantic set tomorrow, what else must be remembered?** If
  the answer is "another list", the order is wrong. If the answer is "nothing", it is right.

**Before any of this: search the tests for the defect's own write-up.** Mature codebases
often already know. Grep for the vocabulary of a recorded-but-unfixed issue —
`not fixed`, `recorded`, `known`, `mismatch`, `TODO`, `XXX`, and the phase/finding
identifiers the project uses. In one case three separate tests pinned the symptom as the
contract and one said, in as many words, *"a silent empty success. Recorded, not fixed."*
The investigation took minutes instead of hours because the cause had been written down —
and it also told us which tests would have to change, because they were the ones that had
encoded the bug deliberately.

That second move has a cost worth accepting: when a test documents a defect on purpose,
"fixing the code" **must** be paired with rewriting that test. Read its class and its
neighbours first — its name is often about something else, and the defect assertion was
incidental.
