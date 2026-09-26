# PHASE_CORPUS_INTEGRITY_II — two residual items, one closed and one still environmental

**Baseline:** `HEAD` = `5c129d5` (§0 records `aa47ae0`), tree = 29, ADR 57. No drift. Deliverable 1
landed as **`6ec0f48`**.
**Deliverable:** 2 of 2. **Type:** bounded fixes, **no new ADR**.
**Reports:** this file; `PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md` (Deliverable 1).

---

## 2.1 — the M4 construction-site count — **CLOSED**

### Driven

`tests/test_m4_governance_wiring.py::test_tool_executor_construction_sites_are_known`, as it stood:

```
E  AssertionError: a new ToolExecutor construction site appeared — check whether it
   passes a policy bundle: [('wisp/acp_session.py', 208, False),
                            ('wisp/composition.py', 142, False),
                            ('wisp/benchmark/runner.py', 82, False)]
E  assert 3 == 2
```

Three sites, none passing `policy=`. Blame, per site:

| site | introduced by | date |
|---|---|---|
| `wisp/composition.py:142` | `4519404` | 2026-05-22 |
| `wisp/acp_session.py:208` | `cef3e90` | 2026-08-24 |
| **`wisp/benchmark/runner.py:82`** | **`8a7e9ab`** | 2026-09-25 |

### The brief's claim is wrong on both counts — and so is the corpus's

The brief says: *"There are 3 — the third is `wisp/acp_session.py`. Introduced by `cef3e90`."*
`PHASE_CORPUS_INTEGRITY.md` §2.1 said the same. **Driven, both are false:**

- **`wisp/acp_session.py` is one of the *original two*.** `PHASE_10_M4_GOVERNANCE_UNWIRED.md:46` names
  the pair explicitly: *"**2 sites** — `composition.py:134` and `acp_session.py:208`"*. The site absent
  from the guard's knowledge **and** from the document's inventory is **`wisp/benchmark/runner.py`**.
- **It was introduced by `8a7e9ab`** (the autonomous-convergence chain, ADR-0045/0046), not `cef3e90`.
  `cef3e90` introduced the `acp_session.py` site — the one the document already knew about.

Confirmed by commit order: `git merge-base --is-ancestor ade4dc6 8a7e9ab` → true. The guard was written
by `ade4dc6`; the third site arrived later. So the guard's count was **correct when written** and went
stale at `8a7e9ab`. **The seventh consecutive brief with a wrong specific claim.**

### Was the third site authorised? — **yes**, and that settles the classification

`8a7e9ab` wired an executor into `benchmark/runner.py::make_ollama_core_factory`. **ADR-0045's F54 fix
names it as the fix**:

> *"`CompositionRoot` wires an executor; **`benchmark/runner.py::make_ollama_core_factory` did not** —
> and it is the one place that builds a core by hand. So `wisp bench` refused every `write_file`,
> `edit_file`, `run_bash`, `run_tests` and `spawn`, and had been reporting FAIL for tasks no agent
> could pass … The fix is one line plus two tripwires."*

So the brief's hypothesised finding — *"a production `ToolExecutor` construction site added without a
decision that names it"* — **does not obtain**. The site is authorised, by name, in an ADR. And that
makes this guard's failure a genuine **contract update**, exactly as `PHASE_CORPUS_INTEGRITY.md` §2.1
classified it. (The *other* guard in that table was not — see F86, Deliverable 1.)

### The update

`assert len(sites) == 2` → `assert len(sites) == 3`, with the reasoning recorded **in the test** (not
deleted, not weakened) — it now states what `8a7e9ab` did, why ADR-0045 authorised it, and why the
message says *"check whether it passes a policy bundle"* rather than *"this must never change"*.

**Plus one strengthening.** The count alone cannot distinguish *a fourth site appeared* from *a site was
swapped for another*. The test now also names the three files, so a swap fails too. That is the
inventory's real property; the count is a summary of it.

### The inventory's other assertions — confirmed

