---
name: reliability-phase-engineering
description: Implement a ratified architectural decision with the smallest viable change, add tripwires that keep the authority boundaries intact, and verify that existing behaviour and ownership are preserved. Use when asked to "implement this decision", "wire up the seam", "land this phase", "make the mechanism authoritative", "add the feature flag", or after a contract has been ratified and the change touches a gate, a decision point or a durable record. Also use when an implementation landed but its evidence is incomplete, or when a full-suite failure set looks like it moved.
agent_created: true
---

# Land the change, keep the architecture

## The one rule that matters

> "The feature works" and "the architecture remains correct" are different
> claims, and only the second is the deliverable.

A change can pass every new test and still have created a second authority,
widened a pinned interface, made an unbounded loop, or broken replay. Verify the
architecture, not just the behaviour.

## Implementation

### Smallest viable seam

Prefer, in order:

```text
existing boundary + small interface + local state
    > new parameter on an existing call
        > new module
            > new subsystem
```

Before adding an interface, check whether the boundary already carries what you
need — an existing injected dependency, an existing published handle, an existing
event, an existing configuration read. Name the seam you chose and what already
crosses it.

### Pass state, not ownership

When a new component needs a decision that another component owns, pass a
**read-only predicate or a value**, never the owning object. Handing over the
owner hands over the ability to mutate it, which is how a second authority is
born without anyone deciding to create one.

Keep state local: per call, per turn, per run. Global mutable state needs an
explicit justification, and almost always has a local alternative.

### Flags

When behaviour is optional, the flag contract is:

```text
OFF -> previous behaviour, exactly
ON  -> new behaviour
```

Two rules that are easy to get wrong:

- **Read a flag once, at one site.** A flag read in two places can disagree with
  itself.
- **Gate anything that adds a record to every caller's log.** A record that
  appears unconditionally in every run's output is a behaviour change, even if
  nothing consumes it. Existing callers' logs are an interface.

### Backwards compatibility has two sides

Adding an optional parameter is compatible at the **call site** and incompatible
at the **implementation**. Every alternative implementation of that interface —
test doubles, adapters, other backends, other language bindings — pays for it.
Pass a new optional argument **only when it is actually needed**, so the default
path emits the old call unchanged. Focused tests that all drive the real
implementation cannot catch this; only the full suite can.

### Bounded by construction

If the change introduces a retry, replan, recovery, recursion, poll or
intervention, the bound is part of the implementation, not a follow-up. Then
check the interaction: **a bound must not be able to turn its own honest
surrender into a harder failure.** If reaching the bound at the edge of another
limit converts a clean outcome into a fatal one, the bound is wrong.

### Model and user output is a proposal

Text produced by a model or supplied by a user is input to a decision, never the
decision. It must not silently acquire authority over permissions, completion,
routing, retry counts, escalation or safety. If the change makes generated
content authoritative over anything, stop and get that authority granted
explicitly.

### No opportunistic refactoring

Fix only what blocks the phase. Everything else discovered gets recorded with a
reason and a deferral tripwire. Unrelated repairs in the same change make the
regression unattributable and the review impossible.

## Tripwires

A tripwire is an executable assertion that fails the moment a boundary the
change depends on is violated. **Documented deferrals and protected authorities
should be asserted, not described** — a gap in a comment is not a gap anyone
closes.

Add tripwires for:

| Boundary | Tripwire asserts |
|---|---|
| **Authority bypass** | the gate is consulted *before* the thing it protects, in source order |
| **Unexpected consumer** | only the intended module may reference the authority's internals |
| **Unexpected producer** | only one module produces the decision |
| **Event-sequence drift** | the durable record's shape/order is pinned |
| **Default behaviour change** | with the flag off, the call path is unchanged |
| **Forbidden coupling** | the protected module does not import the new one |
| **Global mutable state** | the new state is a local, not an attribute |
| **Accidental activation** | the new path is unreachable while the flag is off |
| **Persistence drift** | the durable record still carries every input the decision needs |
| **Replay disagreement** | reconstructing from the record reproduces the live decision |

Prefer **AST-based** ratchets over text searches: a whole-file text search
matches the comment that *describes* the problem — including your own docstring —
and reports a false failure. Pin the property, not a string.

