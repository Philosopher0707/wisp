# PHASE M13 REPORT — The Stagnation Detector Runs on the Live Turn Path

Phase **M13** of the Persistent Graph Loop migration — the last of the five items M9 recorded as blocked
on it. P7 built the mechanism and deferred the wiring; this closes the wiring. Decision is **ADR-0034**;
tests are `tests/test_stagnation_live_wiring.py` (27).

## 1. The gap, verified

P7 built `ProgressSignal`, `StagnationDetector`, `route_to_recovery()` and `may_report_goal_met()`, and
made `config.graph_oscillation_guard` readable — the plan's own completion criterion. But **nothing in
`wisp/` constructs a detector**: grepping `wisp/**/*.py` for `StagnationDetector` returns only
`core/stagnation.py` itself. The flag was readable and unread.

## 2. F32 — an empty observation was read as "no progress"

`ProgressSignal.from_verdict_and_graph` takes a P3 verdict and a P4 graph. **Both are opt-in records
that default off** (`record_verdict`, `task_graph`). So on a default configuration the turn-end signal
is the *empty* observation on every turn. Reproduced before writing any wiring:

| Observation | Result |
|---|---|
| turn 1 | `UNKNOWN` |
| turn 2 | `trap_fired = True`, `may_report_goal_met() = False` |
| turn 3+ | **`STAGNATING`** |

A productive session was declared stagnating by its third turn, and blocked from reporting its goal met
from its second. That is the plan's *"flagging productive work as stagnated"* risk — rated `Low-medium` —
arriving in its worst form: not from a tuned threshold, but from a mechanism that **cannot represent
"I don't know"**. The signal type had no way to say "this observation carries nothing", so absence of
information was read as absence of progress.

This is why the phase could not be "just construct a detector in the loop". The obvious wiring is a
severe false positive on the default configuration.

## 3. F33 — the identity stagnation needs is the *action* identity

M9 recorded M13 as depending on M11 (node identity). Wiring it showed that claim narrows again:

- **M11's `TaskNode.work_unit` is the wrong identity for this question.** It is the protocol
  `tool_call_id`, and a repeated identical call gets a **fresh** one — so every repeat would look like
  new work. Pinned by `test_the_same_work_unit_again_is_not_progress`, whose failure message says so.
- **The right identity is `action_key(tool, args)`** — P1's canonical digest, already stamped on the
  journal.
- **And the runtime could not always see the arguments.** Verified: a scripted `read_file` round produced
  `error`, `tool_result`, `content`, `done` — **no `tool_call` event at all**. The engine refuses a call
  before dispatch and emits only the refusal, so the arguments never reach the runtime. A signal built
  from the reply alone cannot tell `read_file(a.txt)` from `read_file(b.txt)` — which flags a productive
  three-file read as stagnation.

## 4. The decision (ADR-0034)

1. **`ProgressSignal.work_units`** — the action identities observed. A new one is progress; the same one
   again is not.
2. **`ProgressSignal.is_empty`**, and `observe()` returns `UNKNOWN` for an empty observation without
   appending it, counting it flat, or feeding it to the trap. *"We learned nothing" is not "nothing
   changed".*
3. **`with_work()`** — the live-path fold-in, mirroring `with_artifact`. Accumulated rather than
   differential: progress is "we have learned something new at some point", and a delta comparison would
   call an A→B→A oscillation progress on every step.
4. **The detector is constructed per turn** in `run_turn`, gated by `config.graph_oscillation_guard`.
   Per **turn**, not per session: the question is "is this turn going round in circles?", and a detector
   spanning turns would compare one user request against the next — not the same task.
5. **The identity travels with the refusal.** `_refusal_result_event` — the one helper every refusal goes
   through — stamps `action_key`, computed by the same function the journal uses. A refused call emits no
   call event, so this is the only place its arguments can be observed. For an *allowed* call the runtime
   already read the call event; the two cover disjoint cases.
