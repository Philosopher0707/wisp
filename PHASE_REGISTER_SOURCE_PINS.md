# PHASE_REGISTER_SOURCE_PINS.md — the register source pins

**Mission:** apply `17130c7`'s content check to the two newer derived registers, and re-pin what it
finds. **No ADR** — the pattern (`CURRENT_AUTHORITIES.md`'s guard), the generators' refusal
behaviour (corpus governance II) and the tolerance (`17130c7`'s) are all decided; this mission
repairs an instrument.
**Baseline:** the PR #30 branch at `3c606cd` (corpus governance II + its review fixes). Work is on
branch `register-source-pins`, stacked on it, so PR #30's CI is not re-triggered. Tree = the user's
WIP in `CONTEXT.md` §8 — untouched.

---

## §1 — The mission, in one page

**Measured, then repaired.** The content check (`scripts/register_pins.py`, one rule for both
registers, window ±3) found **12** stale pins in `CURRENT_FINDINGS.md` and **37** in
`CURRENT_OPEN_ITEMS.md`; **12 and 37 were re-pinned**, and both checks now pass at **0 stale** (97
and 101 checkable). The counts re-pinned equal the counts measured.

| register | stale (measured) | re-pinned | quotations changed | statuses changed |
|---|---|---|---|---|
| `CURRENT_FINDINGS.md` | 12 | 12 | 0 | 0 |
| `CURRENT_OPEN_ITEMS.md` | 37 | 37 | **1** — `ITEM-F3`'s missing `…` marked (no word changed; `§Findings` entry) | 0 |

**Where the brief's numbers differ, and why.** The brief expected **55** for open items, from
`PHASE_CORPUS_GOVERNANCE_II.md` §5. That count was **my own over-count**: its heuristic had no rule
for a quote spanning two source table cells, so eleven correctly pinned ledger rows (and one with a
nested quote) were called stale. The measurement is **37**.

**Three instrument defects, all found by running:** the first cut of the check reproduced that
over-count (49) until the cell-boundary and nested-quote rules were added; a quote *search* sent two
rows to one line, so §12 rows are re-pinned **by id**; and the probe harness trusted **stale
bytecode** after size-preserving, same-second mutations — twice — until it wrote none.

**One departure from the brief's letter**, flagged in §4: `ITEM-F3`'s quote was never verbatim (not
an amended source), and its elision is now marked rather than exempted. Revert it if you prefer the
letter. **No ADR, no `wisp/` change, no status change.**

---

## §2 — Deliverable 1: the content check, in both generators

### What was built

**`scripts/register_pins.py`** — the rule, **once**, imported by both generators so the two
registers cannot disagree about what a stale pin is:

- **The quote** is the curly-quoted text directly after `path:line — `, with nested `“…”`
  balanced. A later quote in the same cell may cite another file (F37's cell quotes `CONTEXT.md`
  after its ledger pin) and is not checked against this pin.
- **Normalisation** drops presentation only — `*`, backticks, `✅`, curly-vs-straight quotes — and
  reads a source's table-cell boundary `|` as the register's `—`: a register cell may not contain
  `|` (the generator refuses one — the defect that hid F37 and F39), so a quote spanning two source
  cells is written `A — B`.
- **Elisions**: a quote split by `…` must have every fragment (≥ 6 characters) in the window.
- **The window is ±3 lines of the cited line — never the whole file.** Not 0, because phase
  reports hard-wrap prose and a quote begun on the cited line ends on the next
  (`PHASE_DAG_RETIREMENT.md:150`–`151`). Not wide, because the quotes are short status phrases
  that recur (*"✅ COMPLETE — ADR-00xx"*): a whole-file window passes a pin that has drifted onto a
  *different* row carrying the same words. Measured on the 198 checkable pins, the widest a correct
  pin needs is **3** (findings: 78 at 0, 2 at 1, 1 at 2, 4 at 3).

Both generators call it from their source check, with a **floor** (`QUOTE_FLOOR = 60` checkable
quotes; 97 and 101 today), and **refuse, writing nothing**, on a stale pin — each refusal names
where the words actually are.

### The measurement — RED

| register | rows | checkable | **stale** | the brief expected |
|---|---|---|---|---|
| `CURRENT_FINDINGS.md` | 104 | 97 | **12** | 12 |
| `CURRENT_OPEN_ITEMS.md` | 102 | 101 | **37** | 55 |

**Findings:** `F75`, `F79`, `F80`, `F82`, `F87`, `F88`, `F89`, `F97`, `F101`, `F102`, `F103`, `F104` —
the same twelve corpus governance II recorded.

**Open items: 37, not 55 — and the difference is a defect in corpus governance II's own
measurement.** That count (`PHASE_CORPUS_GOVERNANCE_II.md` §5) compared a 16-character prefix of
each quote to the source line with no cell-boundary rule, so the **eleven ledger rows** whose quote
spans two table cells (`P3 · item 6/7`, `P5 · item 5`, `P8 · item 4/5/7`, `P9 · item 2/3/4/5/8`) were
counted stale while correctly pinned, and so was one row whose quote nests `“…”`
(`PHASE_OBJECTIVE_FLAG_COMPOSITION R1`). The first run of *this* check reproduced that error (49
stale, 13 "nowhere") until the two normalisation rules above were added; reading the thirteen
"nowhere" lines showed each quote **is** on its line. **The instrument is not exempt** — recorded,
and the brief's number is the hypothesis, as it said.

**The 37:** the 36 `CONTEXT.md` §12 rows other than the three corpus governance II re-pinned (`R1`,
`R2`, `G0`, `E`, `G1`, `R1b`, `W1`, `R10`, `F1`–`F5`, `G2`, `R3`–`R9`, `M1`–`M7`, `M9`, `M10`,
`M12`–`M16`) — cited at `:2030`–`:2075`, now at `:2205`–`:2250` — plus `PHASE_AUTHORIZATION_PARITY
R3` and `PHASE_KEY_TRUST_WORKFLOW R6` (a few lines each). **One of them, `ITEM-F3`, is also not a
verbatim quote**: its words are found nowhere, because the register dropped a clause from the middle
of the source's text with no `…`. The source was **not** amended. Deliverable 3 handles it.

### The guard

Both `test_current_findings_pins.py` and `test_current_open_items_pins.py` gain
`TestEverySourcePinCarriesItsQuote`: the check passes on the page (**RED now**, by design), the
checkable count meets the floor, a pin moved 40 lines onto other text is **refused**, and a pin
shifted **2** lines stays **silent** — a heading added above a row is not drift (F92).

| check | result |
|---|---|
| both generators, current tree | **refused** — 12 and 37 named, nothing written |
| the guards | **4 failed** (the two stale-pin tests, and each register's existing "generator refuses an unsound table", which runs the same check), **64 passed** — incl. floor, moved-pin refused, in-window shift silent |
| rows changed | **none** — Deliverables 2 and 3 are the re-pins |

---

## §3 — Deliverable 2: `CURRENT_FINDINGS.md` re-pinned

**12 re-pinned = Deliverable 1's 12.** Every candidate line was listed, not just the nearest: nine
rows had exactly one line carrying their quote; for three (`F79`, `F102`, `F103`) the quote begins at
the end of one line and wraps onto the next, so no single line holds its first words, and each was
read and pinned to the line where the quote **begins** — the same rule as the other nine.

| row | was | now | | row | was | now |
|---|---|---|---|---|---|---|
| `F75` | `PHASE_GATE_ENABLEMENT.md:160` | `:175` | | `F89` | `PHASE_KEY_TRUST_WORKFLOW.md:136` | `:148` |
| `F79` | `PHASE_AUTHORIZATION_PARITY.md:208` | `:211` | | `F97` | `PHASE_CORPUS_INTEGRITY_III.md:131` | `:134` |
| `F80` | `PHASE_AUTHORIZATION_PARITY.md:214` | `:218` | | `F101` | `PHASE_LAYER_B_BOUNDARY.md:271` | `:276` |
| `F82` | `PHASE_OBJECTIVE_FLAG_COMPOSITION.md:145` | `:156` | | `F102` | `PHASE_LAYER_B_BOUNDARY.md:281` | `:302` |
| `F87` | `PHASE_CORPUS_INTEGRITY_II.md:123` | `:177` | | `F103` | `PHASE_LAYER_B_BOUNDARY.md:306` | `:309` |
| `F88` | `PHASE_KEY_TRUST_WORKFLOW.md:118` | `:134` | | `F104` | `PHASE_LAYER_B_BOUNDARY.md:313` | `:317` |

**Only line numbers moved.** The re-pin script asserted every curly-quoted string in the generator is
byte-identical before and after; the regenerated page differs from `HEAD`'s in **13** lines — the 12
rows and the header's commit — and in nothing but a line number or that commit. **No status
changed**, and no quotation was inaccurate, so `§Findings` gains no entry.

| check | result |
|---|---|
| the generator's content check | **passes** — 0 stale of 97 checkable |
| `test_current_findings_pins.py` | **32 passed** |
| reproducible | the page is the generator's output (its reproducibility test is in the 32) |
| **falsification** | narrowing `WINDOW` to 0 → a valid wrapped pin **fails** (CAUGHT, and the in-window test fires too); reverting `F87` to `:123` → **CAUGHT**; widening, the stale `:123` pin is **rejected at ±3 and accepted at ±200** — the tolerance is what separates them. Tree restored byte-identical |

---

## §4 — Deliverable 3: `CURRENT_OPEN_ITEMS.md` re-pinned

**37 re-pinned = Deliverable 1's 37.** The 35 `CONTEXT.md` §12 rows were located **by the row's own
id, within §12 only** — not by searching for the quote. The search was tried first and sent two
different rows to one line twice (`R1b`/`W1`, `M2`/`M15`): the quotes are short status phrases
(*"✅ COMPLETE — ADR-00xx"*) that recur, and ids like `E`, `G1` and `M1` also head rows in §0's
tables. Each id-located line was then required to carry its quote **on that exact line**. The two
phase-report rows (`PHASE_AUTHORIZATION_PARITY R3` → `:131`, `PHASE_KEY_TRUST_WORKFLOW R6` →
`:235`) wrap, and were read and pinned where the quote begins.

| rows | was | now |
|---|---|---|
| `R1`, `R2`, `G0`, `E`, `G1`, `R1b`, `W1`, `R10`, `F1`–`F5`, `G2`, `R3`–`R9` | `CONTEXT.md:2030`–`:2050` | `:2205`–`:2225` |
| `M1`–`M7`, `M9`, `M10`, `M12`–`M16` | `CONTEXT.md:2059`–`:2074` | `:2234`–`:2249` |
| `PHASE_AUTHORIZATION_PARITY R3` · `PHASE_KEY_TRUST_WORKFLOW R6` | `:126` · `:230` | `:131` · `:235` |

**`M8`, `M11` and `Layer C` — the three corpus governance II re-pinned — were already correct and did
not move** (asserted: their rendered rows are byte-identical to `HEAD`'s).

### One quotation was never verbatim — `ITEM-F3`

`ITEM-F3`'s source cell quoted *"Accepted (low) — annotate so nobody wires them without the missing
checks"*. **Git history shows the source row has never said that**: since `98bb8f9` it has read
*"Accepted (low) — the executor authorises per call; annotate so nobody wires …"*. The register
dropped a clause with no elision mark. **The source was not amended**, so this is not the case the
brief's rule names (*"the source has been amended since"*), and following that rule to the letter —
leave the quote, add a `§Findings` entry — would leave the check permanently red or need an
exemption, a hole in the guard this mission exists to close.

**The decision taken, and flagged as the one departure from the brief's letter:** the elision is now
**marked** — `… ` inserted where the words were dropped — and **no word was added or changed** (the
re-pin script asserted the quotation changed by exactly that and nothing else). The page's `§Findings`
gains an append-only entry, *"Quotations that were never verbatim"*, recording what the source says,
since when, and what was changed. Revert the `…` and the entry if you would rather hold the letter.

### Verification

| check | result |
|---|---|
| the generator's content check | **passes** — 0 stale of 101 checkable |
| the diff against `HEAD` | **39** changed lines — 37 rows + the page's two commit references — differing **only** in a line number or that commit, **except** `ITEM-F3`'s `… `; **9** lines added (the `§Findings` entry). **State counts unchanged**: 57 / 1 / 6 / 38 |
| the four register guards + editorial + entry point + doc drift | **214 passed** |
| **falsification** | `WINDOW = 0` → **CAUGHT** (32 open-item pins legitimately need 1–3 lines); reverting `R1` to `:2030` → **CAUGHT**; the stale `:2030` pin is **rejected at ±3, accepted at ±200**. Stable over three back-to-back runs; tree restored byte-identical |

### An instrument defect in the probe harness — found, root-caused, fixed

The first open-items probe run reported **`WINDOW = 0` → MISSED**, and then the guard went **RED on a
correct tree**, naming `R1` at `:2030` — the value a probe had written *and restored*. **Stale
bytecode**, `CONTEXT.md` §10's *`.pyc` purge* case, twice over:

1. A probe's mutation (`2205`→`2030`, `3`→`0`) **preserves the file's size**, and its restore landed
   in the **same second**; CPython validates a cached `.pyc` by size and whole-second mtime, so the
   next test process loaded the *mutated* bytecode. The direct `python scripts/…` run (as
   `__main__`, uncached) passed, which is what separated the two.
2. After `PYTHONDONTWRITEBYTECODE=1` was set for the subprocesses, a MISSED recurred **only on a run
   started immediately after another**: the harness's own final step imported the module in the
   *parent* process, which still wrote bytecode — after the purge — for the next run to trust.

Fixed in the harness (no bytecode written by the subprocesses **or** the parent; caches purged on
every restore); every probe above was re-run under it. **No committed file was affected** — the
guard's RED was the stale cache, and a clean-cache run of the same tree passed. Recorded because a
probe that misreports is the class this corpus exists to catch, and it caught this one only because
the results disagreed with a run by hand.