**And a check that a location EXISTS is not a check that the right thing is there.** A derived
document's pin guard asserted that each `path:line` pin pointed at a *non-blank* line. When the pinned
file moved, the guard caught **one** of **five** stale pins and passed four that had landed on other,
non-blank lines — so the guard's name (*"every pin names a real line"*) was stronger than its check.
Verify the **content** at the location, not its existence:

```python
assert line_no <= len(lines) and lines[line_no - 1].strip()   # the weak form
assert EXPECTED in lines[line_no - 1]                         # the check that holds the claim
```

The same shape appears wherever an index points at a place: a manifest entry, a registry row, a
changelog reference, a `path:line` citation. When a location moves, only the content check notices.

Patterns and code shapes are in `references/tripwire-catalogue.md`.

## Verification

Run every category that applies, and say which you skipped and why:

```text
STANDARD     the new behaviour does what the contract says
BOUNDARY     the edges of the bound: at the limit, past it, exactly one left
CONFLICT     every row of the conflict matrix, including the ones you expect to lose
FAILURE      each input absent / unavailable / raising / stale / duplicated
REPLAY       reconstruct from durable state alone and compare with the live run
CONCURRENCY  two instances cannot see each other's state
REGRESSION   the full suite against a stable baseline, as a SET
PROSE-ONLY   the change really is documentation: no compiled byte moved
```

Three habits that catch what the others miss:

- **Test the defect, not the feature.** A test that fails on the old code is worth
  more than one that passes on the new. Prefer asserting several facts together —
  a single-property test passes for the wrong reason far more often.
- **Drive a production entry point.** A mechanism reachable only from its own
  test file is not delivered. If the wiring is deferred, assert the deferral.
- **When a test passes alone but fails in a batch, that is a finding about the
  test** — usually shared state outside the process. Find the state, not the
  symptom.

### Regression is a set, not a count

A single run is not a baseline. Build the **stable set** — the intersection of
two runs — and after the change require **both** directions to be empty:

```bash
comm -13 baseline-stable.txt after.txt   # NEW failures      -> must be empty
comm -23 baseline-stable.txt after.txt   # now passing       -> explain each
```

The second direction matters: something that stopped failing is also a change you
should be able to attribute. Compare as **sets**, not with a line-walking diff
across differently-sorted files.

Report the **set**, never "the suite passes". When the set moves, run the
decisive experiment rather than reasoning: re-run excluding your new tests and
diff the sets again. That separates "my change caused it" from "the count is
unstable" in one command.

**If a full run cannot complete** — resource limits, a mid-run kill, no summary —
say so, split the run into chunks, union the results, and state that the method
was weaker than the two-run intersection. A weaker method that is declared is
evidence; a stronger method that is claimed and not performed is not.

### A documentation-only change is a behavioural claim

*"This change is documentation"* is a claim about compiled behaviour, and it is
usually made by inspection — the instrument that fails silently. A stray
re-indentation, a moved statement or a deleted line inside a function is
invisible in a prose diff and is a real change.

Prove it with `scripts/prove_prose_only.py`, which runs two instruments that
must both agree:

- **docstring-stripped AST equality** — delete every module/class/function
  docstring and compare the AST dump. Comments never reach the AST, so they are
  covered for free.
- **recursive bytecode equality** — compile both revisions and compare every
  code object's instruction stream. Docstring *text* lives in the code object's
  constant table and never appears in its instructions, so a text-only change
  moves no byte.

Then check the prose is **true**. A docstring stating the wrong rule is worse
than no docstring: drive the mechanism it describes and assert each sentence it
makes. Documentation describing a behaviour the code does not have is a defect
the phase introduced, and no test will catch it.

Two scope habits this category forces:

- **A stale docstring is often fixable while a behavioural fix is not.** The
  authority that permitted the documentation change usually forbids the code
  change, even when the documentation is stale *because* the code is wrong.
  Correct the prose, record the code defect, do not fix it here.
- **Declare any surface you touched beyond the ones your authority enumerated.**
  An unlisted fourth edit that silently joins a list of three is how a scope
  boundary erodes — and a boundary nobody can see eroding is not a boundary.

## Output

- `assets/templates/IMPLEMENTATION_PLAN.md` — the seam, the flag, the bound, the
  non-goals.
- `assets/templates/VERIFICATION_MATRIX.md` — one row per category and case.
- `assets/templates/PHASE_REPORT.md` — what landed, what did not, the honest
  limits, the regression set.

