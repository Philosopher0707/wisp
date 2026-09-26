# PHASE_STRING_SCAN_AUDIT.md — every test assertion that reads a `.py` source file as text

> **An audit, not a repair.** Nothing in this document changes a test. It reports what is there, how
> much of it is hazardous, and which conversions are worth doing — so the changes can be decided
> rather than applied wholesale to 39 files.
>
> Generated 2026-09-27 at `21bc8cc`.

---

## §1 — Why this audit exists

Three guards failed this session in the same way, and **all three were test guards**:

- `PHASE_DAG_RETIREMENT R1` — the defect-pin held a bug by matching its message.
- `PHASE_LAYER_B_BOUNDARY R2` — `assert "from .dag import DAGScheduler" in src`, which passes on a
  commented-out import and fails on an equivalent one reordered.
- `test_external_input_path` — `assert "approval_required" not in src`, which **failed on ADR-0066 R3's
  own comment** while a real read of the attribute, split across lines or via `getattr`, would pass.

Three in one session, all in tests, is a pattern rather than a coincidence. This audit measures how
large it is.

## §2 — The method, and why the filter matters

An AST walk over every file in `tests/`, finding assertions of the form
`assert "<literal>" in <expr>` / `not in <expr>` where `<expr>` is **bound to a `read_text()` /
`getsource()` result**. Parsing, not scanning — a scan would have counted its own docstring.

**The filter is the whole finding.** Of the raw matches, the split is:

| what the assertion reads | count | verdict |
|---|---|---|
| a **`.py` source file** | **125** across **39 files** | the hazardous class — a code *construct* asserted as text |
| a data file, JSON, prompt, or output | 101 | benign — the text *is* the subject |

Conflating them would have produced a scare number. The 125 is the one that matters, and it is still
large.

## §3 — Not all 125 are defects. The rule.

The hazard is not "matching text". It is **asserting a code construct by its spelling**. Two families
sit inside the 125 and only one is a defect:

**Hazardous — a construct as text.** Passes on a commented-out line; fails on a reordered, rewrapped,
or renamed-but-equivalent form; cannot see a second copy elsewhere.

| fragment | where | what it really asserts |
|---|---|---|
| `'turn_succeeded = _goal_outcome is TerminalOutcome.SUCCEEDED'` and the shorter `'_goal_outcome is TerminalOutcome.SUCCEEDED'` | **seven files assert on it — and eight files contain it** | an assignment's spelling |
| `'async def apply_patch'`, `'apply_patch('` | `test_unwired_controls_inventory` | a function exists |
| `'self.policy = policy'`, `'effective_policy=self.policy,'` | 3 files | an assignment + a kwarg |
| `'_connect_task.cancel()'`, `'await self._connect_task'` | `test_tui_task_ownership` | a statement |
| `'declared_gate: Any = None'`, `'bool(declared_gate())'`, `'except Exception'` | `test_acceptance_gate_enablement` | an annotation, a call, a handler |
| `'def _effective_principal'`, `'def set_mcp_manager'` | 2 files | a `def` |
| `'if turn_succeeded:'`, `'if _event_has_payload(normalized):'` | 2 files | a statement |
| `'get_setting("permission_mode", PermissionMode.AUTO_EDIT.value)'` | `test_unwired_controls_inventory` | a call |

**Legitimate — prose as text.** The assertion is *about* the words: a docstring's claim, a citation,
a prompt. These should stay string matches, because the text is the artefact.

| fragment | why it is fine |
|---|---|
| `'No network, ever'` | a docstring's own claim (ADR-0058 R6) |
| `'ADR-0055'`, `'ADR-0004'`, `'authorization_parity_measurement.py'` | citations in a docstring |
| `'journal first, blob as the fallback'`, `'pre-P0'` | a docstring's own words |
| `'Use web_search to find a valid URL'` | prompt text — the string *is* the product |
| `'remove the block'`, `'no provider or tool_executor'` | prose |
| `'approval-required verdicts deny'` | a docstring clause |

**Ambiguous, and worth a look rather than a conversion:** `'TERMINAL_TYPES'`, `'NON_PAYLOAD_TYPES'`,
`'"--workspace"'`, `'"task_graph", False'`. These assert a *name or literal* rather than a statement.
A name check is weaker than an AST check but far less brittle than a statement check; they are not the
worst offenders and are listed separately for that reason.

**My estimate of the hazardous subset: roughly 60 of the 125.** I am not going to state it more
precisely than that without reading all 39 files, and this document does not claim to have.

## §4 — The worst files, by count

| file | source-text assertions |
|---|---|
| `tests/test_provider_listing_equivalence.py` | 11 |
| `tests/test_unwired_controls_inventory.py` | 11 |
| `tests/test_tui_task_ownership.py` | 8 |
| `tests/reliability/test_acceptance_gate_enablement.py` | 7 |
| `tests/reliability/test_next_autonomous_wiring.py` | 7 |
| `tests/reliability/test_criteria_source_on_turn_path.py` | 5 |
| `tests/test_acceptance_verdict.py` | 5 |
| `tests/test_m4_governance_wiring.py` | 5 |
| `tests/test_structured_delegation.py` | 5 |

**The one that repeats across files** — `turn_succeeded = _goal_outcome is TerminalOutcome.SUCCEEDED`,
and the shorter `_goal_outcome is TerminalOutcome.SUCCEEDED` — is the single highest-value conversion.

**Measured twice, and the two counts disagree — reported rather than reconciled.** The AST walk finds
it **asserted in seven files**; a plain substring search finds it **present in eight**
(`test_outcome_classification_delegation.py` contains it without matching the audit's
`assert <literal> in <text>` shape). The gap is the point: **the AST figure is a floor**, because it
counts only assertions in one syntactic form. A rewrap in the source file breaks at least seven guards
at once, and possibly eight.

## §5 — What I would do, in order

1. **Convert the repeated fragment first.** One AST helper asserting the assignment's *shape*
   (`Assign` to `turn_succeeded` whose value is a `Compare` against `TerminalOutcome.SUCCEEDED`)
   replaces seven brittle text matches, in two spellings. Highest ratio of brittleness removed to code
   written.
2. **Convert the `NotIn` guards to AST.** `NotIn` on source text is the dangerous direction — it
   passes when the code moves elsewhere, which is exactly when you want it to fail.
   `test_provider_listing_equivalence`, `test_next_autonomous_wiring` and
   `test_post_m13_f43_f44_authority_convergence` are the ones to look at.
3. **Leave the prose assertions alone.** They are not defects, and converting them to AST would
   *lose* the check — a docstring's claim is only checkable as text.
4. **Then, and only then**, consider a guard. A rule that fails on any new `assert "<code>" in
   <source-text>` would be the self-maintaining form — but it cannot be written until steps 1–3 have
   established what the legitimate exceptions look like, or it will be a guard everyone bypasses.

**Step 4 is the real prize and it is not available yet.** This audit is the prerequisite: it separates
125 into ~60 hazardous and ~40 legitimate, which is what a rule needs in order to be non-vacuous
without being obstructive.

## §6 — What this document did not do

- **It changed no test.** No assertion was edited, no file was touched.
- **It did not read all 39 files.** The classification in §3 is from the fragments the AST walk
  printed; the ~60 estimate is an estimate and says so.
- **It did not count non-`assert` text matching** — `if "x" in src:` branches, `pytest.skip` guards,
  or matches inside fixtures. The 125 is assertions only, which is a floor for the pattern.
- **It did not audit `wisp/` itself.** The same pattern may exist in production code; this looked at
  `tests/` because that is where the three found defects were.
