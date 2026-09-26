# PHASE_REGISTER_SOURCE_PINS.md — the register source pins

**Mission:** apply `17130c7`'s content check to the two newer derived registers, and re-pin what it
finds. **No ADR** — the pattern (`CURRENT_AUTHORITIES.md`'s guard), the generators' refusal
behaviour (corpus governance II) and the tolerance (`17130c7`'s) are all decided; this mission
repairs an instrument.
**Baseline:** the PR #30 branch at `3c606cd` (corpus governance II + its review fixes). Work is on
branch `register-source-pins`, stacked on it, so PR #30's CI is not re-triggered. Tree = the user's
WIP in `CONTEXT.md` §8 — untouched.

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