All three live in the `architecture-evolution` skill directory.

Work these checklists in order; each is a gate, not reading:

`references/checklists/pre-implementation.md` ->
`references/checklists/adversarial-testing.md` ->
`references/checklists/replay-durability.md` (when a durable decision is involved) ->
`references/checklists/concurrency.md` (when state, a bound or a callback is involved) ->
`references/checklists/authority-verification.md` ->
`references/checklists/phase-completion.md`

Two things a phase report must contain, because their absence is the most common
way a report misleads: **the honest limits** (what is built but not yet reached
from the live path) and **the deferrals with their tripwires**.

## Failure modes this skill exists to prevent

| Symptom | Cause |
|---|---|
| Twelve unrelated tests broke on an "optional" parameter | only the call site was considered |
| A subsystem built that nothing calls | reachability treated as an intention |
| An unbounded loop in production | bound deferred to a follow-up |
| A ratchet that fails on its own documentation | text search instead of AST |
| A decision that changes after a restart | transient state persisted as an input |
| Two components disagreeing about one input | a second authority created |
| "0 new failures" that moved next session | baseline was a single run |
| A report that reads better than the code | limits not stated |
| A latent defect appears the moment a broken dependency is restored | the defect was **masked**, not absent |
| A rule applied to every case changed a branch it was never about | the component dispatches by *kind*, and only one kind reads what you changed |

### Scope a rule to the branch that reads it

When the fix is a **rule about inputs** — "only a successful result may be used", "never
forward a refusal" — the tempting shape is to apply it uniformly at the boundary. Before
you do, check how the consumer *dispatches*:

```text
consumer branches on a KIND (a name, a type, a status)
    one branch READS the payload        <- your rule is about this branch
    other branches only RECORD the event <- their payload is inert
```

Applying the rule uniformly changes the *other* branches too — usually by skipping an
event they were counting, which shifts a counter, a floor, or a state that a later
decision reads. That is a semantic change smuggled in under a defect fix.

Two habits that keep the blast radius honest:

- **Scope the rule to the branch whose payload is read**, and read the branch's own
  declaration from the owner (import the set it dispatches on) rather than copying it —
  a copy is a second authority, and it drifts.
- **Write a test for the branch you are deliberately *not* changing.** Its only job is to
  fail if a later edit widens the rule. Without it, "I scoped this narrowly" is a claim
  with no tripwire.

The rule's *purpose* is what must hold everywhere; its *mechanism* need only hold where
the payload is consumed. Say which one you scoped, in the report, with the test that
pins it.

### Stratify before you conclude from a rate

A rate is only as meaningful as the population it was measured over, and the thing
that varies across that population is often **not** the system you are measuring.

```text
measure the rate
    -> split it by every factor that could drive it (the model, the client, the task
       class, the configuration)
        -> if the split rows disagree wildly, the headline number is an artefact
```

Two traps this catches:

- **A degenerate 100%.** A rate pinned at one end usually means the population could
  not exercise the mechanism at all — nothing reached the code path under test, so
  every observation took the same shortcut. Check *how many observations actually
  reached the path* before quoting the rate. "N turns, and zero of them performed the
  operation" is a statement about the population, not about the system.
- **A single-cause attribution.** If one factor flips the rate from 60% to 100%, then
  the rate is a joint property of your system *and* that factor, and reporting it
  unstratified misrepresents both.

**State the mix alongside the number, always.** And when the definition you are
satisfying does not ask for the mix, say so — that is a finding about the definition,
not a reason to omit it.

### A double must take the same path as production, including the cleanup

The most expensive kind of green test is one whose double enters the code by a
different door than production does. It passes, it looks like coverage, and it
hides the exact defect it exists to catch.

The specific shape to watch for is **resource lifetime**:

```text
production   obj = open(...)          double   raises from the OPEN call
             try:                            (so the body never runs)
                 use(obj)                    -> any bug inside the body is unreachable
             finally:
                 obj.close()
```

If the real failure is raised *inside* the guarded region — a status check, a
validation, a `raise_for_status()` — then a double that raises from the *opening
call* never enters that region at all. Everything downstream of it is untested,
including the capture and the cleanup.

**So: make the double return, and let it fail where production fails.**

```text
return a fake object  ->  the body runs  ->  the failure happens in the right place
raise from the call   ->  the body never runs
```

