# Phase — Corpus integrity pass

**Repository:** `/Users/philosopher/iCloud Drive (Archive)/Documents/wisp`
**Baseline:** `HEAD` = `e639115` (the ADR-0057 landing). Working tree = the 29 WIP entries of
`CONTEXT.md` §8 — **no drift**.
**Nature:** bounded fixes, **no new ADR**. The point is that the corpus's *claims* become as verifiable as
the code's *behaviour*.

## Dispositions

| item | state | evidence |
|---|---|---|
| **2.1** — the protected-path guard cannot run here | **OPEN** — `httpx` is not in the uv cache, not in `.venv`, and **not anywhere on this host**. The corpus's counts are corrected instead | measured below |
| **2.2** — the comment that does not describe its branch | **CLOSED** — the comment now describes the branch it sits in | docstring-stripped AST **identical** to HEAD |
| **2.3** — the derived page's prose drift | **CLOSED** — the pin guard now checks four prose properties, and **caught a real drift on its first run** | 4/4 probes caught |
| **2.4** — the class entry's fourth sub-case | **CLOSED** — §10 names it, with two citations | docstring-only |

---

## 2.1 — OPEN, and the corpus's counts corrected

**Measured.** `httpx` is absent from `.venv`; the offline install from the uv cache fails
(`Because httpx was not found in the cache`); and a filesystem search finds no `httpx` in the litllm
conda env, the 3.12 framework Python, or the managed envs. So the item **cannot be closed** without
network, and the brief's own instruction for that case applies: *"remove '26 passed' from the corpus's
claims about that file until it can be re-measured."*

**Three things the measurement changed.**

1. **It is not one file — it is two, and they break the whole block.** §11's "Phase 10 tests — the full
   set (245 passed)" **aborts at collection**:

   ```
   ERROR tests/test_protected_path_guard.py  - starlette.testclient requires httpx
   ERROR tests/test_server_policy_gate.py    - starlette.testclient requires httpx
   ```

   With those two ignored, the block reports **216 passed, 2 FAILED**. So the heading's **245** was not
   merely stale — it was **not producible in this environment at all**.

2. **The host has 9 `TestClient`-dependent files, not 11.** Measured by AST-scanning `tests/**` for a
   `testclient` import. The "11" was an estimate; §7 now carries the measured figure.

3. **Two Phase-10 guards are red at HEAD, and nothing caught them.** Neither is F38, neither is in the
   canonical block, and both predate the 2026-09-25 chain:

   | Test | Failure | Introduced by |
   |---|---|---|
   | `test_outcome_classification_authority.py::test_no_module_reimplements_tool_result_status_classification` | `wisp/core/stateless.py:158,167` compare `.get("status")` to `"ok"`/`"error"` directly | `ade4dc6` (POST-M13 execution semantics) |
   | `test_m4_governance_wiring.py::test_tool_executor_construction_sites_are_known` | asserts 2 `ToolExecutor` construction sites; there are 3 (the third is `wisp/acp_session.py`) | `cef3e90` |

   **Not repaired.** Both are **contract updates** — exactly F38's class — and the brief is explicit that
   that needs its own authorisation, not a side effect of another phase. Named, dated, and attributed so
   the next reader can act on them deliberately.

**What changed in the corpus.**

| Location | Change |
|---|---|
| `CONTEXT.md` §11's Phase 10 block | the `(245 passed)` heading replaced by the measured reality, the two `--ignore`s, and both failures with their introducing commits. The block now **runs**. `test_m4_governance_wiring.py` was briefly dropped while drafting and **put back** — removing a failing test from a block is the weakening this pass exists to prevent |
| `CONTEXT.md` §0.0.2's verification table | marked a **Phase-10-era snapshot**, with the three rows corrected (ruff is not clean, mypy is not exit 0, the 245 is not producible) |
| `CONTEXT.md` §0c's test inventory | marked a **Phase-10-era inventory**, not a re-measurable claim |
| `CONTEXT.md` §7 | `11` → **`9`**, with how it was measured |
| `PHASE_10_PROTECTED_PATH_GUARD.md` §5 | the `26 passed` row **annotated**, not deleted — it is what that phase measured, in that environment |
| `PHASE_10_AUTHORITY_CLOSURE_IMPLEMENTATION.md` | the same annotation over its counts table |

**The rule applied, and why the phase reports keep their numbers.** F71 says *do not quote a count that
has not been produced in this environment*. A **phase report is a record of what was measured then** —
deleting its number would falsify history, and the corpus's problem was never that Phase 10 lied, it was
that a *live* document kept repeating Phase 10's number as if it were current. So the live documents
(§0, §11, §7) are corrected, and the records are annotated with what changed since.

---

## 2.2 — CLOSED

`wisp/tool_executor.py`'s fall-through carried:

```python
# auto_approve=True + no handler + not forced = pass through
```

inside a branch whose guard is `not auto_approve`. The comment describes a branch this one is not in.
It now states the branch it sits in, names why (`not auto_approve` is already required to get here), and
cites ADR-0055 §3 residual 3 for the measured consequence — plus a parenthetical recording what the old
comment said, so the next reader does not "restore" it.

**Behaviour unchanged, proven not asserted:** the docstring-stripped AST is **identical** to HEAD's
(`True`), while the file grew 623 bytes of comment. This is the PROSE-ONLY category from the skill's own
verification taxonomy.

---

## 2.3 — CLOSED, and it caught a real drift immediately

