# Tripwire catalogue

Patterns for `reliability-phase-engineering`. A tripwire is an executable
assertion that fails the moment a boundary the change depends on is violated.

**Why AST and not text.** A whole-file text search matches the comment that
*describes* the problem — including your own docstring. A ratchet written as a
substring search will fail on its own documentation, and the temptation is then
to weaken it. Pin the property, not a string.

## 1. Single producer

Only one module may **produce** the decision. Allow readers.

```python
import ast
tree = ast.parse(Path("src/core/engine.py").read_text())
targets = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
           for t in n.targets if isinstance(t, ast.Name)}
assert "the_decision" in targets          # non-vacuous: it must exist somewhere
# ...and nowhere else in the tree
```

Include the **non-vacuity** assertion. A ratchet that passes when the pattern has
vanished entirely is worse than none.

## 2. Ordering (a gate cannot be bypassed)

The claim "the existing gate is consulted first" is unobservable at runtime when
the gate cannot be triggered in the test environment. Assert the source order.

```python
loop = the_iteration_loop_ast()
guard = [i for i, s in enumerate(loop.body) if calls(s, "existing_gate")]
new   = [i for i, s in enumerate(loop.body) if calls(s, "new_gate")]
assert max(guard) < min(new)
# and: the existing gate's branch must short-circuit
assert any(isinstance(n, ast.Continue) for n in ast.walk(existing_branch))
```

## 3. Forbidden coupling

The new component must not import the authority's internals.

```python
imported = {a.name for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.ImportFrom) and n.module == "the.authority"}
assert imported <= {"the_message_type"}
```

Stronger: assert that no attribute of the authority is touched at all.

```python
touched = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
assert not (touched & {"internal_field", "internal_method"})
```

## 4. Local, not global

```python
stored = {t.id for n in ast.walk(tree) if isinstance(n, ast.Name)
          and isinstance(n.ctx, ast.Store) for t in [n]}
assert "the_counter" in stored
attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
assert "the_counter" not in attrs        # it must not be an attribute
```

## 5. Default path unchanged

Assert that the new argument is passed **only** when needed, so the default call
is the old call:

```python
assert "new_arg=" in src
assert src.count("new_arg=") == 1        # one site, conditional
```

And behaviourally: drive the default path and assert the observable transcript is
identical to the pre-change expectation.

## 6. Deferral tripwire

For work deliberately not done, assert that it is still not done — and make the
failure message name the report section explaining why.

```python
def test_the_ladder_is_not_consulted_yet():
    """Deferred: see PHASE_REPORT.md section 14. Wiring this changes control
    flow and needs its own decision."""
    assert not calls_anywhere("ladder.decide")
```

When the deferral is later implemented, **invert** the tripwire in the same
change. A tripwire left pointing the wrong way is a lie.

## 7. Record-shape ratchet

Pin the durable record's required inputs, so a later refactor cannot quietly drop
one:

```python
assert {"terminal_outcome", "verdict", "predicate"} <= set(record)
```

Add a **totality** check where the record's vocabulary is an enumeration: an
audit-kind list, a set of state names. The guard's job is to force a deliberate
decision when the set grows — it should fail with a message saying "declare the
new kind", not "update the expected value".

## 8. Field classification instead of a name blacklist

To forbid payload on a structure, classify **every** field rather than listing
forbidden names. A name blacklist is evadable by naming (`body`, `payload`,
`blob`) and it also forbids legitimate new fields by accident.

```python
KINDS = {"deps": REFERENCE, "work_unit": REFERENCE, "status": STATE}
assert {f.name for f in fields(Node)} == set(KINDS)   # total
assert not [f for f, k in KINDS.items() if k is PAYLOAD]  # declared, empty
```

## 9. Replay agreement

```python
def test_replay_equals_live(record):
    assert reconstruct(record) is derive(**{k: record[k] for k in INPUTS})
```

Drive the case that actually diverges — usually the one where two terms of a
predicate disagree — not just the easy one.

## 10. A derived document is guarded

When several decisions accumulate, the *reasoning* lives in the append-only log
and the *answers* are scattered across a dozen entries — so the next person
re-derives the current state by reading the whole chain, and that reconstruction
is where drift re-enters. The remedy is a **derived page**: one place that states
the current state and cites, rather than re-argues.

A derived page is a second copy of the truth, so it **must be pinned or it will
rot**. Two checks, both mechanical:

```python
# (a) every pin resolves, and every pin is fully qualified
PIN = re.compile(r"`([A-Za-z0-9_./-]+\.py):(\d+)(?:-(\d+))?`")
for path, start, end in PIN.findall(page):
    assert (ROOT / path).exists()          # a bare `progress.py:50` is NOT a pin:
    assert line_is_not_blank(path, start)  # three files may share that name

# (b) the table it reproduces is DRIVEN through the real authority, row by row
for row, kwargs, expected in SELECTORS:    # one selector per row
    assert derive(**kwargs) is expected
```