Two habits that catch this:

- **Assert the cleanup happened.** If production closes a resource, assert the
  double was closed. A test that never observes the close is not exercising the
  lifetime — and a captured-then-closed value is exactly where "the body is empty
  by the time you read it" defects live.
- **When a fix works against your fixture but not against the real thing, the
  fixture is the suspect.** That asymmetry is the signal. Re-read the production
  control flow and find where the double diverges; do not patch the real code to
  satisfy the double.

**A faithful double can still be paired with an expectation that encodes the bug.**
This is the second, quieter form of the same disease, and it survives even when the
double enters by the right door:

- One test drives the path with the representation **production actually produces**
  and asserts the *failure* — with a comment calling it "honest error". It passes.
- Another test drives the same path with a **convenient** representation and asserts
  the *intended* behaviour. It passes too.

**Two tests, opposite outcomes, one code path, both green.** The suite then documents
the defect while appearing to cover it. The tell is that the *fixture is the only
variable* — so the question to ask is not "is this test faithful?" but **"which
representation does production actually emit at this point, and does any test assert
the intended behaviour with *that* one?"** If the answer is no, you have no coverage,
however many green tests point at the code.

Corollary: an `except Exception` that swallows a failure makes this undetectable, because
it collapses *"the dependency failed"* and *"I cannot read the dependency's output"* into
one observable. When you see a broad handler on a consumer, check whether two distinct
causes share a single symptom — that is where a defect hides from a green suite.

### A repair that restores a capability exposes what its absence was hiding

When the thing you are fixing is a *blocked path* — a missing dependency, a disabled
flag, an unreachable branch — the code behind it has not been executing. Any defect
downstream of the block has been invisible for as long as the block has existed, and
the repair will surface it. Expect it, and budget for it.

Two consequences:

- **Classify every newly exposed failure** as a *new* defect, a *pre-existing defect
  now reachable*, or a *test that assumed the broken environment*. The three need
  different responses; the middle one is the common case and the easiest to misread
  as a regression you caused.
- **Do not repair what the repair exposed**, unless it is inside the authority you
  were granted. A provisioning change is not a licence to fix the verification logic
  it revealed. Report it with a reproduction and let it get its own decision.

**The attribution of "pre-existing" failures deserves suspicion.** A failure set
recorded as pre-existing is a claim that it is unrelated to your block — and it was
measured *while the block was in place*, which is exactly when the block's own effects
are indistinguishable from everything else. Re-measure before quoting it; a repair
that turns 24 "pre-existing" failures green has disproved the attribution, not
moved a baseline.

### When the fix lands, expect the failing set to be the bug's own documentation

A defect that survived a green suite survived because **something asserted its
symptom as the contract**. So when you fix it, the tests that go red are usually not
collateral damage — they are the record of the bug, and each one needs its *purpose*
read before it is touched.

Classify every newly-red test as one of:

```text
PINNED THE DEFECT      the assertion IS the bug ("… so it cannot see them: honest error")
DEPENDED ON THE DEFECT the property only held BECAUSE of the bug
GENUINELY BROKEN       your change actually broke a real contract
```

Only the third is a regression. The first two must be rewritten to assert the
*intended* behaviour — and the rewrite must preserve whatever else the test existed
for. Read the class it lives in and the neighbouring tests: the test's name is often
about something else entirely (terminal uniqueness, success derivation) and the defect
assertion was incidental.

**Do not delete a failing test to make the suite green, and do not weaken it to fit
the new output.** If a test asserted "this turn is not a success", and after your fix
the turn *is* recorded as a success, you have found a semantic question, not a test
that needs updating. Say so, pin the current behaviour explicitly, and hand it to the
authority that owns it.

#### Prove "pre-existing" by showing the changed path is byte-identical

When a side effect appears that your change *might* have caused, do not argue from
plausibility. Find the value your code hands to the suspect component and compare it
to what the old wiring handed it:

```python
old_input = old_path(event)                      # what the consumer used to receive
new_input = passthrough(new_path(event))         # what it receives now
assert old_input == new_input                    # then behaviour cannot have moved
```

If they are equal for every input class, the side effect is provably not yours — and
you have the evidence in hand rather than a claim. Then go one step further and show
the *other* branch already behaved that way: if the untouched dict-provider path
already produced the new outcome, the old expectation was an artifact of the path you
fixed, and the property never held universally.

