# PHASE POST-M13 — VERIFICATION EVIDENCE AUTHORITY RECON

**Mode:** READ-ONLY FORENSIC RECON + ARCHITECTURE ANALYSIS
**Production changes:** 0 · **Test changes:** 0 · **Config defaults changed:** 0
**Predecessor:** `PHASE_POST_M13_F8_PROVISIONING_AND_TOOL_EXECUTION_RESTORATION.md` (which exposed the defect)

---

## 1. Mission Result

The false success is **reproduced**, its **root cause is located to a single extraction
expression**, and the repair target is identified as **a mechanical correction that restores an
already-documented contract at the existing authority boundary** — not an architecture decision.

```text
A mutation followed by a command that exits 3
    -> P3 acceptance_verdict = "pass"
    -> goal_state = "goal_met"
```

Proven by a live turn through `AgentRuntime.run_turn` with a real `ToolExecutor`, with the value
captured at every boundary (§4, §10, §11).

**The one-sentence answer to the primary question:**

> A failing verification becomes successful evidence because the consumer at
> `core/stateless.py:924` reads `result_event["result"]` — which is the tool's **JSON envelope
> string** (`{"status": "ok", ..., "data": "[exit code: 3]\n", ...}`) — and hands that whole
> string to a parser whose documented contract is the **formatted command output**, where the
> failure marker is the *first* thing on the line. The parser tests `startswith`; the envelope
> starts with `{`. The parser is correct; the argument is wrong.

**Three things that make this a precise, bounded defect rather than a broken subsystem:**

1. **No information is lost.** The exit code survives every boundary — as the text
   `[exit code: 3]` inside `data`, and as the **typed integer** `metadata.exit_code == 3`. The
   consumer simply never reads either.
2. **The parser is faithful.** Driven with the representation it documents, it classifies
   correctly in all seven control cases (§11). Nothing about the shell-success encoding is wrong.
3. **It is one expression.** `core/stateless.py:924-925`. The guard, P3 and the goal arbiter all
   behave correctly given what they are handed (§14, §15).

---

## 2. Entry Criterion

```text
=== ENTRY CRITERION ===
F8_REPAIRED: YES
JSONSCHEMA_IMPORTABLE: YES            (4.26.0, provisioned offline from the local uv cache)
REAL_TOOL_EXECUTION: YES              (read / write / approved run_bash, proven post-F8)
MUTATION_EXECUTION: YES               (write_file landed on disk)
FAILING_VERIFICATION_REPRODUCED: YES  (run_bash "exit 3", exit_code=3 in metadata)
P3_FALSE_SUCCESS_REPRODUCED: YES      (acceptance_verdict=pass, goal_state=goal_met)
PRODUCTION_CHANGES: 0
ENTRY: PASS
=======================
```

---

## 3. Baseline

| Item | Value |
|---|---|
| `HEAD` | `7c15626` (M11) — every POST-M13 phase is implemented, verified, documented, **uncommitted** |
| branch | `main` |
| `wisp/` tracked modifications | 20 (all pre-existing WIP; **unchanged** by this phase) |
| interpreter | `.venv/bin/python` — `jsonschema` importable at 4.26.0 |
| `stagnation_gate` / `goal_state` / `recovery_ladder` | `false` / `false` / `false` (defaults, unchanged) |
| verification authority | `core/verification.py::VerificationFloorGuard` — single writer, `note_tool_result` |
| evidence scripts | `.workbuddy-ai/memory/post-m13-verification-evidence-recon/` (outside the tree) |

The repository tree was **not** modified. The in-process method wrap used to observe
`note_tool_result` exists only inside the evidence scripts.

---

## 4. Reproduction

**Setup:** a temporary workspace, a scripted provider, the live `AgentRuntime`, a real
`ToolExecutor`, `permission_mode = ASK_ALL` (so `run_bash` is approval-gated rather than
pre-dispatch blocked), `goal_state=True`.

**Turn shape:**

```text
round 1:  write_file(path=m.txt, content=x)   -> a real mutation
round 2:  run_bash(command="exit 3")          -> a genuinely failing verification
round 3:  content "done"
```

**The invariant under test:** the command's real exit status is **3**.

**Observed, at every boundary:**

| # | Boundary | Value |
|---|---|---|
| 1 | `bash.py::_format_bash_output(3, "", "")` | `"[exit code: 3]\n"` |
| 2 | `ToolExecutor._run_bash_tool` | `'{"status": "ok", "tool": "run_bash", "data": "[exit code: 3]\\n", "metadata": {..., "exit_code": 3}}'` (a `str`) |
| 3 | the emitted `tool_result` event's `result` | **byte-identical to #2** (a `str`) |
| 4 | the value passed to `note_tool_result` | **byte-identical to #2** |
| 5 | `_verify_result_is_success(#4)` | **`True`** |
| 6 | `guard.verify_ok_after_edit` | **`True`** |
| 7 | `guard.resolved()` | **`True`** |
| 8 | `floor_guard_verdict(guard).verdict` | **`pass`** |
| 9 | journal `goal_states[0].acceptance_verdict` | **`"pass"`** |
| 10 | journal `goal_states[0].goal_state` | **`"goal_met"`** |