6. **Recorded, not enforced.** A new `STAGNATION` audit event, journal-only (ADR-0028's shape), written
   **only when the verdict is reached** — so a normal turn adds no record to any caller's log, which is
   what lets it run under the flag's declared default (`True`) instead of being opt-in like
   `record_verdict`/`task_graph`.

## 5. The M11 tripwire fired

`test_progress_signals_still_count_nodes_rather_than_name_them` failed with its written message:
*"ProgressSignal now names work units — M13 has started; update WISP_MIGRATION_STATUS.md §M13 and
PHASE_M11_REPORT.md"*. It was replaced by its inverse
(`test_the_progress_signal_now_names_work_units`), the P9/M15 pattern, and the M11 docs updated.

That is the second tripwire this migration has seen fire (the first was P9's, during M15) — and it fired
on the exact condition it named.

## 6. Two P7 tests used an information-free fixture

`test_a_repeat_is_detected` and `test_a_trap_firing_alone_blocks_goal_met` both used
`ProgressSignal(criteria_satisfied=0)` as their "an observation happened" fixture — which **is** the empty
observation. That is F32's conflation present in P7's own tests: they never distinguished "an observation
with no progress" from "no observation at all".

Both now use the existing `_flat()` fixture (`criteria_satisfied=0, completed_nodes=1, total_nodes=3`),
which carries information, and both assert exactly what they asserted before. An **update, not a
weakening** — and the conflation is named in a comment in each.

## 7. Item status

| # | Concern | Status | Evidence |
|---|---|---|---|
| 1 | The live path constructs the detector | `COMPLETE` | `test_a_repeated_exchange_is_recorded_as_stagnating` |
| 2 | The signal is built from state that is always present | `COMPLETE` | `test_the_signal_is_not_built_from_the_opt_in_records` |
| 3 | An empty observation is not evidence | `COMPLETE` | F32; 6 empty observations never stagnate |
| 4 | A repeated exchange is detected, a productive one is not | `COMPLETE` | two live-turn tests |
| 5 | The existing flag is the rollback switch | `COMPLETE` | `test_the_existing_flag_disables_the_record` |
| 6 | The record is durable and replayable | `COMPLETE` | `JOURNAL_ONLY_RECORDS` + `apply` + replay |
| 7 | Recording does not change the turn | `COMPLETE` | transcript equality, detector on/off |
| 8 | Routing to the recovery ladder | `DEFERRED — pinned` | AST tripwire on `ladder.decide` |
| 9 | Gating completion on `may_report_goal_met()` | `DEFERRED — pinned` | AST tripwire |

## 8. Completion criteria

- [x] The plan's item 1: the trap is reachable from the live loop, through the detector
- [x] The config flag that existed before the migration is now **read and obeyed**
- [x] A synthetic oscillation is detected on a real turn; a productive turn is not
- [x] **Zero new failures** — see §9
- [x] Rollback by the existing flag
- [x] Reachability (RULE 11): the record reaches the journal and replays
- [ ] `ruff` / `mypy` — not installed
- [ ] **Enforcement** (routing, goal-met gating) — deliberately deferred, tripwired

## 9. Regression

**Two full-suite runs on the final tree, byte-identical to each other and to the stable baseline: 129,
zero new, zero fixed, zero flaky.** Compared as **sets** under `LC_ALL=C` (F31), and independently
re-checked with a Python set diff — both directions empty for each run.

| Run | Set size | New | Fixed |
|---|---|---|---|
| `m13/final1.txt` | 129 | 0 | 0 |
| `m13/final2.txt` | 129 | 0 | 0 |
| intersection | 129 | 0 | 0 |

The intersection **and** the union both equal the stable baseline exactly, so the F17 flake — which is
*absent* from the baseline — appeared in neither run. The two runs' outputs are byte-identical to each
other, not merely set-equal, so the set is stable on this tree.

**How the two-run confirmation was completed.** The first attempt was interrupted by the disk filling
(F34); the re-run's second pass was then cancelled mid-flight, leaving `final2.txt` empty. The second
pass was re-run on its own and completed clean — which is what turns this from "one run" into the
intersection the method prescribes.

**The first attempt did find one real new failure**, and it is worth recording what it was:
`test_escalation_durability.py::TestTheStateBearingAuthority::test_the_journal_only_list_covers_every_audit_kind`
— M16's totality guard over the audit kinds, failing because M13 adds `STAGNATION` as an eighth audit
kind. The guard was right and was updated to declare the new kind. That is the second time this
migration's guards have caught a phase's own incompleteness.

The neighbouring suites were checked explicitly: `test_stagnation_detection` (P7),
`test_stagnation_live_wiring`, `test_node_identity` (M11), `test_execution_view_projection` (M9),
`test_durable_layer_reachable`, `test_task_graph_materialization` — **176 passed**, plus
`test_escalation_durability` at 40 passed after the guard update.

Re-measured on the final tree when the second pass was completed: the **full migration suite set
(24 files) is 714 passed**, and the eight M13-adjacent guards plus `test_doc_drift` are **228 passed**.

## 10. Honest limits

- **`min_consecutive` is still untuned** — P7's own limit, unchanged. With per-exchange observation it
  means "one baseline plus two flat observations", which P7's semantics already implied.
- **The `action_key` stamp covers refusals; an allowed call's identity comes from the runtime's own read
  of the call event.** Two producers for *disjoint* cases — stated rather than hidden. They compute the
  same value with the same function.
- **Nothing acts on the verdict.** A turn the detector declares stagnating still runs to its ceiling.
  That is the deferral, and the tripwires keep it visible.
- **The signal cannot see reasoning.** A turn that thinks productively without new tool outcomes looks
  flat; `min_consecutive` is the mitigation and it is untuned.
- **The record is not measured on real traffic.** It fires on the scripted turns the tests drive. Whether
  it is noisy on real sessions is the question `min_consecutive` needs a measurement to answer — the same
  measurement P3 stage 3b is blocked on.