| assertion | status |
|---|---|
| the two original sites are the composition root and the tool-executor fallback | **confirmed** — `composition.py:142` is `CompositionRoot.tool_executor`; `acp_session.py:208` is the ACP session's *"config-driven bare executor so permission gating still applies"* |
| **no** site passes `policy=` | **confirmed at all three** — `test_no_tool_executor_is_constructed_with_a_policy` (the M4 tripwire) is green |
| the runtime never imports `wisp.policy` | **confirmed** — `test_the_runtime_never_imports_the_policy_package` is green |
| `ToolExecutor.__init__`'s `policy` parameter still defaults to `None` | **confirmed** |

The document's first row is annotated (a stale line number `:134` → `:142`, and the missing third site)
rather than rewritten — a phase report records what was measured **then**.

---

## 2.2 — the `httpx` environment — **OPEN**

Re-attempted at the start of this mission, per the brief, because a cache could have been populated
since. It has not.

```
$ env -u PYTHONPATH UV_OFFLINE=1 ~/.local/bin/uv pip install --python .venv/bin/python --offline 'httpx>=0.27'
  × No solution found when resolving dependencies:
  ╰─▶ Because httpx was not found in the cache and you require httpx>=0.27, we
      can conclude that your requirements are unsatisfiable.
  hint: Packages were unavailable because the network was disabled.
```

The cache exists and is populated — `archive-v0`, `builds-v0`, `sdists-v9`, `simple-v24`, `wheels-v6` —
and **contains no `httpx` entry of any kind** (`find ~/.cache/uv -iname "*httpx*"` → nothing). The
declared pin is **`httpx==0.28.1`** (`uv.lock:680`; `pyproject.toml:31` declares `httpx>=0.27` in the
`dev` extra).

**The consequence, re-measured:**

```
ERROR tests/test_protected_path_guard.py - RuntimeError: The starlette.testclient requires httpx
ERROR tests/test_server_policy_gate.py   - RuntimeError: The starlette.testclient requires httpx
Interrupted: 2 errors during collection
```

**Closed or open:** **OPEN**, and correctly so. The brief's rule for this case applies — *"leave the
corpus as corrected — this mission is not the place to install a dependency that requires network."*
Nothing is quoted from those two files. The host has **9** `TestClient`-dependent files (AST scan for a
`testclient` import), all un-collectable here; `test_protected_path_guard.py`'s 26 and
`test_server_policy_gate.py`'s 14 remain **unmeasurable in this environment** and **unquoted**.

**What would close it:** `httpx==0.28.1` in the uv cache, or network access to pypi.org. Neither is a
code change.

---

## F87 — §3's commit table did not list every commit, and §0 said it did

Found while doing the §0/§3 update this mission requires, by **driving the claim instead of trusting
it**. `CONTEXT.md` §0 says:

> *"§3 lists every commit on top of it and is the authority for the count."*