The final recorded record:

```json
{
  "goal_state": "goal_met",
  "terminal_outcome": "succeeded",
  "acceptance_verdict": "pass",
  "stagnation_verdict": "progressing",
  "stagnation_allows_goal_met": true,
  "turn_succeeded": true
}
```

---

## 5. Raw Tool Result Contract

`run_bash` does **not** return text. It returns a **JSON string** — the executor's canonical
result envelope.

```text
runtime type : str
json.loads -> keys: ['data', 'metadata', 'status', 'tool']
  status     : 'ok'            <- the TOOL succeeded; it says nothing about the COMMAND
  data       : '[exit code: 3]\n'   <- the formatted command output (the failure marker)
  metadata   : {..., "exit_code": 3}  <- the exit code as a TYPED INT
```

Producers:

| Layer | File:line | Produces |
|---|---|---|
| formatter | `wisp/tools/bash.py:31-51` | `"[exit code: N]\n" + stdout [+ stderr]` — the prefix is emitted **only** when `N != 0` (`:39-40`) |
| metadata | `wisp/tools/registry.py:880-886` | `meta["exit_code"] = int(...)` — a **typed int**, via `"[exit code:" in result` (substring, on the *formatted* output) |
| envelope | `wisp/tool_executor.py:1347-1390` | `json.dumps({"status": "ok", "tool": ..., "data": raw_result, "metadata": ...})` |

**Answer to §6's question:** `result_event["result"]` contains **A — the actual tool result**,
where "the tool result" is by contract a **JSON envelope string**. It is *not* an event wrapper
(the event wrapper is the surrounding dict: `{name, result, duration_ms, tool_call_id, type,
timestamp}`). The consumer's error is assuming the tool result is **plain text**.

**A second producer exists and has a different shape.** `WispAgentCore._normalize_tool_result`
(`stateless.py:2013-2107`) builds a **dict** envelope `{"status": ..., "data": ...}`, used by the
engine's no-executor fallback and by the engine's own schema-error path. So
`result_event["result"]` is `str` on the executor path and `dict` on the fallback path — the
consumer at `:925` collapses both with `str(...)`, which is why the shape difference is invisible
until it decides a verdict.

---

## 6. Event Contract

| Question | Answer |
|---|---|
| producer | `wisp/core/events.py:240-248` — `tool_result(name, result, duration_ms, auto_approved, tool_call_id)` |
| payload schema | `{"name": ..., "result": <verbatim>, "duration_ms"?: ..., "tool_call_id"?: ...}` |
| event type | `TYPE_TOOL_RESULT` (`events.py:44`) |
| serialization boundary | `stateless.py:117-124` `_flatten_event` — merges `ev.data` with `type` + `timestamp`; **no transformation of `result`** |
| the executor's yield | `tool_executor.py:1031` — `yield _tool_result_event(func_name, result, ...)`; `result` is passed **verbatim** |
| consumer | `stateless.py:920-930` |

**Is `result_event["result"]` intentionally an envelope?** **Yes.** The producer is a
pass-through by design: `tool_result()` never inspects or unwraps `result`, and
`_flatten_event()` only adds `type`/`timestamp`. The envelope is the tool result, and the tool
result is the envelope.

**Is the event contract therefore wrong?** **No.** The contract is coherent: an event carries
whatever the tool produced. The defect is that **one consumer of that event assumed a narrower
shape than the contract guarantees** — the only place in the codebase that reads
`result_event["result"]` as if it were text.

---

## 7. Verification Evidence Contract

```text
def note_tool_result(self, name: str, result_text: str, args=None) -> None   verification.py:138
```

| Question | Answer |
|---|---|
| accepted input type | `result_text: str` — **the formatted tool output**, not the event envelope |
| expected representation | the text `tools/bash.py::_format_bash_output` produces |
| does it parse? | yes — `_verify_result_is_success` parses the code after the prefix |
| does it trust the caller? | **yes, absolutely.** It performs no shape validation and no unwrapping |
| does it normalize? | no |
| can it distinguish success from failure? | **yes — given the contract's representation.** No — given the envelope |

`_verify_result_is_success` (`verification.py:100-116`), the exact rule:

```text
if not text.startswith("[exit code:"):  -> True   (exit 0; the formatter emits no prefix)
else:                                   -> tail.split("]", 1)[0].strip() == "0"
```

The encoding is documented as owned **jointly** by this gate and `_format_bash_output`
(`verification.py:91-97`), pinned by `tests/test_verification_contract.py`.

### Classification table (§8 of the brief)

