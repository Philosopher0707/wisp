# Phase — Corpus integrity III

**Status:** `COMPLETE` — **no new ADR**. 2.1, 2.3, 2.4 **closed**; 2.2 **partly closed** (the table is
consolidated; the installs remain impossible here). Two further items found and closed (F97).
**Baseline:** `HEAD` = `f35188a` (Deliverable 1); tree = the 29 WIP entries of §8.
**Predecessor:** `PHASE_CORPUS_INTEGRITY_II.md` (2.1 closed, 2.2 open).
**Report scope:** the four items, each closed or named, plus what the pass found on its own.

---

## The items

| item | state | evidence |
|---|---|---|
| **2.1** — F89's falsy-`expires_at` message | **CLOSED** | `test_a_bundle_with_no_expiry_raises_a_named_error` + a discriminating-pin test; **36/36** expiry inputs identical in outcome |
| **2.2** — the declared-and-absent dependency table | **CLOSED (the table); OPEN (the installs)** | `CONTEXT.md` **§6.1**, one table with pins, checks, blocked files and un-quotable counts. Both offline installs re-attempted and **still fail** |
| **2.3** — the class's two new sub-cases | **CLOSED** | `CONTEXT.md` §10 gains sub-cases **5** and **6**, with three citations each; docstring-only |
| **2.4** — the ADR-0057 residual | **CLOSED** | `CONTEXT.md` §12 gains row **W1**; the residual exists exactly as the brief described |
| **F97** — two stale ADR-range claims | **CLOSED** | `CONTEXT.md`'s `§0` line and §7 documents table; `CURRENT_AUTHORITIES.md`'s header |

---

## 2.1 — F89, and what the fix actually was

`merge_layers` read:

```python
expires_at=min(h for h in (higher.expires_at, lower.expires_at) if h) or 0.0,
```

**The trailing `or 0.0` is unreachable.** The filter drops every falsy candidate, so `min()` either
returns a truthy value from a non-empty sequence (and `or 0.0` does not fire) or raises on an empty one
(and `or` never runs). The author's evident intent — *"earliest stated, else `0.0`"* — was never
realised, and a bundle that omitted `expires_at` (the `PolicyBundle` default is `0.0`) failed with
`ValueError: min() iterable argument is empty`: a message naming neither the bundle nor the cause.

**The behaviour is kept, and the reason is not inertia.** Returning `0.0` would make
`PolicyBundle.is_expired()` true and `trim_expired` would then set **every** approval to `deny` and the
network to `off` — silently. That is the false-assurance shape ADR-0058 R3 exists to refuse: an
operator's omission would become a total lockdown without saying so. So the bundle is refused, as
before; the message now names the field to set and the type that defaults it.

**Proved unchanged, not asserted:** the old expression and `_effective_expiry` driven over the
**cross-product of six expiry values** (`0.0`, `1000.0`, `2000.0`, `-5.0`, `0`, `None`) — **36/36
identical in outcome**, where "outcome" means the same exception type or the same returned value. The
only difference is the text on the all-falsy case.

**Non-vacuity:** reverting the call site to the old expression makes the new test **fail**
(CAUGHT); the file was restored byte-identical (sha256).

**And the test's own pin is discriminating** — ADR-0058 §6's NV1 lesson. `pytest.raises(ValueError)`
passes on any `ValueError`, so a second test reproduces the pre-fix expression and asserts that its
message *fails* the same assertions. That is the property the first test needs to be worth anything.

**On ADR-0058's non-violation 2** (`wisp/policy/` is unchanged): the guard for it pins the package's
**exported surface** and three **signatures**. `_effective_expiry` is private (not in `__all__`) and no
signature moved, so the non-violation holds as written. The change is a private helper and one call
site — stated here rather than left for a reader to check.

---

## 2.2 — the dependency table, and the re-attempt

`CONTEXT.md` **§6.1** is now the one place the declared-and-absent dependencies are stated together:

| dependency | pin | what it blocks | counts NOT quotable |
|---|---|---|---|
| `httpx` | `0.28.1` | **9** `TestClient` files | all of them; two are in §11's Phase-10 block, which **aborts at collection** (F80) |
| `cryptography` | `50.0.1` | 4 policy test files | **the M4 policy suite** — driven per file: **14 failed, 6 errors, 18 passed** across the six (F88) |
| `numpy` | `2.4.6` | numeric benchmarks | — |
| `tiktoken` | `0.14.0` | P8's token-budget measurement | P8's budgets |
| `aiohttp` | `3.14.3` | nothing directly | — |
| `jsonschema` | `4.26.0` | **nothing** — FIXED 2026-09-24 | — |

**The re-attempt, recorded once more:** both installs fail with *"was not found in the cache … the
network was disabled"*. The cache exists and is populated (`archive-v0`, `builds-v0`, `sdists-v9`,
`simple-v24`, `wheels-v6`) and holds no `httpx` or `cryptography` entry of any kind. **Closing either
needs network access, not a code change** — so this item is open in the only sense that matters, and
the corpus stops quoting those counts.