Driven (`git log --reverse --format="%h %s" 83b10af..HEAD` diffed against the table's hash rows):

```
68 commits on top of 83b10af; 58 hash-rows in §3's table
MISSING from the table (11):
  08dd57e docs: corpus integrity II …          ← this mission
  6ec0f48 fix: the success test …              ← this mission
  5c129d5 docs: point the handoff at aa47ae0
  0cd5613 docs: point the handoff at 9d56aec
  8de56d8 docs: point the handoff at 17130c7
  c03eee3 docs: point the handoff at 5898e0e
  3ed402a docs: point the handoff at 805eca8
  3c1b6dd docs: point the handoff at 1e83e34
  8a7db35 docs: point the handoff at 3990313
  40cfa52 docs: point the handoff at 3f9e639, and record F72-F74
  c8b3cc8 docs: point the handoff at 8a7e9ab
```

**Nine of those predate this mission** — every "point the handoff" commit since `0bc4f22` had simply
stopped being listed, while the table went on listing earlier ones (`b8dc4ac`, `af3a89a`, `dd21f6d`,
`0bc4f22`). So the claim and the table disagreed for four phases, and nothing checked.

**Closed, not named.** The fix is pure transcription — twelve rows from `git log`, no judgement — and
the claim §0 makes is load-bearing (§3 is *"the authority for the count"*). Backfilled, and the
`HEAD` marker moved from `aa47ae0` (where it had been left) to `08dd57e`.

**One exception remains, and it is stated rather than papered over.** The handoff commit that carries
the §0 line pointing at `08dd57e` is necessarily created *after* the table is written, so it cannot
list itself. §0 now names that exception explicitly. That is the honest resolution of a
self-referential table; the alternative — a table that claims completeness it cannot have — is the
defect being fixed.

**Re-runnable:** `.workbuddy-ai/memory/post-m13-gate-enablement/check_commit_table.py`.

---

## What changed, and what was left alone

| Location | Change |
|---|---|
| `tests/test_m4_governance_wiring.py` | the count 2 → **3**, the reasoning in the docstring, and the three files named (a swap can no longer hide behind the count) |
| `CONTEXT.md` §11's Phase-10 block | the note now records **218 passed, 0 failed** (was "216 passed, 2 FAILED"); both guards' dispositions stated; the `httpx` re-attempt recorded |
| `CONTEXT.md` §10 | F86 added as a row of the **fourth sub-case** (the instrument's subject was the wrong thing); **F85** added as a discipline bullet — *a count is canonical only if it is measured after the LAST change to any member* |
| `CONTEXT.md` §11's count-history note | F85 (Deliverable 1) |
| `PHASE_10_M4_GOVERNANCE_UNWIRED.md` | annotated: `:134` → `:142`, the third site named, its authorisation cited. **Not rewritten** |
| `PHASE_CORPUS_INTEGRITY.md` §2.1 | annotated: the classification is corrected for one of the two guards (F86) and the third site for the other. **Not rewritten** |
| `tests/test_outcome_classification_authority.py` | the floor (Deliverable 1) — the M4 guard needed **no** floor: its subject is not a collection |
| `CONTEXT.md` §0 / §3 / §12 | the `HEAD` line and the two new phase rows; **§3's table backfilled with the nine missing handoff commits, plus this mission's three (F87)**; §12's M4 row corrected from *"both construction sites"* to **three**, with the third's provenance |

**Nothing else.** No production code changed in this deliverable. The `httpx` item is environmental and
stays open. The other instrument-defect instances were not opened.

---

## Verification

| check | result |
|---|---|
| the M4 guard | **25 passed** (was 24 passed, 1 failed) |
| non-vacuity (2.1) | **3/3 CAUGHT**, 3 files restored byte-identical (sha256) |
| the Phase-10 block, verbatim | **218 passed, 0 failed** (was 216 passed, 2 failed) |
| the two `httpx` files | **still ERROR at collection** — 2.2 remains OPEN |
| the canonical block (47 files) | **1390 tests — 1389 passed, 1 failed (F38)** |
| `ruff` on the changed files | clean; `ruff check wisp/` unchanged at **11** (F71) |
| `mypy` | not re-run — stated as an argument, not a measurement |

**Non-vacuity probes** (`.workbuddy-ai/memory/post-m13-gate-enablement/m4_inventory_nonvacuity.py`):

| probe | what it breaks | result |
|---|---|---|
| NVA | a **fourth** construction site appears | **CAUGHT** |
| NVB | a **known** site disappears (the file set, not just the count) | **CAUGHT** |
| NVC | a site passes a `policy` bundle (the M4 tripwire) | **CAUGHT** |

---

## Residuals, open

1. **`httpx`** — §2.2. Environmental; needs the cache or the network.
2. **The nine `TestClient` files' counts remain unquoted** until then.
3. **`PHASE_10_M4_GOVERNANCE_UNWIRED.md`'s row keeps its stale `:134`** — by design. An annotation, not
   a rewrite, is the convention for a phase report.
4. **The M4 count guard is still not in the canonical block.** It is a Phase-10 contract guard and its
   own tripwire is the property that matters; adding it is a separate call. Named, not made.