| Input representation | Actual meaning | Current classification | Correct? |
|---|---|---|---|
| formatted output of a successful `run_bash` | success | `True` | ✅ |
| **formatted output of a failing `run_bash`** | failure | `False` | ✅ |
| **the JSON envelope of a failing `run_bash`** (what is actually passed) | failure | **`True`** | ❌ **false success** |
| the envelope of a successful `run_bash` | success | `True` | ✅ (right answer, wrong reason) |
| a `dict` denial envelope stringified | denial / not run | `True` | ❌ (unreachable — see §13) |
| bare `"[exit code: 3]"` | failure | `False` | ✅ |
| bare `"[exit code: 0]"` | success | `True` | ✅ |
| empty output | exit 0 | `True` | ✅ |
| `"(no output)"` | exit 0 | `True` | ✅ |
| arbitrary success-looking text | unknown | `True` | ✅ per contract (absence of prefix = exit 0) |

---

## 8. Authority Map

```text
ToolExecutor                     KNOWS?  yes — it has the int exit code
    |                            ROLE:   transport + structured metadata producer
    v
tool result (JSON envelope)      KNOWS?  carries it (data text + metadata.exit_code)
    |                            ROLE:   transport
    v
event producer (events.py:240)   KNOWS?  no — pass-through by design
    |                            ROLE:   transport
    v
event schema {name,result,...}   KNOWS?  no
    |                            ROLE:   transport
    v
evidence adapter                 KNOWS?  NO — it *assumes* `result` is text
  stateless.py:920-930           ROLE:   TRANSFORM, and this is where failure is LOST
    |                            CAN MANUFACTURE SUCCESS: yes, and it does
    v
VerificationFloorGuard           KNOWS?  yes — given the contract's representation
  verification.py:138-157        ROLE:   INTERPRETATION AUTHORITY (the only one)
    |                            CAN MANUFACTURE SUCCESS: no, it is faithful
    v
P3 CompletionCriteria            KNOWS?  no — reads `resolved()`
  verification.py:225-253        ROLE:   projection
    v
CompletionVerdict (acceptance)   KNOWS?  no — pure judge over criteria + evidence
  acceptance.py:216              ROLE:   projection
    v
GoalState (goal.py)              KNOWS?  no — arbitrates the verdict
                                 ROLE:   precedence arbiter
```

**Transport authority vs interpretation authority.** The interpretation authority is
`VerificationFloorGuard` — a single, named, tested function. Everything upstream is transport.
The **one node that transforms** is the evidence adapter at `stateless.py:920-930`, and it is the
**only node that can manufacture success**. It does so not by lying but by **failing to unwrap**:
it forwards the envelope and lets a text parser answer a question about text that isn't there.

**`stateless.py:920-930` is the sole call site** of `note_tool_result` in production (verified by
tree-wide search). There is exactly one writer and exactly one reader.

---

## 9. Failure Information Preservation Matrix

| Boundary | Exit code present? | stdout present? | stderr present? | Structured? | Can failure be lost? |
|---|---:|---:|---:|---:|---:|
| `bash.py::_format_bash_output` | **yes** (`[exit code: 3]`) | yes | yes (in `data`) | no | no |
| `ToolExecutor._run_bash_tool` | **yes** (×2: `data` text **and** `metadata.exit_code` int) | yes | yes | **yes** (JSON) | no |
| runtime `tool_result` event | yes (verbatim) | yes | yes | yes | no |
| evidence adapter (`stateless.py:924`) | **yes in the string, but not at the front** | yes | yes | discarded (`str()`) | **YES — here** |
| `note_tool_result` → `_verify_result_is_success` | **LOST** | n/a | n/a | n/a | **YES — recorded `True`** |
| `VerificationFloorGuard.verify_ok_after_edit` | LOST | n/a | n/a | n/a | YES — `True` |
| P3 `acceptance_verdict` | LOST | n/a | n/a | n/a | YES — `pass` |
| `goal_state` | LOST | n/a | n/a | n/a | YES — `goal_met` |

**Located:** failure information is **preserved at every boundary until the evidence adapter, and
lost exactly once — at `stateless.py:924-925`.** Nothing downstream re-derives it, so the loss
propagates unchanged to the goal state.

---

## 10. False-Success Mechanism

**Proven, not inferred.** Three facts, each measured:

```text
FACT 1:  raw_result == result_event["result"] == the value passed to note_tool_result
         (all three byte-identical; captured at each boundary)

FACT 2:  the failure marker is INSIDE the string but NOT AT ITS FRONT
         "[exit code:" in envelope                      -> True
         envelope.startswith("[exit code:")             -> False
         inner_data.startswith("[exit code:")           -> True

FACT 3:  the parser tests startswith()
         _verify_result_is_success(envelope)            -> True
         _verify_result_is_success(inner_data)          -> False
```