`CURRENT_AUTHORITIES.md`'s pin guard checked `path:line` pins and nothing else, which is why F81's prose
drift (a superseded disposition for three ADRs) was invisible to it.

**Four properties added**, each parsing a citation or a header field — no meaning is interpreted:

| Property | Fails when |
|---|---|
| every cited `ADR-NNNN` resolves to a `## ADR-NNNN` heading | the page cites an ADR that does not exist |
| no cited ADR's index row begins `SUPERSEDED` | a superseded decision is cited as current |
| the header's `covers **ADR-0001 … ADR-NNNN**` reaches every ADR the page cites | **F81's drift, mechanised** |
| the header's `Generated … at \`<sha>\`` names a commit that exists **and is an ancestor of HEAD** | the header names a typo'd, foreign, or future revision |

**On the third property, the guard failed on its first run — because the drift was still there:**

```
AssertionError: the page cites ADR-0051, ADR-0053, ADR-0054 but its header claims to
cover only up to ADR-0049 — regenerate the header
```

The header had said `ADR-0049` since before ADR-0050 landed, while the page cited three newer ADRs. The
header is regenerated (`e639115`, `… ADR-0057`), and it now records that the range is checked rather than
trusted.

**Why `ancestor`, not `== HEAD`.** A check that the header names *exactly* HEAD would fail on every
subsequent commit — the "pins a state, not a property" nuisance class, three instances already. *Ancestor*
is the property: a header naming a commit outside this history is a typo or a lie about when the page was
generated.

**The reversal condition, honoured.** If any of the four needed to **read for meaning** rather than parse
a citation, it must not be written. None does: all four are regexes over citation syntax and header
fields. That is stated in the guard's own comment block.

**Non-vacuity: 4/4 caught**, each file restored byte-identical:

| # | Mutation | Test it must fail | Result |
|---|---|---|---|
| NV1 | the page cites `ADR-9999` | `test_every_cited_adr_exists` | **CAUGHT** |
| NV2 | the header range shrinks to `ADR-0049` | `test_the_header_range_covers_every_adr_the_page_cites` | **CAUGHT** |
| NV3 | a cited ADR becomes `SUPERSEDED` in the index | `test_no_cited_adr_is_superseded` | **CAUGHT** |
| NV4 | the header names `deadbeef` | `test_the_header_names_a_real_ancestor_commit` | **CAUGHT** |

Floors: the citation set must be non-empty **and** ≥ 20 (it is 23), so the check cannot pass by finding
nothing.

---

## 2.4 — CLOSED

`CONTEXT.md` §10's class was drawn the same way five times: *the instrument does not reproduce the
production control flow, or does not fail when the subject fails, and reports its own defect as a result
about the subject.* All five are **a broken instrument**.

The fourth sub-case is a different and harder shape, and it now has its own entry:

> **The instrument's SUBJECT was the wrong thing.** The instrument worked perfectly and its result was
> sound — it was sound about the wrong thing, and it looked like a fact for three phases.

| Where | The instrument | What it was actually about |
|---|---|---|
| `PHASE_AUTHORIZATION_PARITY.md` §6 (**F78**) | a ratchet comparing `authorize()` to `SecurityPolicy.check()`, calling the first *"the agent's verdict"* | two **models**, not the two **paths** its name, docstring and table all described |
| `PHASE_OBJECTIVE_FLAG_COMPOSITION.md` §6 (**F82**) | two "canonical suite" blocks, each quoting a count | **one of two** blocks, listing different file sets (43 vs 41) |

With the tell — **a table whose name and whose subject have drifted apart** — the added discipline
(*before trusting a comparison, drive the two things its name says; and when a field is compared, check
that something reads it*), and a cross-reference to F83 (the same shape applied to a *claim* rather than
an instrument).

**Docstring-only.** No test, no guard, no code.

---

## Verification

```
the extended pin guard                    34 passed (30 + 4 new)
non-vacuity (2.3)                         4/4 CAUGHT, tree restored byte-identical
2.2 is prose-only                         docstring-stripped AST identical to HEAD: True
regression (48 files)                     1371 tests — 1370 passed, 1 failed (F38, pre-existing)
the repaired §11 Phase-10 block           216 passed, 2 failed (both pre-existing, attributed)
ruff check wisp/                          11 errors — UNCHANGED (F71)
ruff (changed + new files)                clean; the one F401 in tool_executor.py is pre-existing
                                          (verified against the HEAD copy of the same file)
```

`mypy` was **not** re-run — the count is 1844 at HEAD and this pass does not claim it moved.

---

## What is still open, and why

| Item | Why it is open |
|---|---|
| **2.1's install** | `httpx` is in neither the uv cache nor anywhere on this host. Installing it needs network, which this environment does not have. The corpus's counts are corrected instead, which is what the brief prescribes for exactly this case |
| **The two red Phase-10 guards** | Contract updates. Both need explicit authorisation, like F38 — not a side effect of a corpus pass |
| **The agent path's dead WebSocket approval frame** | ADR-0057 residual 1. Reconciling it is a real fix and a real behaviour change on a live path; its own ADR |
| **G3** | `POST /api/hooks` accepting an unvalidated `command`. ADR-0057 R9 defers it as its own ADR |
| **`wisp/core/stateless.py:291-292`, `:1177-1178`** | A guarded import of the **untracked** `wisp/capability_filter.py` (the user's WIP). Guarded by a flag defaulting OFF, so a fresh clone works until the flag is set. Named in §8; not this pass's to fix |