**A correction to F88's own number.** F88 recorded *"14 failed, 6 errors, 13 passed"* across *"the five
`test_policy_*.py` files"*. Driven again: the five give 14/6/13 ✓ — and there is a **sixth**
(`test_policy_enforcement.py`, **5 passed**, unaffected by `cryptography`). The six together are
**14 failed, 6 errors, 18 passed**. F88's number was right about what it named; the *set* was one file
short. Recorded rather than quietly widened.

---

## 2.3 — the class's two new sub-cases

`CONTEXT.md` §10's instrument-defect class had three sub-cases. It now has **five**, added as
docstring-only entries with citations:

**Sub-case 5 — a raise that does not discriminate.** `pytest.raises(X)` is satisfied by **any** `X`, so a
test whose property is *"this refuses, for this reason"* passes on a refusal for a different reason.
Cited: `PHASE_KEY_TRUST_WORKFLOW.md` §6 (**NV1** — the loader reached `_bundle_to_effective` and raised
F89's unrelated `ValueError`, the same type from the same call) and this pass's own test, which asserts
the pin discriminates. **Tell:** the assertion names a type, and the type has more than one producer.

**Sub-case 6 — a guard that pins a STATE rather than a PROPERTY.** Cited three times:
`PHASE_M4_WIRING.md` §3 (**F92**, both instances — an exact caller **set**, and a scan for a **bare
name**) and `PHASE_REST_AUTHORIZATION_COMPOSITION.md` §6.1 (**F96** — the guard recomputed the
production call instead of observing it). **Tell:** the failure message describes a *situation*, not a
*violation*.

Sub-case 6 is the **inverse** of the first five — the instrument works, it fails, and it fails for the
wrong reason — which is why it needed its own line rather than being folded into the class statement.

---

## 2.4 — the ADR-0057 residual is a live item now

The brief's description was **accurate**: ADR-0057 §"Residuals, named" 1 reads *"The agent path's
WebSocket approval prompt has never rendered. `approve()` sends a frame no client reads, so it always
timed out (60 s) and denied."*

**It was not in `CONTEXT.md` §12.** Grep for `ADR-0057` in that file returned only the phase table and
the commit table. So a live, security-relevant item — a prompt that has **never rendered** — existed
only inside an ADR's residual list, which is not where a reader is sent for open work. §12 gains row
**W1**, marked **OPEN — needs its own ADR**, with the guard that pins it named.

**Finding F100: a residual named in an ADR is not automatically a live item.** Every ADR in this corpus
ends with a residuals list, and every one of those is *true*; none of them is a **handoff**. §12 is the
open-items authority, and nothing moves a residual into it. This is the F82/F98 shape again: two
records, one of which is the one readers consult, and no mechanism comparing them.

---

## What the pass found on its own

**F97 — a live range claim can survive fourteen ADR landings.** `CONTEXT.md` §7's documents table read
`WISP_ARCHITECTURE_DECISIONS.md | **ADR-0001 … ADR-0044**` while the log was at **59**. §0's
`**Decisions:**` line said **0057**. And `CURRENT_AUTHORITIES.md`'s header said *"covers ADR-0001 …
ADR-0057"* while its own citations reach ADR-0054 and the log reaches 0059. Three stale claims of the
F81 class — prose a pin guard cannot see. All three corrected.

`CURRENT_AUTHORITIES.md` is **derived**, and this pass did **not** regenerate its body: ADR-0059 changes
none of the six stated authorities (ADR-0055 §6's rule, re-applied). Its **header** — the range and the
commit — is the page's own claim about the log's extent, and a regeneration is what rewrites it. So the
header was regenerated and the body left byte-identical, with the reason stated in the header itself.

**F99 — the brief cited two ledger rows that do not exist.** *"Update `WISP_MIGRATION_STATUS.md`'s G1
and M4 rows"*: the file contains **zero** occurrences of `G1`, `authorization parity`, `policy bundle`
or `governance layer`, and its 16 `M4` mentions are **ADR-0004 revisited**, a different M4. The live
target is `CONTEXT.md` §12's **E** and **G1** rows — updated in Deliverable 1 — and the ledger is left
alone rather than given a fabricated row. The third consecutive mission with a wrong citation into this
file.

---

## Verification

| check | result |
|---|---|
| 2.1's differential | **36/36** expiry inputs identical in outcome |
| 2.1's non-vacuity | reverting the call site → **CAUGHT**; file restored byte-identical |
| the F89 tests | **2 new** (`test_a_bundle_with_no_expiry_raises_a_named_error`, `test_the_named_expiry_error_is_a_discriminating_pin`) |
| the doc guards + the D1 guards + the ADR-0058 guard | **137 passed** |
| regression (24 files) | **361 passed, 0 failed** |
| `ruff check wisp/` | **11 errors — unchanged** (F71); changed files clean |
| `mypy` | not re-run — stated as an argument, not a measurement |

**The canonical block's count is unaffected by this deliverable** (no test file was added to or removed
from it) — but it was re-measured in Deliverable 1's change, per F85, and both headings carry **1453
tests — 1452 pass, 1 fails**.