The parser's docstring is explicit that it expects the **formatted command output**
(`verification.py:100-111`), and the joint-contract comment (`verification.py:91-97`) names
`_format_bash_output` as the other owner. The adapter supplies the **envelope**.

**A sharper statement of the defect:** it is *not* that the consumer mangled the value — the
consumer passes the tool result faithfully. It is that the consumer **assumed the tool result is
text**, while the executor's contract makes it a JSON envelope. "Pass the raw result instead"
is **not** a fix: the raw result *is* the envelope (FACT 1).

---

## 11. Negative Controls

Ten representations, classified by the real `_verify_result_is_success`:

| Control | Input | Classification | Correct? |
|---|---|---:|---|
| A | raw tool string of a **successful** `run_bash` | `True` | ✅ |
| B | raw tool string of a **failing** `run_bash` | **`True`** | ❌ **false success** |
| C | the envelope actually passed in (a failure) | **`True`** | ❌ same as B — **B and C are byte-identical** |
| D | the **inner `data`** field of the failing envelope | `False` | ✅ ← **the correct value was available** |
| E | the inner `data` of a successful envelope | `True` | ✅ |
| F | bare `"[exit code: 3]"` | `False` | ✅ |
| G | bare `"[exit code: 0]"` | `True` | ✅ |
| H | `""` | `True` | ✅ |
| I | `"(no output)"` | `True` | ✅ |
| J | `"All tests passed."` | `True` | ✅ |

Controls B and C are identical because the raw result **is** the envelope. Control D is the
decisive one: **the correct answer was one dictionary lookup away, and the parser already gets it
right.** This is why the defect is a *wiring* defect, not a *decision* defect — and why the
repair must not touch the parser (§17, Option D).

---

## 12. Replay / Durability

| Question | Answer |
|---|---|
| Is the raw tool result persisted? | **Yes** — the session journal's `outcomes` section stores the envelope string, including `[exit code: 3]` |
| Is the event envelope persisted? | Yes, as `outcomes[].data` (a JSON string) |
| Is the verification evidence persisted? | Yes — `verdicts[]` **when `record_verdict` is on** (default off); otherwise via the goal record |
| Is `acceptance_verdict` persisted? | **Yes** — `goal_states[].acceptance_verdict = "pass"` |
| Is the actual exit code persisted? | **As text** (`outcomes[].data` contains `[exit code: 3]`). **Not as a typed int** — the `metadata.exit_code` int is dropped at the journal boundary |
| Can replay reconstruct the original verification result? | **It does not try.** Replay reads the recorded `acceptance_verdict` as a fact. |

**Observed replay behaviour:** the recorded record and the live derivation agree (`pass` /
`goal_met`) — replay is **faithful**.

**`REPLAY_CONTRACT_GAP = YES`, in one specific sense.** `acceptance_verdict` is a **derived
value persisted as an authoritative input**. Replay trusts it and never re-derives from
`outcomes`. Consequently:

- Replay of an existing record will keep producing `pass` **after** the live adapter is fixed.
- The corrected live code would produce `fail` for the same turn.
- So **live-after-fix ≠ replay-of-old-record** for every affected turn — a *historical*
  divergence, not a live/replay divergence.

The underlying evidence is durable enough to re-derive (`outcomes[].data` carries the failure
text), so a migration is *possible*; it is not automatic, and this phase does not authorise one.
For **new** turns the gap does not arise: the record will carry the corrected verdict.

---

## 13. Duplicate Authority Search

Tree-wide search for every implementation that decides "the command succeeded".

| # | Site | Encoding it reads | Verdict |
|---|---|---|---|
| 1 | `core/verification.py:100-116` `_verify_result_is_success` | **text prefix** `[exit code: N]` | **the turn-loop completion authority** — correct, but fed the wrong string |
| 2 | `tools/registry.py:880-886` `_build_tool_metadata` | **text substring** `"[exit code:" in result` | extracts a **typed int** into `metadata.exit_code`; **reads the right string** (the formatted output) — correct |
| 3 | `graph/verifier.py:39-51` `gate_tests_green` | **typed int** `evidence["exit_code"] == 0` | the **graph/fanout** verification authority; a *different subsystem* with a *typed* interface |
| 4 | `graph/reference.py:141` | **typed int** `inputs.get("exit_code")` | reference gate; same typed convention |

**Assessment.**

- There is **no duplicate of the turn-loop authority**: `_verify_result_is_success` is the only
  decision-maker for `verify_ok_after_edit`, and `note_tool_result` has exactly one production
  caller. The guard remains the single authority.
- But the tree carries **two encodings of the same fact**: a **text prefix** (sites 1, 2) and a
  **typed int** (sites 2, 3, 4). Sites 2/3/4 already prefer the typed int. **Site 1 is the only
  one that must parse text** — and it is the one being fed the wrong text.