### A falsified probe is a hypothesis, not a finding

An adversarial probe that "succeeds" in breaking the system is the most exciting result you can get —
and the one most likely to be wrong. **Before reporting it, re-derive the expectation from the
contract.** The probe encodes your model of the system; when probe and code disagree, either can be
the one that is wrong, and you built the probe.

The failure mode, concretely: a probe asserted *"a fatal provider error must make the turn
unsuccessful"* and reported the turn as successful. The probe had emitted the fatal error **after** the
terminal marker. The contract says post-terminal bytes cannot retroactively fail a completed stream —
so the error was never a turn-level error at all. **The code was right; the probe was measuring its own
misconception.**

So when a probe falsifies:

1. **Re-derive the expected value from the documented contract**, not from intuition. Quote the rule.
2. **Check the probe's INPUT SHAPE first.** Ordering, position, and which side of a boundary the input
   sits on are where probes go wrong far more often than the code does.
3. **Ask what the system would have to do for the probe to be right** — and whether any existing test
   or ADR says otherwise. If one does, the probe is the thing to fix.
4. **Then convert the misunderstanding into a probe of its own.** The corrected case usually reveals a
   *rule worth pinning*: here, "a post-terminal error does not fail the turn" became its own assertion.
   A falsified probe is often the cheapest way to discover which invariants were never written down.

Corollary for the report: **say a probe was corrected, and why.** A falsification count that quietly
drops from 1 to 0 reads like a claim that nothing was ever wrong. The correction is the evidence that
the adversarial pass was real.

The mirror-image discipline applies to a probe that *passes*: a probe that cannot fail is not evidence.
Before trusting a green adversarial run, confirm at least one probe in the set would have failed against
the pre-change code — otherwise you have measured nothing.

### The probe harness lies to you too

A mutation-probe harness has its own failure mode, and it produces the most confusing possible output:
**the control run fails on a tree that is byte-identical to a green one.**

The cause is bytecode. A probe that mutates and restores in the same second, with a mutation that is
**size-preserving** (swapping two lines, changing one token), leaves `__pycache__/*.pyc` holding the
*compiled mutated* source. The interpreter's staleness check is (mtime, size) — both still match after the
restore — so the next run imports the **mutated** bytecode from a **restored** file.

```python
def purge():
    for f in glob.glob(f"{PKG}/**/__pycache__/*.pyc", recursive=True):
        os.unlink(f)

probe(); purge(); rc = run()          # mutate
restore(); purge(); rc = run()        # restore — purge BOTH sides
assert sha256(target) == before       # byte-identical, or the probe is void
```

**A size-preserving mutation is the one to watch for**: a probe that adds a line changes the size and
invalidates the cache by accident, so the trap only springs on the edits that look most innocuous.

Two habits:

- **Purge on both sides of every probe, and assert byte-identity after each restore.** "I restored it" is
  a claim; the hash is the evidence.
- **Run the control *before and after* the probe set.** A control that only runs first cannot detect a
  harness that corrupted the tree on its way out. When the two controls disagree, the **harness** is the
  suspect — not the code under test.

This is the same class the rest of this section describes — the instrument reporting its own defect as
the subject's — arriving one level up: not the double, the *probe runner*.

### A guard that pins a STATE, not a PROPERTY

The most expensive guard is one that fires on a harmless change and stays silent on the harmful one. It
looks like coverage, it trains the reader to edit it rather than read it, and it is the same defect every
time: **it asserts a fact about the code as it was, instead of the property it claims to defend.**

Three instances, all found by probes rather than by reading, and all in one repository:

```python
# 1. A derived document's guard, asserting a PHRASE the page used to contain
assert "could not pin" in page_text          # true only until the page is corrected
# -> went red when the page was REGENERATED correctly. The property is
#    "every finding carries a disposition", not "this sentence exists".

# 2. A no-channel tripwire, asserting parameter set EQUALITY
assert set(sig.parameters) == {"goal", "workspace", "baseline", "strict"}
# -> tripped on a new `use_declaration: bool`. A boolean switch cannot carry
#    criteria; the property is "nothing here CAN carry criteria".

# 3. The same tripwire, rewritten as an allow-list
assert set(sig.parameters) <= ALLOWED        # -> tripped on a harmless `verbose: bool`
#    WORSE than (2): it now fires on every innocuous addition, so the reader
#    extends the list without thinking and the check becomes decoration.
```

