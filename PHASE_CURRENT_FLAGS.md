# PHASE_CURRENT_FLAGS.md — the flags register

**Mission:** the corpus governance layer, Deliverable 3 of 4.
**Baseline:** `HEAD` = `e892a7d`, `main`. Tree = 29 WIP entries (the user's, §8) — untouched.
**Type:** a **derived register**. It introduces **no decision**.
**Deliverable:** `CURRENT_FLAGS.md` (26 switches) + `scripts/derive_current_flags.py` +
`tests/reliability/test_current_flags_pins.py` (38 tests).

---

## 1. Why the page exists

ADR-0002 states *"one rollback flag per concern, read via `getattr` with a safe default"*. The
corpus now has **26 switches** and **no page stated which are on by default, where each is
read, or which depend on which.** Anyone asking *"what does `WISP_ACCEPTANCE_GATE` default to
and what does it depend on?"* had to reconstruct the answer from `config.py`, the ADR that
introduced it, and the guard that pins it.

---

## 2. What was built

`CURRENT_FLAGS.md` — **26 rows**:

| | |
|---|---|
| `WispConfig` `bool` settings | **23** |
| switches read from `os.environ` (not config fields) | **3** |
| **ON by default** | **11** |
| **OFF by default** | **15** |

Columns: `name · env · default · read_at · gates · depends_on · adr · tripwire`. Plus §(a) the
reading rule and its measured departures, §(b) the interaction matrix, §Findings.

**The totality rule is mechanical**, which is the point: *every `bool` setting in
`WispConfig`'s schema gets a row, plus the three env-only switches.* The guard asserts it
against `wisp.config.get_schema()` — so **adding a flag to `config.py` without regenerating the
page fails the build.** That is a far stronger property than a judgement about which flags
"count" as rollback flags, and it is the reason the eight rendering/prompting knobs are on the
page too.

**Every `default` is read from the schema at generation time**, and every `read_at` is checked
against the tree on every run — the site must exist, be in range, and **name the flag** within
±3 lines (by setting name, env-var name, or the constant holding the env var). A stale read
site fails the derivation.

---

## 3. §(a) — the reading rule, and three measured departures

**The rule as ADR-0002 states it**, which is **not** how the brief paraphrases it:

> *"each read at the **consumption site** as `getattr(config, name, True)`, and each resolvable
> from the environment (`WISP_*`) via `get_setting`"*

Three parts: **one flag per concern**; **read at the consumption site** via `getattr` with a
safe default; **resolvable from the environment** via `get_setting`.

**Measured departures, recorded not repaired:**

1. **Three rollback flags are read from `os.environ` directly and are not `WispConfig` fields.**
   `criteria_strict_derivation` and `criteria_structured_declaration`
   (`wisp/autonomous.py:58`, `:68`, read by `_strict_derivation_enabled` /
   `_structured_declaration_enabled`) and `ws_auto_approve`
   (`wisp/transport/websocket.py:127`). They are **invisible to `config.py`, to `wisp doctor`,
   and to any consumer that reads a config object**, and a test double cannot opt out by setting
   an attribute. Each docstring says the choice was deliberate (*"Read at this composition point
   for the same reason as the strict flag: the pure function stays testable without env"*), which
   makes it a **stated deviation** rather than an accident — but ADR-0002 part 2 does not reach
   them.
2. **`verification_loop` has two consumption sites** (`stateless.py:505`, `:1439`) and
   `turn_spans` has two (`composition.py:310`, `runtime.py:1409`). ADR-0002 says *"read at the
   consumption site"* — **plural sites are consistent with the rule.** Recorded as a fact, not a
   violation, because the brief's paraphrase would make both violations. See §4.
3. **`graph_oscillation_guard` is the only flag whose `config.py` description names no ADR**,
   though ADR-0034 and ADR-0036 both cite it and `stagnation_gate`'s description names it as its
   paired level. Its provenance is on this page instead.

---

## 4. §Findings — what the page could not pin

### 4.1 The brief's paraphrase of ADR-0002 is wrong, and it would have produced a false finding

The mission brief states the reading rule as *"read once, at the composition point, via
`getattr` with a safe default"*. **ADR-0002 says *"read at the consumption site"***, and
ADR-0002's own Consequence clause explains why: the point of `getattr` is that *a test double
that predates the flag still gets the new behaviour* — that only works if the read happens
where the behaviour is consumed.

Under the brief's paraphrase, **`verification_loop`'s two sites and `turn_spans`'s two sites
would each be a violation of a rule the corpus does not have.** The brief's own method section
says *"every specific claim in this brief is a hypothesis"*, and this is one that would have
decided the wrong way. Recorded, not repaired — the ADR is the authority.

### 4.2 Five flags the brief names do not resolve

The brief lists nineteen flag names. Measured:

| the brief names | measured |
|---|---|
| `verification_gate` | **appears nowhere in `wisp/`** — the real name is `verification_loop` |
| `graph_mutation` | **appears nowhere in `wisp/`** — the P5 concern's flag is `task_graph` |
| `criteria_strict_derivation` | exists, but **not a `WispConfig` field** (§3 departure 1) |
| `criteria_structured_declaration` | exists, but **not a `WispConfig` field** |
| `ws_auto_approve` | exists, but **not a `WispConfig` field** |

Five of nineteen names do not resolve as `WispConfig` flags. Two are phantoms; three are
env-only.

### 4.3 Eight switches carry `—` in `read_at`

`auto_approve`, `capability_filtering`, `show_thinking`, `show_tool_output`, `compact_mode`,
`env_context`, `auto_compact` and `autonomous` are read by the transports, the renderer and the
prompt assembler — surfaces outside this deliverable's subject — and their exact lines were not
traced. `—` states that the location is **not pinned**, rather than naming a module the page
cannot verify. **An unpinnable location is a finding, not a claim.**

### 4.4 The interaction matrix cites an ADR for every interaction, and that is a measured result

Seven interactions, each with the ADR that states it: ADR-0054 (the `acceptance_gate` →
`turn_criteria_source` dependency), ADR-0056 (the criteria flags' independence and their
derivation *order*), ADR-0036 (the stagnation concern's two levels), ADR-0002 (P0's three
independent concerns), ADR-0010 (P0 fidelity vs P1 journaling), ADR-0057 (the two approval
flags). **No pair was found that interacts without a stated interaction.**

---

## 5. Verification

| check | result |
|---|---|
| the guard | **38 tests** |
| **non-vacuity** | **16/16 CAUGHT** by page mutation; **4/4 REFUSED** by the generator; tree restored byte-identical (sha256) |
| totality | 23 `bool` settings + 3 env-only = 26 rows, asserted against `get_schema()` |
| defaults | 26/26 match the schema |
| env names | 23/23 (config rows) match the schema's `env_var` |
| read sites | every `read_at` exists, is in range, and names the flag within ±3 lines |
| interactions | 7/7 cite an ADR, and each names two real flags |

### 5.1 Three probe defects, all found by running

1. **`capability_filtering`'s `read_at` was a bare module** (`wisp/core/stateless.py`), not a
   location. The guard correctly rejected it — *a location that cannot be checked is not a
   location* — and it became `—` with a §Findings entry.
2. **`rule-authority-dropped` did not break the property it named.** The mutation removed the
   rule's inline attribution to ADR-0002 but left the **section heading's**, which also names it.
   The guard was right to stay green; the mutation was incomplete. Fixed by stripping both — and
   the guard's test was *also* strengthened, because the original form
   (`"ADR-0002" in §(a)`) would have been satisfied by the heading alone.
3. **`paraphrase-finding-dropped` replaced the wrong occurrence.** `"read once"` appears twice —
   once in §(a) and once in §Findings — and the whole-page replace hit §(a) first, leaving the
   §Findings entry intact. Scoped the mutation to §Findings.

### 5.2 The generator refused three broken tables

- a `|` in a cell → refused;
- a default that disagrees with the schema → refused;
- an env name that disagrees with the schema → refused;
- a `bool` setting omitted from the table → refused.

That last one is the property the whole page exists for: **a flag added to `config.py` without
a row fails the derivation.**

---

## 6. The residual

- **The eight unpinned read sites.** Their locations are not recorded. Tracing them means
  auditing the transports and the prompt assembler, which is a different subject.
- **The three env-only switches are not config fields.** Converting them is a behaviour change
  and its own decision.
- **The interaction matrix is over the pairs the corpus names.** A pair that interacts and that
  no ADR mentions would not be found by reading the ADRs — it would take driving every pair.
  Stated as a scope, not claimed as exhaustive.

---

## 7. What this deliverable did not do

- **No flag added, no default changed.** The register's totality is *asserted*, not extended —
  and the assertion is what makes an addition fail rather than pass silently.
- **No departure repaired.** The three env-only switches are recorded, not converted.
- **No production code touched.** Two new files (the page, the generator) and one test file.
- **The user's WIP was not staged.**