- **Repairing site 1 will not create a competitor** and will not leave another authority
  disagreeing: no other site consumes the same input or answers the same question
  (`gate_tests_green` is fed *tool-measured* evidence by its own docstring, on a different path).
- **A latent convergence worth recording:** the typed int is already produced
  (`metadata.exit_code`) and already preferred elsewhere. A future phase *could* move site 1 to
  the typed boundary — but that is **Option B**, it changes the guard's input contract, and it is
  **not** the minimal repair (§17).

---

## 14. P3 Impact

**Proven: this is a verification-layer defect.** The projection is faithful at every step.

```text
VerificationFloorGuard.verify_ok_after_edit = True          <- WRONG INPUT, set upstream
    -> resolved() = wrote_code and verify_ok_after_edit is True = True
        -> floor_guard_criteria check: (not wrote_code) or resolved() = True
            -> floor_guard_evidence: {wrote_code: True, verify_ok_after_edit: True, ...}
                -> acceptance.evaluate(...) = PASS
```

- `floor_guard_criteria` (`verification.py:225-253`) is a pure function of guard state. Given
  `verify_ok_after_edit=True`, `resolved()` is `True` and the criterion is satisfied. **The
  projection adds no error of its own.**
- `evaluate` (`acceptance.py:216-237`) is a pure judge over criteria + evidence; rule 2
  ("a failing DETERMINISTIC criterion → FAIL") is not reached because no criterion failed.
- Feeding the *correct* input through the same code produces the *correct* output — demonstrated
  by control D (§11) and by the **counterfactual in the predecessor phase**: with the inner
  `data` the criterion is unsatisfied → `FAIL`.

**`P3_IMPACT: PROVEN — verification-layer defect. The P3 projection is not implicated and must
not be changed.`**

**Adjacent observation (not the defect, reported because it is now measurable).** The projection
cannot express *"the verification did not run"*. A mutating turn whose verification is **denied**
leaves `verify_ok_after_edit = None`; the criterion is `(not wrote_code) or resolved()` → `False`
→ **FAIL** → `goal_failed`, while `turn_succeeded` is `true`. Measured in the AUTO_EDIT case:
`acceptance_verdict: "fail"`, `goal_state: "goal_failed"`. `INCONCLUSIVE` is ADR-0035's state for
"could not tell", but this criterion has no path to it. This is a **denial-vs-failure** semantics
question, distinct from the false success, and it is **not** resolved here.

---

## 15. Goal-State Impact

```text
P3 FAIL  ->  GOAL_FAILED     (verified)
P3 PASS  ->  GOAL_MET        (verified)
```

The arbiter consumes `acceptance_verdict` at `runtime.py:1234-1244`; the record at
`runtime.py:1245-1270` persists the inputs alongside the answer. The recorded
`stagnation_allows_goal_met` is taken from the same computation the arbiter used (F35's fix,
`runtime.py:1265`).

**`GOAL_MET` is merely faithfully consuming an incorrect P3 verdict.**

```text
GOAL_STATE_AUTHORITY: PRESERVED
```

`core/goal.py` is **not** implicated and **must not be modified**. The precedence table is
correct; it was handed a verdict that was wrong before it arrived.

---

## 16. Security Boundary Analysis

**The defect is confined to verification and completion. It is not a security-boundary failure.**

| Question | Answer | Evidence |
|---|---|---|
| Can it authorize a forbidden command? | **No** | authorization runs before execution (`SecurityPolicy.check` + `ApprovalGate`, pre-dispatch at `stateless.py:635-656`); the guard runs *after* results return |
| Can it bypass approval? | **No** | same — approval is resolved pre-dispatch; the guard cannot reach it |
| Can it bypass `SecurityPolicy`? | **No** | the guard has no reference to the policy |
| Can it execute additional tools? | **No** | the guard has no execution surface. `resolved()` is read at `stateless.py:864` (auto-skill capture) and by the completion gate; `rejection()` at `:794`. Neither dispatches a tool |
| What can it corrupt? | **completion evidence only** | `verify_ok_after_edit` → P3 → goal state |

**Concepts kept separate:** `authorization` (unaffected) · `approval` (unaffected) ·
`execution` (unaffected) · `verification` (**this defect**) · `completion` (**corrupted
downstream of verification**).

**Consequence of the false success, stated plainly:** the guard believes a mutating turn is
verified, so it **does not nudge the model to re-verify** and it **does not block completion**.
A model that writes broken code and "verifies" it with a failing command receives neither the
`HARNESS_REJECTION` intervention nor a `GOAL_STAGNATED`/unverified outcome. The failure is
silent — which is precisely why it matters under ADR-0035/0036/0037, where completion honesty is
the whole contract.

---

## 17. Architectural Options

Not selected, not ranked. Each is stated with what it would require.

### Option A — Fix evidence extraction at the call site

Unwrap the tool result before handing it to the guard: pass the envelope's **`data`**
(the formatted command output the parser documents), not the envelope.