**The test to apply:** *if this guard fires, is the thing it names actually broken?* If the answer is
"no, I just have to update the guard", the guard is pinning a state. Rewrite it to assert the property —
and give it a **positive control**: a mutation that must stay green, alongside the ones that must go red.

```python
assert criteria_types.search(ann)   # fires on a real channel  -> must be CAUGHT
assert not channel_name.search(name) or ann in scalars
probe("a criteria-typed parameter", ..., want_caught=True)
probe("a harmless scalar switch",  ..., want_caught=False)   # <- the control
```

**And probe the FIXTURE, not only the code.** A test can pass for a reason unrelated to its claim because
its fixture does not exercise the mechanism. A declared-path test asserted "the criterion is required on
a red baseline" — but the baseline was keyed to a *different* criteria id, so the branch under test was
never reached and the assertion was satisfied by a default. Un-promoting the criterion was invisible:

```python
baseline = {"verify:cmd0": {...}}       # the criterion under test is `declared:cmd0`
# -> `base is None` -> required regardless of promote_absolute
# -> the test passed for a reason unrelated to its claim, and the mutation that
#    would have reopened the defect kept the suite green
```

So when a probe does not falsify, the first suspect is **the input the test supplies**, not the code.
Give the fixture the id, the shape and the position the mechanism actually reads.

**And a helper that applies the rule itself is a fixture that bypasses it.** A whole-path test built a
`_publish(core, failure, *, capability: bool)` helper that set the capability flag from its *argument* —
so the predicate the production sites actually call was never exercised, and the probe that broke that
predicate **did not falsify**:

```python
# the helper took the ANSWER as a parameter -> the rule was replicated, not driven
def _publish(core, failure, *, capability: bool):
    if capability: tc["_capability"] = True       # <- the predicate never runs
    else:          tc["_denial"] = "SCHEMA_INVALID"

# fixed: apply the REAL rule, so breaking it breaks the test
def _publish(core, failure):
    if _is_capability_failure(failure): tc["_capability"] = True
    else:                               tc["_denial"] = "SCHEMA_INVALID"
```

The tell is a **boolean parameter that mirrors a branch under test**. If a helper can be handed the
outcome it is supposed to derive, the branch is not covered however many green tests point at it — and
the non-vacuity probe is the only thing that will say so. Record the defect in the helper's own
docstring, or the next reader "simplifies" the signature back.

**And a probe that breaks one instance of many has not broken the thing.** A guard asserted that an
index's entries resolved to real content; the probe that was supposed to falsify it reformatted the
index's pin syntax — but with `text.replace(old, new, 1)`, so **one of thirty-seven** entries changed and
the guard correctly passed. The probe reported `MISSED`, which reads as "the guard is weak" and was in
fact "the probe is weak".

```python
text.replace(old, new, 1)     # one entry  -> a per-entry check stays green
text.replace(old, new, -1)    # every entry -> the floor and the check both fire
```

Before reporting a non-falsifying probe as a gap in the subject, check **how many** instances the probe
actually reached. A collection-wide break needs a collection-wide edit, and the count belongs in the
probe's output, not in your head.

### A comparison is only about what it actually drives

A ratchet can be green, non-vacuous, well-named, and still be about the wrong two things. The tell is a
table whose **name** and whose **subject** have drifted apart.

A parity ratchet compared `authorize()` to `SecurityPolicy.check()` and described the result as *"the REST
gate is weaker than the agent path"*. Both are real functions; neither is a path. The agent's actual gate
chain read its approval requirement from a **third** set (`_get_write_tools`, derived from a risk table)
and **discarded** the `approval_required` field the ratchet was comparing — a field consumed by a
different agent surface entirely. Driven against the two **paths** the table named, the divergence was
**0 of 36**; the six "divergences" were between two models, on three action names the agent **cannot
execute at all** (no tool implementation, no risk-table row — `risk_for_tool` returned a fail-closed
default, which is not a row). Three phases of a security finding rested on it.

```python
# the ratchet: two MODELS, named as two PATHS
authorize(...)                        vs  SecurityPolicy(...).check(...)
# the paths it claimed to be about — never driven until asked:
ToolExecutor.execute(..., approval_handler=None)   vs  require_tool_allowed(...)
```