**The trap that makes this worth a category.** The first version of the guard
checked that each row of the reproduced table *existed* but never that it named
the right **result** — so flipping a row's outcome in the page was **not caught**.
It was found only because a mutation probe was run, and the probe that "passed"
was the finding. *A probe that does not falsify means the test is not testing what
you think.* The result cell must be parsed, not merely the row number.

Two habits:

- **Declare the page regenerated, not edited**, and assert that declaration. A page
  that can be hand-edited has silently become the authority.
- **Record what you could not pin.** A derived page that claims nothing is
  unpinnable is claiming more than it can support; a claim that cannot be pinned
  is a **finding**, not a claim.

## 11. A distinction is observable without widening a signature

When a caller must tell two failure kinds apart but the return type is pinned —
`Optional[str]`, an event payload that must stay JSON-serializable — a `str`
subclass carrying the discriminator preserves **every** existing consumer
(`isinstance(x, str)`, `if x:`, f-strings, `json.dumps`) while adding the one thing
that was missing:

```python
class Failure(str):
    __slots__ = ("kind",)
    kind: str                      # declare it: __slots__ alone is invisible to a checker
    def __new__(cls, message, kind):
        self = super().__new__(cls, message); self.kind = kind; return self
```

A dataclass is the obvious shape and the wrong one: it reaches
`{"status": "error", "data": <object>}` and stops being serializable, and
`f"Blocked: {x}"` renders a repr.

Pair it with a **clause-ordering** tripwire — the specific handler must precede the
broad one, or the broad one swallows it — and an **"otherwise unchanged"** pin for
the surrounding function:

```python
def ast_without_the_fixed_block(source):     # remove ONLY the block you changed
    ...                                       # (comments are not AST nodes, so
    return ast.dump(fn, ...)                  #  annotating the site is free)
assert sha256(ast_without_the_fixed_block(src)) == PRE_CHANGE_DIGEST
```

Check the helper against a mutation **outside** the block, or a helper that
accidentally stripped the whole function would match trivially — *a scanner
reporting "absent" is not evidence of absence* until shown to find what is there.

## 12. A document cites an artefact that must exist

A reference doc makes claims *about* the code: it links a file, names a make
target, cites a decision by number, points at a test as its evidence. Each is a
claim, and each rots silently — the prose stays grammatical while the thing it
points at is renamed or was never written.

Why this is worse than it looks: **a citation reads as evidence.** A dead link is
a dead end the reader notices. A cited *test* that does not exist is a claim of
proof with nothing behind it, and it stops the reader looking. One of those sat
in two documents, on the most load-bearing claim in the design, for the project's
whole life.

Enumerate the citation **kinds** the docs use and check each:

```python
LINK      = re.compile(r"\]\(([^)#\s]+\.md)\)")               # every internal link resolves
TARGET    = re.compile(r"^\s*make ([a-z-]+)|`make ([a-z-]+)")  # exists in the Makefile
DECISION  = re.compile(r"\b0\d{3}\b")                          # inside a link, never bare
TEST_FUNC = re.compile(r"\b(test_[a-z_0-9]+)\b(?!\.py)")       # exists in the suite
TEST_FILE = re.compile(r"\b(test_[a-z_0-9]+)\.py")
```

**Check both directions.** A doc naming something the code *cannot* produce is
worse than a doc missing something it can: the first describes a format that does
not exist, and someone will code against it. "Is everything documented?" — the
obvious check — misses it entirely.

**Prose has no AST**, so this is the one category where text matching is the only
option — which is exactly why the non-vacuity rule below matters more here, not
less. Tie it to a floor on citations *found*: a regex that silently matches
nothing looks exactly like a regex that finds nothing wrong. That floor caught a
missing `re.MULTILINE` in the check itself, which would otherwise have passed
forever.

Two traps, both paid for:

- **Scope the citation to where it is a claim.** A bare `\bmake (\w+)` matches
  "the invariants that *make replay* exact". Require a code block or a backtick.
  A helper named `test_*` is likewise *collected as a test* — it runs, returns
  something, and passes.
- **Exempt the record of mistakes.** A changelog or learning log must be free to
  name the broken thing it reports. Reference docs must resolve every citation; a
  log of errors must not be forced to. The false citation above appeared in both,
  so excluding the log still caught it.

## Rules

- **AST over text.** Always.
- **Non-vacuous.** Assert the pattern exists *and* that it is the only one.
- **Pin the property, not a string.**
- **Never weaken a ratchet to make a change pass.** If a ratchet fires, it is
  usually right; the change is what is wrong.
- **Invert deferral tripwires in the same change** that implements the deferral.
- **A ratchet that cannot fail is decoration.** Break it deliberately once, and
  watch it fail.