```text
AUTHORITY_IMPACT:        none — the guard remains the only interpretation authority
SECURITY_IMPACT:         none — verification/completion only (§16)
SCHEMA_COMPATIBILITY:    n/a
REPLAY_IMPACT:           new turns correct; existing records unchanged (§12)
OFFLINE_IMPACT:          none
PACKAGING_IMPACT:        none
TEST_SCOPE:              one integration test (a real failing run_bash -> not resolved)
ADR_REQUIRED:            NO — restores an already-documented contract at the existing boundary
```

### Option B — Make the guard consume a structured result

Move success classification to a typed boundary (e.g. `metadata.exit_code`), retiring the text
parser.

```text
AUTHORITY_IMPACT:        moves the input contract of the verification authority
SECURITY_IMPACT:         none
SCHEMA_COMPATIBILITY:    n/a
REPLAY_IMPACT:           changes what evidence means; the recorded verdict's derivation changes
OFFLINE_IMPACT:          none
PACKAGING_IMPACT:        none
TEST_SCOPE:              rewrites tests/test_verification_contract.py (the formatter↔gate pin)
ADR_REQUIRED:            YES — changes the verification evidence contract and unpins a
                         documented joint contract
```

### Option C — Fix the event schema / producer

Have the producer emit the formatted text as `result` and the envelope elsewhere.

```text
AUTHORITY_IMPACT:        none directly
SECURITY_IMPACT:         none
SCHEMA_COMPATIBILITY:    n/a
REPLAY_IMPACT:           changes the persisted `outcomes[].data` shape
OFFLINE_IMPACT:          none
PACKAGING_IMPACT:        none
TEST_SCOPE:              broad — every consumer of `result_event["result"]` and the history
                         serializer (stateless.py:956-964) must be revisited
ADR_REQUIRED:            YES — changes the event contract
```

### Option D — Teach `_verify_result_is_success` to understand the envelope

Make the parser accept both the formatted text and the envelope.

```text
AUTHORITY_IMPACT:        none in name, but the authority now accepts two encodings
SECURITY_IMPACT:         none
SCHEMA_COMPATIBILITY:    n/a
REPLAY_IMPACT:           none
OFFLINE_IMPACT:          none
PACKAGING_IMPACT:        none
TEST_SCOPE:              extends the contract pin to a second representation
ADR_REQUIRED:            YES — the parser's contract is documented as owned jointly with
                         `_format_bash_output` and pinned by test; accepting a second encoding
                         is a contract change, and it leaves the call site's assumption in place
```

### Option E — Move verification authority

Give the executor (or the event layer) ownership of the success fact.

```text
AUTHORITY_IMPACT:        a new verification authority; directly contradicts ADR-0035/ADR-0018's
                         single-floor rule ("runtime re-derives ... would be a second authority",
                         ADR text at WISP_ARCHITECTURE_DECISIONS.md:524)
SECURITY_IMPACT:         none
SCHEMA_COMPATIBILITY:    n/a
REPLAY_IMPACT:           the recorded authority would change
OFFLINE_IMPACT:          none
PACKAGING_IMPACT:        none
TEST_SCOPE:              large
ADR_REQUIRED:            YES — and it is the option the existing ADRs already reject
```

---

## 18. Tradeoff Analysis

| Criterion | A: fix extraction | B: structured guard input | C: fix event schema | D: teach the parser | E: move authority |
|---|---|---|---|---|---|
| Authority preserved | **yes** — unchanged | contract moved | yes | yes (widened) | **no** — new authority |
| Failure-information preservation | **restored** | restored | restored | restored | restored |
| Replay compatibility | existing records keep old verdicts (§12) | verdict derivation changes | persisted shape changes | unchanged | recorded authority changes |
| Backward compatibility | **full** | breaks the formatter↔gate pin | breaks consumers | accepts both | breaks ADR-0035/0018 |
| Minimality | **one expression** | one function + tests | producer + all consumers | one function | subsystem |
| Testability | one integration test | rewrite the pin | broad | extend the pin | large |
| Typedness | text (as today) | **typed** | text | text | — |
| Event-contract stability | **stable** | stable | **changed** | stable | stable |
| P3 compatibility | unchanged | unchanged | unchanged | unchanged | unchanged |
| Goal-state compatibility | unchanged | unchanged | unchanged | unchanged | unchanged |
| Security independence | **preserved** | preserved | preserved | preserved | preserved |
| ADR required | **NO** | YES | YES | YES | YES |

The load-bearing distinction: **A changes no contract.** It makes one call site conform to a
contract that is already documented in two places and already pinned by a test. B, C, D and E
each change a contract — the guard's input, the event payload, the parser's accepted encodings,
or the owner of the fact.

---

## 19. ADR Requirement

```text
ADR_REQUIRED: NO
```

**Reasoning against §21's test.**