**The test to apply:** *does the comparison drive the two things its name says?* If it drives
representatives, helpers, or models, say so in the name — or drive the real thing. And when a field is
compared, check that something **reads** it: a computed value with no consumer is a fact about the
function that computes it, not about the system.

**And a count is canonical only if there is one block.** The same repository carried two "canonical
suite" command blocks — in the agent guide and in the handoff — that listed **different file sets** (43 vs
41). Both headings quoted a count; neither described the intersection the method calls for. Two copies of
a ratcheted invariant are a divergence waiting to be found, so make the blocks identical **and** derive
each heading's count from a run of that block.

**Re-measure after the LAST change, not the change that motivated measuring.** A count is a fact about a
moment. The same repository set a block's heading in one deliverable, then added **four** tests to a file
the block names in the next — so the heading was stale before the mission that wrote it had ended, and the
block reported 1316 where both headings said 1312. *One block* and *measured after the last edit to any
member* are two rules that compose; neither is enough alone.

### A measurement's CONDITION is part of its result

A number can be exactly right and still be quoted for a claim it does not support, because the **state it
was measured in** travelled with it as an unstated premise. One repository carried *"0 divergences of 36"*
for three missions as a property of the two **paths**; it was a property of the paths **with no policy
loaded**, and the field it would have diverged on was inert on both sides. A later landing turned that
field live on one path only — and did not re-measure, because the number it would have contradicted was
already "known". Driven, **11 of 36** pairs had diverged, five in the **default** mode.

**The rule:** when you record a measurement, record the **configuration** it was taken under — flags,
loaded state, mode, environment — and when a change makes any of those reachable, the old number is
**stale**, not a baseline. A count's staleness is visible by re-running it; a *behavioural* measurement's
staleness is only visible if you wrote down what it was conditioned on.

### Observe the production path, not a copy of it

A guard can name the right property, drive the right two things, and still be unable to fail — because it
**reconstructs** the production call beside the code instead of invoking it. One test computed the value
the production function *would* compute and compared it with the other side; mutating the production code
to pass something else left it green. The non-vacuity probe caught it, and only the probe could have: the
test's assertions were all correct.

**The test to apply:** mutate the production line the guard is about and confirm the guard fails. If it
does not, the guard is reading a **copy** — its subject is right and its **observation point** is wrong.
This is the sub-case above one level down, and it is the reason *"does this probe falsify?"* has to be
asked of the probe that is already passing.

**And when you instrument a live path, print the container's KEYS before reading a field out of it.**
Instrumenting a real request to check "is the prompt on the wire?" produced two confident findings in a
row that were both **mine**, not the code's:

- *"the system prompt is 258 characters"* — the recorder read `messages[role == "system"]`, found nothing,
  and the fallback printed an unrelated error string from an earlier run. The number was real; the subject
  was not.
- *"there is no system message at all"* — same recorder, same blind spot. The prompt was in the payload's
  **top-level `system` field** (Ollama's `/api/chat` shape), which a `messages`-only view cannot see.

`sorted(payload.keys())` settled it in one line and showed the prompt was there all along, 18,309 chars
and correct. **An instrument that reports a wrong subject is the defect class this whole section is about —
and it does not spare the instrument you wrote five minutes ago.** Print the shape before reading the
field.

### A red guard is a question, not a verdict — decide which side is wrong first

A pinned test that fails has two possible repairs, and they are opposites. The tell is **what the test
asserts**:

- it encodes a **broken environment** as the contract (it passes only because a dependency was missing) →
  **the test is wrong**; fix the test, and say why inside it;
- it asserts a **canonical rule the code violates** → **the code is wrong**; the test is doing its job and
  needs no change.

The reflex — *"a pinned test changed, so it is a contract update"* — is wrong about half the time, and
when it is wrong it silently legalises a live violation. Measured: of two red guards recorded in one
table, one was each. **And a rule about a class must not be inferred from one instance** — that same table
classified both guards from the evidence of one, which is the section above's defect (*a comparison is
only about what it actually drives*) one level up.

**Drive the claim, not just the code.** A document asserting *"this table lists every commit"* is a claim
with a checker: diff the table against `git log`. Nine commits were missing from one that had said so for
four phases. Completeness claims are cheap to verify and expensive to trust — the same move as driving a
probe, applied to prose.