An ADR is required when the repair changes verification authority, an event contract, a new
evidence authority, P3 semantics, completion semantics, replay semantics, or a denial/failure
class.

- **Verification authority** — unchanged. `VerificationFloorGuard` remains the sole
  interpretation authority; `note_tool_result` remains the sole writer (§13).
- **Event contract** — unchanged. The event keeps carrying the tool result verbatim (§6).
- **New evidence authority** — none introduced.
- **P3 semantics** — unchanged. `floor_guard_criteria` / `evaluate` are not touched (§14).
- **Completion semantics** — unchanged. `resolved()` keeps its definition; it will simply be
  computed from a truthful input.
- **Replay semantics** — unchanged *for new turns*. Existing records retain their recorded
  verdicts, which is what "the record is the authority" means (§12). No replay rule changes.
- **Denial/failure classes** — none introduced. Option A does not add a class.

The intended contract is **explicit in three places at once**:

1. `verification.py:91-97` — "The shell-result success encoding, owned jointly by this gate and
   `wisp/tools/bash.py::_format_bash_output`".
2. `verification.py:100-111` — the parser's docstring, describing the formatted output.
3. `tests/test_verification_contract.py` — driving `_format_bash_output` into the guard,
   "as production does".

Statement (3) is the sharpest evidence that this is a mechanical defect: **every test obeys the
contract, and the one production call site does not.** Source and documentation agree; the
implementation at a single site does not conform. That is the definition of a mechanical repair.

**If a future phase chooses B, C, D or E, an ADR is required**, and the numbering protocol
applies (append-only; `CURRENT_MAX_ADR: 0037`; next sequential `0038`).

---

## 20. Implementation Boundary

```text
IMPLEMENTATION AUTHORIZED: YES (for the Option-A repair only, in a separate phase)
ADR REQUIRED:              NO

PRODUCTION FILES EXPECTED:
  - wisp/core/stateless.py        (the evidence adapter, :920-930 — the extraction only)

TEST FILES EXPECTED:
  - tests/reliability/…           (one integration test: a real failing run_bash through the
                                   live runtime must NOT set verify_ok_after_edit / resolved()
                                   / a PASS verdict)
  - tests/test_verification_contract.py  (unchanged — it already pins the correct contract)

AUTHORITY THAT MUST NOT MOVE:
  - VerificationFloorGuard              (the interpretation authority)
  - SecurityPolicy / ApprovalGate       (authorization, approval)
  - ToolExecutor                        (execution)
  - core/acceptance.py, core/goal.py    (P3 projection, goal precedence)

CONTRACTS THAT MUST NOT CHANGE:
  - the shell-success encoding "[exit code: N]" and _format_bash_output
  - the tool-result envelope shape {status, tool, data, metadata}
  - the event payload {name, result, …}
  - the goal-state record fields, incl. stagnation_allows_goal_met
  - the recorded `acceptance_verdict` semantics for existing records
```

**Two implementation sub-decisions that belong to the repair phase, not to this recon:**

1. **Which field to unwrap.** The envelope's **`data`** is the representation the parser
   documents; unwrapping it restores the contract exactly. (The envelope's `metadata.exit_code`
   typed int is available and is what sites 2/3/4 prefer — but consuming it moves the guard's
   input contract, i.e. **Option B**, which needs an ADR.)
2. **What a non-`ok` envelope means.** In the live path, denials never reach the fold — the
   pre-dispatch gate marks the call `_blocked` and `stateless.py:889-898` `continue`s before
   `note_tool_result` (§13, measured). A defensive treatment of non-`ok` statuses would be
   hardening, but if it changes what a denial means for verification it collides with the
   adjacent `denial → FAIL` question (§14) and would need a decision.

**Explicitly out of the authorised boundary:** the nullable-argument contract, `additionalProperties`,
`thin_tools`, the ACP path, M13/stagnation, recovery, graph/fanout, `goal.py` precedence,
`turn_succeeded`, and the `denial → FAIL` semantics.

---

## 21. Non-Goals

Not investigated beyond what the trace required, and **not modified**:

F8 · nullable arguments · `additionalProperties` · `thin_tools` · ACP · M13 · stagnation ·
recovery · graph · fanout · goal-state precedence · `turn_succeeded`.

The verification defect does **not** cross any of these boundaries (§16), so none of them is
implicated. The one adjacent behaviour discovered — denial projecting to `FAIL` rather than
`INCONCLUSIVE` (§14) — is **reported, not repaired**, and is a separate decision.

---

## 22. Risks