**And verify an identifier before you use it.** A brief named an ADR number for the decision it
commissioned; the corpus had one fewer ADR than the brief assumed, because the previous mission had
explicitly produced none — so the number it named was **free, not taken**, and following it would have left
a permanent hole a reader would hunt for. An identifier — an ADR number, a file path, a flag name, a line
number — is a claim about the corpus, and it is checkable in one command. Check it.

**And a residual is not a handoff.** Every decision record in a mature corpus ends with a *"residuals,
named"* list, and every entry is true — which is exactly why they are trusted and exactly why they go
stale. One carried a live, security-relevant item (*"this prompt has never rendered"*) for four missions
**inside the decision document only**: the project's open-items table never listed it, because nothing
moves a residual into it. A residuals list is a **record of what the decision left undone**; the open-items
table is **what the next person is told to work on**. They are two records of one thing, and no mechanism
compares them — the same shape as two "canonical" blocks, or two rows describing one finding's status.

**The test to apply:** for each residual in a decision you land, ask *which live table carries this?* If the
answer is "the decision document", it is not yet a handoff.

### A guard must fail for the reason it names

A tripwire is a guard written to fire when a *specific* thing changes. Two ways it goes wrong — both found
by running the guard against a **legitimate addition**, not against a violation:

**It pins a state, not a property.** One test asserted an exact **set** of callers
(`{the CLI, one named test file}`); adding a second test file that drove the same function fired it,
though the test's own docstring already said tests were expected. Another scanned for a **bare name**
(`policy_pubkey=`) and matched an unrelated, same-named **config setting** rather than the route attribute
it was about. **The tell is in the failure message: if it does not describe the thing that changed, the
guard is measuring a proxy.** Repair by stating the rule — *the CLI, and tests, nothing else* — and probe
it **in both directions**: a real violation must be CAUGHT, and the legitimate addition must be MISSED.

**Its assertion is satisfied by a different failure of the same type.** `pytest.raises(ValueError)` on a
loader does not falsify its own claim when some *other* path in the same call also raises `ValueError`: the
non-vacuity probe disabled the check under test and the test **still passed**, satisfied by an unrelated
`ValueError` from a later line. **Pin the message** (`match="signature invalid"`) whenever a second path
can raise the same type. The probe did not falsify the code — it falsified the *test*, which is the
finding.

### A stated capability is a claim, not a premise

A design document that says *"X already does Y"* is making a claim, and the claim is about a **path**.
Design on it without driving it and the whole change is built on an unmeasured sentence. This is the
same defect as the section above, applied to **prose** rather than to an instrument — and it is the more
expensive version, because the document reads as *context*, and context is what you trust instead of
checking.

The case: a decision record and a mission brief both said the WebSocket channel *"already implements
bidirectional approval flow"*. Half true, and the false half decided the shape of the whole feature:

| direction | server emits / reads | both shipped clients emit / read | match |
|---|---|---|---|
| server → client | `approval_request` · `{approval_id, tool_call}` | `tool_approval_request` · `{call_id, name, arguments, reason}` | **no** |
| client → server | `tool_approval` · `{approved}` | `tool_approval` · `{id, approved, reason?}` | yes |

The **answer** direction worked. The **question** direction emitted a frame **no client branched on** —
so the prompt had *never rendered*, and the existing path had been silently timing out to deny for its
whole life. "Wire the new feature into the existing channel" was impossible as written; and once measured,
the useful discovery was the reverse: **the clients' vocabulary was already the de-facto contract**, so
adopting *it* needed **no client change at all**.

**The test to apply:** *before designing on a stated capability, drive the capability.* Two greps and one
`grep` for the message type across the clients would have found this in minutes; instead it had been
carried as context for three phases. When the claim involves two sides, check **both** directions — a
protocol is bidirectional and is very often wired one way.

**And a block that cannot collect has no count.** A command block in the handoff quoted *"245 passed"* for
a set of files. It **aborted at collection** — two of its files imported a library that is not installed —
so the number had never been producible in that environment. Before trusting a quoted count, **run the
block**; if it errors at collection, the count is not stale, it is **fictional**. And when you repair it,
repair it by **adding** the reason, not by removing the failing file: dropping a red test from a block is
the weakening that the repair exists to prevent.