| Risk | Assessment |
|---|---|
| The repair changes the recorded `acceptance_verdict` for existing sessions | **Real but expected.** Replay is faithful to the record; historical records stay as recorded. A migration would be a separate decision (§12) |
| A naive repair "passes the raw result instead" | **Would not fix it.** The raw result *is* the envelope (control B ≡ control C, §11) |
| A repair that widens the parser instead | **Leaves the call-site assumption in place** and changes a pinned contract (Option D) |
| A repair that reads `metadata.exit_code` | **Is Option B**, not Option A — it moves the guard's input contract and needs an ADR |
| The guard's unit tests are green and will stay green | **Expected** — they feed the documented contract. The regression must be an *integration* test; a unit test of `_verify_result_is_success` cannot detect this class of defect |
| The false success masks other defects | **Already observed once** — the F8 provisioning phase exposed this only because tools could finally execute. Fixing it may expose more of the same class |
| The adjacent `denial → FAIL` behaviour is mistaken for this defect | **Keep separate.** It is the opposite direction (a false *failure*), it has a different cause (the guard's inability to say "unrun"), and it is not in this repair's boundary |

---

## 23. Exit Criterion

```text
=== EXIT CRITERION ===
FALSE_SUCCESS_REPRODUCED:        YES   (live turn: exit 3 -> P3 pass -> goal_met)
ROOT_CAUSE_IDENTIFIED:           YES   (stateless.py:924-925 — the envelope passed as text)
RAW_RESULT_CONTRACT:             DOCUMENTED  (§5 — a JSON envelope string)
EVENT_CONTRACT:                  DOCUMENTED  (§6 — verbatim pass-through; envelope is intended)
EVIDENCE_CONTRACT:               DOCUMENTED  (§7 — formatted output, not the envelope)
AUTHORITY_MAP:                   COMPLETE    (§8 — one interpretation authority, one transform)
FAILURE_INFORMATION_LOSS:        LOCATED     (§9 — lost once, at the evidence adapter)
P3_IMPACT:                       PROVEN      (§14 — verification-layer, projection faithful)
GOAL_IMPACT:                     PROVEN      (§15 — faithful consumer; goal.py untouched)
REPLAY_IMPACT:                   DETERMINED  (§12 — REPLAY_CONTRACT_GAP = YES, historical only)
SECURITY_IMPACT:                 DETERMINED  (§16 — confined to verification/completion)
DUPLICATE_AUTHORITY_SEARCH:      COMPLETE    (§13 — 4 sites, 2 encodings, no duplicate authority)
ADR_REQUIREMENT:                 DETERMINED  (§19 — NOT required for Option A)
IMPLEMENTATION_BOUNDARY:         DETERMINED  (§20)
PRODUCTION_CHANGES:              0
TEST_CHANGES:                    0
EXIT: COMPLETE
=======================
```

---

## 24. Final Status

```text
=== STATUS: COMPLETE ===
PHASE: POST-M13-VERIFICATION-EVIDENCE-AUTHORITY-RECON
MODE: FORENSIC-READ-ONLY
PRODUCTION_CHANGES: 0
TEST_CHANGES: 0
CONFIG_DEFAULT_CHANGED: 0

FALSE_SUCCESS_REPRODUCED: YES
ROOT_CAUSE: core/stateless.py:924-925 — result_event["result"] is the tool's JSON
            envelope; the verification parser's contract is the FORMATTED output text
VALIDATION_AUTHORITY: core/verification.py::VerificationFloorGuard (single writer,
            single caller; unchanged and correct)
EVIDENCE_ADAPTER: core/stateless.py:920-930 (the only transform; the only loss point)
SECURITY_BOUNDARY: PRESERVED (defect confined to verification + completion)
TOOLEXECUTOR_BOUNDARY: unaffected; produces a correct, information-complete result
APPROVAL_ORDER: validation -> authorization -> approval -> execution (unchanged)
P3_IMPACT: verification-layer defect; the projection is faithful
GOAL_IMPACT: faithful consumer; core/goal.py MUST NOT change
REPLAY_CONTRACT_GAP: YES (historical records retain the pre-fix verdict; replay is faithful)
DUPLICATE_SUCCESS_PARSERS: 2 encodings (text prefix; typed int) across 4 sites;
            no duplicate of the turn-loop authority
FALSE_FAILURES: 1 adjacent (denied verification -> P3 FAIL / goal_failed) — reported, not repaired

REPAIR_CLASS: IMPLEMENTATION (a single call site violating a documented, test-pinned contract)
ADR_REQUIRED: NO
MINIMAL_REPAIR_BOUNDARY: the evidence adapter at wisp/core/stateless.py:920-930 (extraction only)

IMPLEMENTATION_AUTHORIZED: YES — for the Option-A repair, in a separate phase
NEXT: authorise a reliability-phase implementation that unwraps the tool-result envelope at
      stateless.py:920-930 (the envelope's `data`), adds one integration test that drives a real
      failing run_bash through the live runtime and asserts NOT resolved / NOT PASS, and
      re-runs the P3 Stage-3b measurement — which is currently contaminated by this defect.
      A separate decision remains open on denial-vs-failure projection (INCONCLUSIVE vs FAIL).

EXIT: COMPLETE
========================
```
