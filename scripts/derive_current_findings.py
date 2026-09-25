#!/usr/bin/env python3
"""Derive `CURRENT_FINDINGS.md` from the corpus.

**Why this is a committed script and not a one-off.** `CONTEXT.md` §0.0.9 records finding
**F75**: *"an instrument that cannot be committed is not a re-runnable measurement."* The
page this emits declares itself *derived, regenerated, never edited*. A "regeneration"
performed by hand is exactly the drift the page exists to prevent, so the derivation is
here, in `scripts/`, which is the repository's committed convention (`scripts/next_*.py`).

**What is derived and what is data.** The corpus is prose; a finding's *status* is a
sentence in a ledger row or a report. This script holds one row per finding — the status,
the class, the source, the tripwire — each transcribed from its source with the source's
own words quoted in the `source` cell. Nothing here is inferred from a title.

Run: `env -u PYTHONPATH .venv/bin/python scripts/derive_current_findings.py`
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "CURRENT_FINDINGS.md"

#: The status vocabulary. Seven words, and each is defined in §(a) of the page.
VOCABULARY = ("OPEN", "FIXED", "CLOSED", "SUPERSEDED", "DECIDED", "DEFECT-PIN", "UNRESOLVED")

#: The defect classes, and where each class is stated. §(b) indexes findings into these.
CLASSES: dict[str, str] = {
    "instrument-defect":
        "`CONTEXT.md` §10 — six sub-cases; the instrument reports its own defect as a "
        "result about the subject",
    "model-vs-path":
        "`CONTEXT.md` §10 sub-case 4 — a right answer about the wrong subject",
    "unwired-control":
        "`CONTEXT.md` §0e — a mechanism with no caller, or an artifact written and never read",
    "record-integrity":
        "two records of one fact, and the one readers consult is stale (F81/F82/F98/F100)",
    "count-canonicality":
        "a quoted count that is not the measured set (F71/F82/F85/F93)",
    "measurement-method":
        "the regression or baseline method itself was unsound (F12/F20/F31/F36/F94)",
    "false-success":
        "a failing outcome recorded as success (F37/F58/F66 MODE A)",
    "duplicated-authority":
        "two producers of one structure (F6/F23/F25/F42)",
    "record-gap":
        "a finding cited whose source does not record it (F77, until `PHASE_CORPUS_GOVERNANCE_II.md` §4 pinned it)",
}

#: `(id, title, status, class, decided_by, source, tripwire)`.
#:
#: `source` is `path:line` and **quotes the source's own status words**, so the register
#: cannot paraphrase a status into existence. `tripwire` is a test *file* (optionally
#: `::Class::test`) that fails if the state changes, or `—`.
ROWS: list[tuple[str, str, str, str, str, str, str]] = [
    # ── F1–F44 — WISP_MIGRATION_STATUS.md §23 (the F1–F44 table) ──────────────
    ("F1", "`test_canonical_execution_state.py` already exists — the audit implied a missing ratchet",
     "CLOSED", "", "—", "WISP_MIGRATION_STATUS.md:1838 — “Corrected; no work”", "—"),
    ("F2", "`RunStatus` ⊂ `RunState` — the audit claimed a divergence needing a shim",
     "CLOSED", "duplicated-authority", "ADR-0003", "WISP_MIGRATION_STATUS.md:1839 — “Corrected; no shim needed (ADR-0003)”", "—"),
    ("F3", "`Session.apply` had no `TOOL_CALL` case and no wildcard — unknown event types dropped silently",
     "FIXED", "", "ADR-0005", "WISP_MIGRATION_STATUS.md:1840 — “Fixed (P0.3, ADR-0005)”", "—"),
    ("F4", "The session event log held only `user_message`/`error`/`done`, yet was the documented replay source",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:1841 — “Fixed (P0.2)”", "—"),
    ("F5", "A stale `# Cache result for idempotency (1h TTL)` comment with no code beneath it",
     "FIXED", "unwired-control", "ADR-0010", "WISP_MIGRATION_STATUS.md:1842 — “Resolved in P1 … idempotency implemented at the action level instead (ADR-0010)”", "—"),
    ("F6", "Two disjoint decision models coexist: `auth.decision.authorize()` and `infra.security.SecurityPolicy.check()`",
     "OPEN", "duplicated-authority", "—", "WISP_MIGRATION_STATUS.md:1843 — “Pre-existing; out of P0 scope”", "—"),
    ("F7", "`SessionRepository.append_events` was dead code that could not work (`transaction()` yields the store, not a connection)",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:1844 — “Fixed in `session_repo.py`”", "—"),
    ("F8", "Six declared dependencies missing from the venv; a missing validator degraded into a total tool outage",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:1845 — headline “NOT FIXED — environment gap”, the same row's later text “**FIXED 2026-09-24** — provisioned offline … 0 production changes”", "—"),
    ("F9", "`_persist_turn_state`'s 6-parameter signature is a pinned contract",
     "CLOSED", "", "—", "WISP_MIGRATION_STATUS.md:1846 — “Respected — journaling moved to a separate method instead”", "—"),
    ("F10", "`test_13h2_determinism.py`'s six failures attributed to baseline",
     "CLOSED", "measurement-method", "—", "WISP_MIGRATION_STATUS.md:1847 — “**CORRECTED 2026-09-24** … they were F8-caused, not pre-existing. The file now reads **39 passed, 0 failed**”", "—"),
    ("F11", "`tests/test_api_key_security.py` fails at collection (`TestClient` needs `httpx`)",
     "OPEN", "", "—", "WISP_MIGRATION_STATUS.md:1848 — “Pre-existing; environment gap”", "—"),
    ("F12", "Baseline methodology: stashing only the touched files reverts pre-existing work and fabricates regressions",
     "CLOSED", "measurement-method", "—", "WISP_MIGRATION_STATUS.md:1849 — “Recorded; every regression delta explained individually”", "—"),
    ("F13", "P0's first journaling implementation produced a provider-invalid transcript",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:1850 — “Fixed; guarded by `test_interrupted_turn_replays_into_a_provider_valid_transcript`”", "tests/test_turn_journal_incremental.py"),
    ("F14", "The layered authorization verdict was recorded only for denials, and only as prose",
     "FIXED", "", "ADR-0013", "WISP_MIGRATION_STATUS.md:1851 — “Fixed for both paths (ADR-0013)”", "—"),
    ("F15", "A `read_only` denial is decided before the `authorize()` consult, so it names no controlling layer",
     "CLOSED", "", "ADR-0014", "WISP_MIGRATION_STATUS.md:1852 — “Pinned in the corpus with an explanatory comment (ADR-0014)”", "tests/test_gate_order_corpus.py"),
    ("F16", "`contracts/tool.py`'s `ToolRequest`/`ToolResult` were producer-less and consumer-less; `PolicyDecisionEnvelope` still is",
     "OPEN", "unwired-control", "—", "WISP_MIGRATION_STATUS.md:1853 — “**Fixed for tool** …; `PolicyDecisionEnvelope` still unwired”", "—"),
    ("F17", "`test_speculative_search.py::TestOracle::test_smallest_diff_wins_ties_broken_by_speed` is flaky under the full suite",
     "OPEN", "measurement-method", "—", "WISP_MIGRATION_STATUS.md:1854 — “Logged; **not** caused by any phase”", "—"),
    ("F18", "`graph/scheduler.py::ready_nodes` recomputed readiness every pass and stored nothing",
     "FIXED", "", "ADR-0019, ADR-0020", "WISP_MIGRATION_STATUS.md:1855 — “Fixed (ADR-0019/0020)”", "—"),
    ("F19", "“Three tests P5's completion criteria require do not exist” — they do; all three are tracked",
     "SUPERSEDED", "record-integrity", "ADR-0060", "WISP_MIGRATION_STATUS.md:1856 — “Verified absent; recorded”; **superseded by F102** (`PHASE_LAYER_B_BOUNDARY.md:281`), which measured them present and predating P5", "tests/reliability/test_layer_b_boundary.py"),
    ("F20", "The full-suite failure set is not stable — a single-run count is not a baseline",
     "CLOSED", "measurement-method", "—", "WISP_MIGRATION_STATUS.md:1857 — “Method fixed: the baseline is now the **intersection of two runs**”", "—"),
    ("F21", "`_fit_sections` records truncation as prose, not as structured data",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:1858 — “Fixed: `Context.dropped` is structured”", "—"),
    ("F22", "`SubagentContract` had no `metadata` field, and the orchestrator *reads* it — a latent crash",
     "FIXED", "unwired-control", "—", "WISP_MIGRATION_STATUS.md:1859 — “Fixed: `metadata` field added”", "—"),
    ("F23", "`multi_agent/_circuit_breaker.py` is a duplicate authority",
     "OPEN", "duplicated-authority", "—", "WISP_MIGRATION_STATUS.md:1860 — “Documented; **not deleted** (the user's untracked WIP)”", "—"),
    ("F24", "M4's blob fallback discarded a surviving escalation",
     "FIXED", "", "ADR-0028", "WISP_MIGRATION_STATUS.md:1861 — “Fixed (ADR-0028); `reconstruct()` salvages on both paths”", "—"),
    ("F25", "The replayed transcript was not the live transcript",
     "FIXED", "duplicated-authority", "ADR-0029", "WISP_MIGRATION_STATUS.md:1862 — “Fixed (ADR-0029); equality is now asserted on a real turn”", "tests/test_session_reconstruction.py"),
    ("F26", "The obvious subagent-authority fix would have leaked thread pools",
     "CLOSED", "", "—", "WISP_MIGRATION_STATUS.md:1863 — “Avoided: the principal travels with the call; ratcheted by `test_the_runner_does_not_construct_a_tool_executor`”", "tests/reliability/test_post_m13_authority_implementation.py"),
    ("F27", "A live T1 violation: workspace-file content sat before the system prompt",
     "FIXED", "", "ADR-0031", "WISP_MIGRATION_STATUS.md:1864 — “Fixed (ADR-0031): classified and moved to the important tier”", "tests/test_prompt_section_trust.py"),
    ("F28", "The engine's own refusals were invisible to the denial predicate, so they were retried",
     "FIXED", "", "ADR-0032", "WISP_MIGRATION_STATUS.md:1865 — “Fixed (ADR-0032): `_ENGINE_DENIAL_PREFIXES`”", "tests/test_failure_signal_classification.py"),
    ("F29", "M9's payload ratchet forbade what M9's own report says M11 requires",
     "FIXED", "duplicated-authority", "ADR-0033", "WISP_MIGRATION_STATUS.md:1866 — “Fixed (ADR-0033): the property kept, the mechanism replaced”", "tests/test_node_identity.py"),
    ("F30", "A name blacklist is evadable by naming — it cannot close the class it exists to close",
     "FIXED", "instrument-defect", "ADR-0033", "WISP_MIGRATION_STATUS.md:1867 — “Fixed (ADR-0033): `NODE_FIELD_KINDS` classifies every field”", "tests/test_node_identity.py"),
    ("F31", "`comm` on locale-sorted failure sets reports identical sets as different",
     "CLOSED", "measurement-method", "—", "WISP_MIGRATION_STATUS.md:1868 — “Method fixed: compare as **sets** (`LC_ALL=C sort` on both, or a Python set diff)”", "—"),
    ("F32", "An empty observation was read as “no progress”, declaring every multi-turn session stagnant",
     "FIXED", "false-success", "ADR-0034", "WISP_MIGRATION_STATUS.md:1869 — “Fixed (ADR-0034): `ProgressSignal.is_empty`; `observe()` refuses an empty observation”", "tests/test_stagnation_detection.py"),
    ("F33", "The identity stagnation needs is the *action* identity, and the runtime could not always see it",
     "FIXED", "", "ADR-0034", "WISP_MIGRATION_STATUS.md:1870 — “Fixed (ADR-0034): `work_units` + the refusal stamp in `_refusal_result_event`”", "tests/test_stagnation_detection.py"),
    ("F34", "The disk filled during M13's two-run regression confirmation",
     "CLOSED", "measurement-method", "—", "WISP_MIGRATION_STATUS.md:1871 — “Recovered: the re-run completed clean and the intersection equalled the stable baseline exactly”", "—"),
    ("F35", "The goal arbiter and the goal record were computed from two different facts, so live and replay could disagree",
     "FIXED", "duplicated-authority", "ADR-0036", "WISP_MIGRATION_STATUS.md:1872 — “**FIXED** (ADR-0036 §6 …): the goal record carries `stagnation_allows_goal_met`, taken from the **same computation**”", "tests/reliability/test_post_m13_authority_implementation.py"),
    ("F36", "The full suite cannot run in one process on a memory-starved host; the sandbox host fallback makes `run_bash` tests environment-dependent",
     "OPEN", "measurement-method", "—", "WISP_MIGRATION_STATUS.md:1873 — “Environment, **not** caused by the phase”", "—"),
    ("F37", "A mutation followed by a *failing* verification was recorded P3 `PASS` / `GOAL_MET` — a false success",
     "FIXED", "false-success", "—", "WISP_MIGRATION_STATUS.md:1874 — headline “**NOT FIXED — newly exposed**”, root-caused there as *the evidence adapter*; **repaired** — `CONTEXT.md` §0 phase table records “**F37 evidence-adapter repair** — `COMPLETE` — **F37 FIXED**”", "tests/reliability/test_verification_evidence_adapter.py"),
    ("F38", "A test encoded the F8 environment as the contract",
     "DEFECT-PIN", "", "—", "WISP_MIGRATION_STATUS.md:1875 — “**NOT FIXED — `TEST_ASSUMED_BROKEN_ENVIRONMENT`**”", "tests/test_node_identity.py::TestANodeReferencesItsWorkUnit::test_a_parallel_round_is_journaled_as_one_exchange_per_call"),
    ("F39", "The Ollama client sends `num_predict` without negotiating the model's real limit, so a class of models fails outright",
     "CLOSED", "unwired-control", "ADR-0038", "WISP_MIGRATION_STATUS.md:1876 — headline “**NOT FIXED**”, root-caused there as `ADR_REQUIRED: YES`; **decided** — `CONTEXT.md` §0 phase table records “F39 token-budget boundary (**ADR-0038**)” as `COMPLETE` / “**`RATIFIED`**”, then “**`ADR-0038 SATISFIED`**”", "—"),
    ("F40", "The iteration wrap-up loop consumes RAW provider events and assumes dicts, so the wrap-up summary is lost",
     "CLOSED", "duplicated-authority", "ADR-0039", "WISP_MIGRATION_STATUS.md:1877 — “**CLOSED (PM-21)** — ADR-0039 decided it (PM-20) and the normalization boundary implemented it”", "tests/test_failure_signal_classification.py"),
    ("F41", "The 4xx error-body log never logged a real body, and the unit tests could not notice",
     "FIXED", "instrument-defect", "ADR-0038", "WISP_MIGRATION_STATUS.md:1878 — “**FIXED (PM-18)** … the body is now captured **inside** the `with`”", "—"),
    ("F42", "A second canonicalizer exists and has drifted: `events.normalize_event` drops a `ToolCallBatch`'s payload",
     "CLOSED", "duplicated-authority", "ADR-0039", "WISP_MIGRATION_STATUS.md:1879 — “**NOT FIXED — reported.** Closed structurally by **ADR-0039 R2**”", "—"),
    ("F43", "The empty-stream detection is representation-dependent",
     "CLOSED", "duplicated-authority", "ADR-0041", "WISP_MIGRATION_STATUS.md:1880 — “**CLOSED (PM-23) — ADR-0041.**”", "—"),
    ("F44", "An iteration-budget-exhausted turn whose wrap-up succeeds was recorded `was_last_turn_complete == True`",
     "CLOSED", "", "ADR-0042", "WISP_MIGRATION_STATUS.md:1881 — “**CLOSED (PM-23) — ADR-0042.**”", "tests/reliability/test_post_m13_completion_enforcement.py"),

    # ── F45–F63 — WISP_MIGRATION_STATUS.md §0 (the per-mission sections) ──────
    ("F45", "No objective-level control loop existed; a failed turn simply ended",
     "FIXED", "unwired-control", "ADR-0045", "WISP_MIGRATION_STATUS.md:166 — “**REPAIRED (ADR-0045)**”", "tests/reliability/test_next_convergence_controller.py"),
    ("F46", "`acceptance.evaluate` had no producer of `AcceptanceCriteria` from a user objective",
     "FIXED", "unwired-control", "ADR-0045", "WISP_MIGRATION_STATUS.md:167 — “**REPAIRED (ADR-0045 R1)**”", "tests/reliability/test_next_autonomous_wiring.py"),
    ("F47", "`PlanStore` is write-only — a plan the model wrote is never shown to it again",
     "OPEN", "unwired-control", "—", "WISP_MIGRATION_STATUS.md:168 — “**OPEN, recorded**”", "—"),
    ("F48", "The default `permission_mode` is `auto_edit`, in which `run_bash` is blocked, so the agent cannot run the project's own tests",
     "OPEN", "", "—", "WISP_MIGRATION_STATUS.md:169 — “**OPEN, recorded**”", "—"),
    ("F49", "A deterministic acceptance check that cannot be evaluated must not report `FAIL`",
     "FIXED", "false-success", "ADR-0045", "WISP_MIGRATION_STATUS.md:170 — “**REPAIRED (ADR-0045 R4)**”", "tests/reliability/test_next_convergence_controller.py"),
    ("F50", "Stagnation was reported from an *empty* measurement and from an objective with no criteria",
     "FIXED", "false-success", "ADR-0045", "WISP_MIGRATION_STATUS.md:171 — “**REPAIRED (ADR-0045 R7)**”", "tests/reliability/test_next_convergence_controller.py"),
    ("F51", "Derived “the verification command exits 0” is unsatisfiable on an already-red suite",
     "FIXED", "", "ADR-0045", "WISP_MIGRATION_STATUS.md:172 — “**REPAIRED (ADR-0045, baseline rule)**”", "tests/reliability/test_next_convergence_controller.py"),
    ("F52", "`benchmark/runner.py::_git_baseline` mutated the **enclosing** repository — eleven commits landed in this repo",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:173 — “**FIXED** … The check is now `--show-toplevel`”", "tests/test_bench_predictions.py::TestBaselineNeverTouchesTheEnclosingRepository"),
    ("F53", "The acceptance conditions were not given to the agent",
     "FIXED", "", "ADR-0045", "WISP_MIGRATION_STATUS.md:174 — “**REPAIRED (ADR-0045)**”", "tests/reliability/test_next_autonomous_wiring.py"),
    ("F54", "The mutating tool surface is unreachable on any path that builds a `WispAgentCore` by hand",
     "FIXED", "instrument-defect", "ADR-0045", "WISP_MIGRATION_STATUS.md:175 — “**FIXED** … One-line fix (wire a `ToolExecutor`) plus two tripwires”", "tests/reliability/test_next_autonomous_wiring.py"),
    ("F55", "`wisp converge` declared `--model`/`--workspace`, but those are global flags stripped before the handler runs",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:176 — “**REPAIRED** … a test asserts the parser does not re-declare them”", "tests/reliability/test_next_convergence_controller.py"),
    ("F56", "Resume lost the recovery strategy — in two parts",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:177 — “**FIXED** … Tests: `test_resume_preserves_the_recovery_strategy`, `test_the_ladder_history_survives_a_resume`”", "tests/reliability/test_next_convergence_controller.py"),
    ("F57", "`~/.config/wisp/.env` is WRITE-ONLY — nothing in `wisp/` ever reads it",
     "OPEN", "unwired-control", "—", "WISP_MIGRATION_STATUS.md:178 — “**OPEN, recorded**”", "—"),
    ("F58", "The acceptance criterion was tamperable: a live agent rewrote the contract and the loop reported `goal_met`",
     "FIXED", "false-success", "—", "WISP_MIGRATION_STATUS.md:179 — “**FIXED** … `verify:cmdN:inputs_unchanged` is a **required** criterion”", "tests/reliability/test_next_autonomous_wiring.py"),
    ("F59", "A timeout that made real progress had no recovery that could continue the work",
     "FIXED", "", "ADR-0046", "WISP_MIGRATION_STATUS.md:180 — “**FIXED (ADR-0046)**”", "tests/reliability/test_progress_aware_recovery.py"),
    ("F60", "A run whose every acceptance criterion PASSES was reported `goal_failed` when its last turn was cut off",
     "FIXED", "", "ADR-0047", "WISP_MIGRATION_STATUS.md:181 — “**FIXED (ADR-0047)**”", "tests/reliability/test_precedence_canonical.py"),
    ("F61", "The continuation rung was available exactly once, so an objective needing two continuations could not converge",
     "FIXED", "", "ADR-0047", "WISP_MIGRATION_STATUS.md:182 — “**FIXED (ADR-0047)**”", "tests/reliability/test_multi_turn_productive_recovery.py"),
    ("F62", "`wisp converge --resume` crashed on a fresh run (`attempts[-1]` on an empty list)",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:183 — “**FIXED**”", "tests/reliability/test_next_convergence_controller.py"),
    ("F63", "The stagnation witness was a function of *when* it was taken, not of *what was there*",
     "FIXED", "", "—", "WISP_MIGRATION_STATUS.md:184 — “**FIXED** … `WITNESS_FIELDS` and `witness_digest`”", "tests/reliability/test_multi_turn_productive_recovery.py"),

    # ── F64–F74 — WISP_MIGRATION_STATUS.md §23 (the table's second half) ──────
    ("F64", "The goal arbitration drifted further than ADR-0047 states — two undocumented cells",
     "DECIDED", "", "ADR-0049", "WISP_MIGRATION_STATUS.md:1882 — “**DECIDED — ADR-0049 (R2/R3), 2026-09-25.**”", "tests/reliability/test_precedence_canonical.py"),
    ("F65", "The precedence table is total only in the code, and “row N” is ambiguous between two ADRs",
     "DECIDED", "", "ADR-0049", "WISP_MIGRATION_STATUS.md:1883 — “**DECIDED — ADR-0049 (R1), 2026-09-25.**”", "tests/reliability/test_precedence_canonical.py"),
    ("F66", "The acceptance criteria — the sole gate on `GOAL_MET` — are derived from prose by three regexes, with three measured failure modes",
     "DECIDED", "false-success", "ADR-0048", "WISP_MIGRATION_STATUS.md:1884 — “**DECIDED — ADR-0048.**”", "tests/reliability/test_criteria_derivation_authority.py"),
    ("F67", "The derivation's production wiring had no test, and a type error shipped green",
     "FIXED", "instrument-defect", "—", "WISP_MIGRATION_STATUS.md:1885 — “**FIXED** (all 11 new mypy errors removed; 0 new / 0 gone vs HEAD)”", "tests/reliability/test_criteria_derivation_authority.py"),
    ("F68", "The `write_file` retry block in `_validate_tool_args` is reachable but INERT",
     "OPEN", "unwired-control", "—", "WISP_MIGRATION_STATUS.md:1886 — “**RECORDED, NOT REMOVED.**”", "—"),
    ("F69", "One condition, two published classifications: a schema failure is a *denial* pre-dispatch and an *error* defense-in-depth",
     "OPEN", "duplicated-authority", "—", "WISP_MIGRATION_STATUS.md:1887 — “**RECORDED.**”", "—"),
    ("F70", "The editable install is a NO-OP, and `CONTEXT.md` §6's claim about it was INVERTED",
     "CLOSED", "record-integrity", "—", "WISP_MIGRATION_STATUS.md:1888 — “**RECORDED, NOT REPAIRED** … **The §6 row is corrected in place**”", "—"),
    ("F71", "`CONTEXT.md` §11's verification authority was wrong in two ways",
     "CLOSED", "count-canonicality", "—", "WISP_MIGRATION_STATUS.md:1889 — “**CORRECTED IN PLACE**”", "—"),
    ("F72", "The criteria classifier's error rate is not a single number — it is a function of `(objective, workspace)`",
     "OPEN", "measurement-method", "ADR-0050", "WISP_MIGRATION_STATUS.md:1890 — “**RECORDED; ADR-0050's Context carries it.**”", "tests/reliability/test_structured_criteria.py"),
    ("F73", "The ADR-0045 R1 no-model-channel tripwire was over-broad, in two successive forms",
     "FIXED", "instrument-defect", "—", "WISP_MIGRATION_STATUS.md:1891 — “**FIXED** — rewritten to test the two things that make a channel”", "tests/reliability/test_structured_criteria.py"),
    ("F74", "A declared-path test was vacuous with respect to the promotion it claimed to test",
     "FIXED", "instrument-defect", "—", "WISP_MIGRATION_STATUS.md:1892 — “**FIXED** — the baseline's criteria id is now a parameter”", "tests/reliability/test_structured_criteria.py"),

    # ── F75–F104 — CONTEXT.md §0 and the phase reports (no ledger row) ───────
    ("F75", "The previous mission's instrument was never committed, so its measurement cannot be re-run",
     "OPEN", "measurement-method", "—", "PHASE_GATE_ENABLEMENT.md:160 — “**Not repaired** (that is ADR-0050's record …)”", "—"),
    ("F76", "The brief's own framing assumed the `INCONCLUSIVE` rate was the question",
     "CLOSED", "model-vs-path", "ADR-0051", "PHASE_GATE_ENABLEMENT.md:181 — “Driven, that framing does not survive contact with the code”", "tests/reliability/test_gate_enablement_contract.py"),
    ("F77", "A bare string scan over a Python tree read a docstring as a caller — M8's `dag_to_graph` tripwire",
     "FIXED", "instrument-defect", "—", "PHASE_DAG_RETIREMENT.md:150 — “Rewritten with `ast`: only an `ImportFrom` of the name, or a `Call` to it, counts.” The report does not number it; the number is `tests/reliability/test_outcome_classification_delegation.py:61`'s (“F77's shape”), resolved by `PHASE_CORPUS_GOVERNANCE_II.md` §4",
     "tests/reliability/test_dag_retirement_contract.py"),
    ("F78", "A model is not a path — the parity ratchet compared two decision models and concluded about two paths",
     "CLOSED", "model-vs-path", "ADR-0055", "PHASE_AUTHORIZATION_PARITY.md:201 — “**F78 — a model is not a path.**” corrected in place", "tests/test_authorization_parity.py"),
    ("F79", "A check that passes by finding nothing, again — none of the ratchet's seven properties compared the two paths",
     "FIXED", "instrument-defect", "—", "PHASE_AUTHORIZATION_PARITY.md:208 — “The new guard parses the AST.”", "tests/test_authorization_parity.py"),
    ("F80", "`tests/test_protected_path_guard.py` cannot run in this environment (`httpx` absent)",
     "OPEN", "", "—", "PHASE_AUTHORIZATION_PARITY.md:214 — “Not repaired: installing a dependency is an environment change”", "—"),
    ("F81", "A derived page carries a superseded disposition (`CURRENT_AUTHORITIES.md` §4)",
     "CLOSED", "record-integrity", "—", "PHASE_AUTHORIZATION_PARITY.md:221 — “**Corrected in place**”", "tests/reliability/test_current_authorities_pins.py"),
    ("F82", "The two “canonical” test blocks listed different file sets",
     "CLOSED", "count-canonicality", "—", "PHASE_OBJECTIVE_FLAG_COMPOSITION.md:145 — “**Both blocks now list the same 43 files**”", "tests/reliability/test_current_authorities_pins.py"),
    ("F83", "A capability claim about a path, measured against the path — the WebSocket question direction never reached a client",
     "FIXED", "model-vs-path", "ADR-0061", "PHASE_REST_APPROVAL.md:153 — “**F83 — a capability claim about a path, measured against the path.**”; **fixed** by ADR-0061 (`CONTEXT.md` §12 row W1: “✅ **CLOSED 2026-09-25 (ADR-0061)**”)", "tests/reliability/test_external_input_path.py"),
    ("F84", "The three routes' own comments described a design the flag now changes",
     "CLOSED", "record-integrity", "—", "PHASE_REST_APPROVAL.md:166 — “They now state both readings.”", "—"),
    ("F85", "A count is canonical only if it is measured after the LAST change to any member",
     "CLOSED", "count-canonicality", "—", "PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:202 — recorded as a **discipline**; `CONTEXT.md` §10 carries it", "—"),
    ("F86", "The corpus classified two unlike guards as one class",
     "CLOSED", "instrument-defect", "—", "PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:213 — added as `CONTEXT.md` §10's **fourth sub-case** row", "tests/test_outcome_classification_authority.py"),
    ("F87", "§3's commit table did not list every commit, and §0 said it did",
     "FIXED", "record-integrity", "—", "PHASE_CORPUS_INTEGRITY_II.md:123 — “**§3's table backfilled with the nine missing handoff commits**”", "tests/test_doc_drift.py"),
    ("F88", "The M4 policy suite is red in this environment (`cryptography` absent), and nothing in the corpus said so",
     "OPEN", "", "—", "PHASE_KEY_TRUST_WORKFLOW.md:118 — “**not repaired** (it needs the dependency, not a code change)”", "—"),
    ("F89", "A bundle that omits `expires_at` crashes the loader with an unrelated message",
     "FIXED", "", "—", "PHASE_KEY_TRUST_WORKFLOW.md:136 — “**Named, not fixed**”; **CLOSED** by corpus integrity III (`CONTEXT.md` §0: “2.1 **CLOSED** (**F89**'s message; the trailing `or 0.0` was **unreachable**)”)", "tests/reliability/test_key_trust_workflow.py"),
    ("F90", "The brief's ADR number is wrong: 0059 is not the next free number; 0058 is",
     "CLOSED", "record-integrity", "ADR-0058", "PHASE_KEY_TRUST_WORKFLOW.md:151 — “**F90 — the brief's ADR number is wrong**”", "—"),
    ("F91", "The brief's worked example for `WISP_POLICY_PUBKEY` is wrong: it is key material, not a path",
     "CLOSED", "model-vs-path", "ADR-0058", "PHASE_KEY_TRUST_WORKFLOW.md:153 — “**F91 — the brief's worked example … is wrong**”", "—"),
    ("F92", "Two M4 tripwires scanned for a bare name and pinned an exact set, so a legitimate addition fired them for the wrong reason",
     "FIXED", "instrument-defect", "—", "PHASE_M4_WIRING.md:154 — “Both repaired, both probed in both directions.”", "tests/test_m4_governance_wiring.py"),
    ("F93", "The canonical block's heading was 1390 and the block measured 1437 — and its own guard was in no running block",
     "FIXED", "count-canonicality", "—", "PHASE_M4_WIRING.md:158 — “re-measured in this same change per F85”", "tests/test_m4_governance_wiring.py"),
    ("F94", "A measurement's condition is part of its result",
     "CLOSED", "measurement-method", "ADR-0059", "PHASE_REST_AUTHORIZATION_COMPOSITION.md:216 — “The number was never wrong; its **scope** was.”", "tests/reliability/test_rest_authorization_composition.py"),
    ("F95", "A brief's worked example can be the thing that decides the wrong way",
     "CLOSED", "model-vs-path", "ADR-0059", "PHASE_REST_AUTHORIZATION_COMPOSITION.md:217 — “The table was the argument for D, and the argument was false.”", "tests/reliability/test_rest_authorization_composition.py"),
    ("F96", "A guard can pass by observing a copy of the code instead of the code",
     "FIXED", "instrument-defect", "—", "PHASE_REST_AUTHORIZATION_COMPOSITION.md:218 — “The non-vacuity probe caught it (NV4).”", "tests/reliability/test_rest_authorization_composition.py"),
    ("F97", "A live range claim can survive fourteen ADR landings",
     "FIXED", "record-integrity", "—", "PHASE_CORPUS_INTEGRITY_III.md:131 — “Three stale claims of the F81 class … All three corrected.”", "tests/test_doc_drift.py"),
    ("F98", "Two records of one finding's status disagreed, and the live one was stale",
     "CLOSED", "record-integrity", "—", "PHASE_REST_AUTHORIZATION_COMPOSITION.md:220 — recorded; §12's G1 row was corrected (F99's row notes §12 was the live target)", "—"),
    ("F99", "A brief cited two ledger rows that do not exist (`G1`, and a governance-layer row)",
     "CLOSED", "record-integrity", "—", "PHASE_REST_AUTHORIZATION_COMPOSITION.md:221 — “The live target is `CONTEXT.md` §12's **E** and **G1** rows, which were updated instead.”", "—"),
    ("F100", "A residual named in an ADR is not automatically a live item",
     "CLOSED", "record-integrity", "—", "PHASE_CORPUS_INTEGRITY_III.md:122 — “§12 gains row **W1**, marked **OPEN — needs its own ADR**”", "tests/reliability/test_external_input_path.py"),
    ("F101", "A probe that checked the wrong paths, and reported two present files as absent",
     "CLOSED", "instrument-defect", "ADR-0060", "PHASE_LAYER_B_BOUNDARY.md:271 — “**The instrument's subject was wrong**”", "tests/reliability/test_layer_b_boundary.py"),
    ("F102", "ADR-0021's stated blocker was FALSE, and was false when it was written",
     "CLOSED", "record-integrity", "ADR-0060", "PHASE_LAYER_B_BOUNDARY.md:281 — “ADR-0060 records the correction and the reason is restated in §3.”", "tests/reliability/test_layer_b_boundary.py"),
    ("F103", "“Zero references from `core/runtime.py`” is no longer literally true",
     "CLOSED", "record-integrity", "ADR-0060", "PHASE_LAYER_B_BOUNDARY.md:306 — “The substantive claim holds; the literal one does not. The guard asserts both halves”", "tests/reliability/test_layer_b_boundary.py"),
    ("F104", "ADR-0057 has no index row",
     "FIXED", "record-integrity", "—", "PHASE_LAYER_B_BOUNDARY.md:313 — “**Corrected here**: the row is added”", "tests/test_doc_drift.py"),
]

#: Findings whose two sources disagree about the status. Recorded, never resolved.
CONFLICTS: list[tuple[str, str]] = [
    ("F8", "`WISP_MIGRATION_STATUS.md:1845`'s headline reads “NOT FIXED — environment gap” while the "
           "same row's later text reads “**FIXED 2026-09-24** — provisioned offline”. The row was "
           "amended in place rather than superseded, so its own status cell is self-contradictory."),
    ("F37", "`WISP_MIGRATION_STATUS.md:1874` reads “**NOT FIXED — newly exposed**”; `CONTEXT.md` §0's "
            "phase table reads “**F37 evidence-adapter repair** | `COMPLETE` — **F37 FIXED**”. The "
            "ledger row was never updated after the repair landed."),
    ("F39", "`WISP_MIGRATION_STATUS.md:1876` reads “**NOT FIXED — reported, not repaired**”; `CONTEXT.md` "
            "§0's phase table reads “F39 token-budget boundary (**ADR-0038**) | `COMPLETE` — "
            "**`RATIFIED`**” and “**`ADR-0038 SATISFIED`**”."),
    ("F89", "`PHASE_KEY_TRUST_WORKFLOW.md:136` reads “**Named, not fixed**”; `CONTEXT.md` §0 reads "
            "“2.1 **CLOSED** (**F89**'s message; the trailing `or 0.0` was **unreachable**)”."),
    ("F19", "`WISP_MIGRATION_STATUS.md:1856` records the three safety-net tests as absent; "
            "`PHASE_LAYER_B_BOUNDARY.md:281` measured all three tracked and predating P5 by 152 "
            "commits. Recorded as `SUPERSEDED` here; the ledger row is unamended."),
]

#: Claims this page once could not pin, and the measurement that pinned each. Kept, so a reader
#: who met the old entry can see what resolved it.
RESOLVED: list[tuple[str, str]] = [
    ("F77", "**Pinned by measurement** (`PHASE_CORPUS_GOVERNANCE_II.md` §4, closing **F105**). "
            "`CONTEXT.md`'s phase table cited F77 as found by `PHASE_DAG_RETIREMENT.md`, which never "
            "numbers it. Its content is §7.1's instrument defect: a committed test names that defect "
            "*“F77's shape”* (`tests/reliability/test_outcome_classification_delegation.py:61`), and "
            "the mission's contemporaneous working notes state *“F77 — a string scan reads docstrings "
            "as code”*. The report's two other finding-shaped statements are open items, not F77 "
            "(`CURRENT_OPEN_ITEMS.md`'s `PHASE_DAG_RETIREMENT` R1/R2). No number was coined."),
]

#: Claims in the artifacts that cannot be pinned. A finding, never a guess.
UNPINNABLE: list[tuple[str, str]] = [
    ("F75–F104", "**No ledger row.** `WISP_MIGRATION_STATUS.md` §23's note says the log runs F1–F44 "
                 "with “**F64–F71 resume here**”; measured, its table actually runs F1–F44 **and "
                 "F64–F74**, §0 carries F45–F63, and **F75–F104 have no ledger row at all** — they "
                 "exist only in `CONTEXT.md` §0's phase table and in the phase reports. A reader "
                 "told the ledger is the findings log will not find 30 of the 104."),
    ("F64–F71", "**Two homes.** These eight exist twice: at `WISP_MIGRATION_STATUS.md:112-119` (the "
                "criteria-authority §0 section, statuses “RECORDED, NOT DECIDED”) and at "
                "`:1882-1889` (§23, statuses “DECIDED — ADR-0049”). The §0 copies are stale. The "
                "ledger itself flags this at `:64-68` as *“the navigability defect this chain was "
                "about and should not be extended”* — and then extended it."),
]


def _check_cells() -> list[str]:
    """No cell may contain an unescaped `|` — it silently splits the markdown table.

    This is the defect that hid **F37** and **F39** from the register's own guard: their
    source cells quoted a table row verbatim, the quoted pipe became a column break, and
    the row stopped parsing. The register looked complete and was two rows short. A
    generator that emits a broken table is worse than one that refuses.
    """
    broken = []
    for row in ROWS:
        for i, cell in enumerate(row):
            if "|" in cell:
                broken.append(f"{row[0]}: cell {i} contains a `|` — {cell[:60]!r}")
    return broken


def _check_tripwires() -> list[str]:
    """Every named tripwire must resolve to a real test file (and class/test if named)."""
    broken = []
    for fid, _t, _s, _c, _d, _src, trip in ROWS:
        if trip == "—":
            continue
        path = trip.split("::", 1)[0]
        if not (REPO / path).exists():
            broken.append(f"{fid}: tripwire {path} does not exist")
    return broken


def _check_sources() -> list[str]:
    """Every source must be `path:line`, the file must exist, and the line must be in range."""
    broken = []
    for fid, _t, _s, _c, _d, src, _trip in ROWS:
        head = src.split(" — ", 1)[0]
        if ":" not in head:
            broken.append(f"{fid}: source {src!r} is not `path:line`")
            continue
        path, _, num = head.rpartition(":")
        if not (REPO / path).exists():
            broken.append(f"{fid}: source file {path} does not exist")
            continue
        try:
            n = int(num)
        except ValueError:
            broken.append(f"{fid}: source line {num!r} is not a number")
            continue
        lines = (REPO / path).read_text(encoding="utf-8").splitlines()
        if not (1 <= n <= len(lines)):
            broken.append(f"{fid}: {path}:{n} is out of range ({len(lines)} lines)")
    return broken


def _counts() -> dict[str, int]:
    out = {w: 0 for w in VOCABULARY}
    for _fid, _t, status, _c, _d, _s, _tr in ROWS:
        out[status] += 1
    return out


def _head_sha() -> str:
    """The commit this page is generated at. Read, never assumed."""
    import subprocess

    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.strip()


#: `OPEN` findings whose cause is the **host**, not the architecture. Named individually so the
#: claim is checkable: each is a declared dependency that cannot be installed here, a resource
#: limit, or an instrument that cannot be committed. Everything else `OPEN` is architectural.
ENVIRONMENTAL = ("F11", "F17", "F36", "F75", "F80", "F88")


def render() -> str:
    counts = _counts()
    total = len(ROWS)
    openish = counts["OPEN"] + counts["UNRESOLVED"]
    lines: list[str] = []
    A = lines.append

    A("# CURRENT_FINDINGS.md — the current state of every finding the corpus has recorded")
    A("")
    A("> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**")
    A("> Regenerate with `env -u PYTHONPATH .venv/bin/python scripts/derive_current_findings.py`.")
    A("> This page states the **current** status of each finding, not its history: it cites the row")
    A("> that records the status, it does not re-derive it, and it **introduces no decision**. If two")
    A("> artifacts disagree, both are cited and §Findings records the conflict — never a resolution.")
    A(">")
    A("> **Every row is pinned to a source.** `path:line`, and the source's own status words are")
    A("> quoted in the `source` cell, so a status cannot be paraphrased into existence. A claim that")
    A("> cannot be pinned is a §Findings entry, not a row.")
    A(">")
    A("> **Sibling registers:** `CURRENT_AUTHORITIES.md` (what each authority's current state is),")
    A("> `CURRENT_OPEN_ITEMS.md` (what is open), `CURRENT_FLAGS.md` (every rollback flag and its")
    A("> default). All four are derived; none may decide.")
    A(">")
    A(f"> Generated 2026-09-25 at `{_head_sha()}` · **{total} findings** (F1–F104) · "
      f"**{openish} not closed** · vocabulary in §(a), classes in §(b).")
    A("")
    A("---")
    A("")
    A("## The register")
    A("")
    A("| id | title | status | class | decided_by | source | tripwire |")
    A("|---|---|---|---|---|---|---|")
    for fid, title, status, cls, decided, src, trip in ROWS:
        A(f"| **{fid}** | {title} | `{status}` | {cls or '—'} | {decided} | {src} | {trip} |")
    A("")
    A("---")
    A("")
    A("## (a) The status vocabulary — stated once")
    A("")
    A("Seven words. Each is a **state**, and each is defined by what it claims about the code or")
    A("the artifacts — not by what the finding was *about*.")
    A("")
    A("| word | what it claims |")
    A("|---|---|")
    A("| `OPEN` | The finding stands. Nothing in the corpus records a repair, and no ADR decided it. "
      "`OPEN` is the **default for silence**: a finding whose source says *“recorded”*, *“logged”* or "
      "*“not repaired”* is `OPEN`. |")
    A("| `FIXED` | A repair landed and is recorded. The source names the change or the test that "
      "holds it. |")
    A("| `CLOSED` | No repair was needed, or the disposition was **not** a code change — corrected "
      "prose, a recorded method, an avoided trap, a decision that needed no implementation. |")
    A("| `SUPERSEDED` | A later finding or ADR measured the claim false. The row is kept so the "
      "supersession is visible. |")
    A("| `DECIDED` | An ADR decided the question the finding raised. The ADR is cited in "
      "`decided_by`. |")
    A("| `DEFECT-PIN` | The finding is **deliberately held open** — the artifact encodes a state the "
      "corpus does not intend to change without authorisation. The pin is the record. |")
    A("| `UNRESOLVED` | The status **cannot be determined** from the artifacts. §Findings says why. |")
    A("")
    A("**The sources' own words, and how they map.** The corpus does not use one vocabulary, so each")
    A("row quotes its source verbatim in the `source` cell. The mapping, for a reader who has the")
    A("ledger's words and needs this page's:")
    A("")
    A("| the source writes | this page |")
    A("|---|---|")
    A("| `Fixed` · `REPAIRED` · `FIXED` | `FIXED` |")
    A("| `Corrected; no work` · `CORRECTED IN PLACE` · `Respected` · `Avoided` · `Method fixed` · "
      "`Recovered` · `Pinned in the corpus` · `Closed structurally` | `CLOSED` |")
    A("| `DECIDED — ADR-XXXX` | `DECIDED` |")
    A("| `OPEN, recorded` · `RECORDED` · `RECORDED, NOT DECIDED` · `RECORDED, NOT REMOVED` · "
      "`NOT FIXED` · `not repaired` | `OPEN` |")
    A("| `NOT FIXED — TEST_ASSUMED_BROKEN_ENVIRONMENT` | `DEFECT-PIN` |")
    A("")
    A("**One source word is not a status and is not used as one:** `NOT MEASURED`. "
      "`PHASE_GATE_ENABLEMENT.md` §8 uses it for a quantity that was never observed — *“Reporting")
    A("those as zero would be manufacturing a metric”*. It is an **admission**, and this page")
    A("inherits it rather than translating it.")
    A("")
    A("---")
    A("")
    A("## (b) The defect-class index")
    A("")
    A("Findings are indexed by the recurring class they belong to, so a reader can reach the")
    A("**discipline** rather than re-deriving it. The class statement is the authority; this index")
    A("only points at it.")
    A("")
    for cls, where in CLASSES.items():
        members = [fid for fid, _t, _s, c, _d, _sr, _tr in ROWS if c == cls]
        A(f"**`{cls}`** — {where}")
        A("")
        A(f"- Members ({len(members)}): " + (", ".join(f"`{m}`" for m in members)
                                              or "no finding is currently in this class"))
        A("")
    A("### The instrument-defect class, by sub-case")
    A("")
    A("`CONTEXT.md` §10 names **six** sub-cases and warns against adding a seventh. Four of the six")
    A("have an `F`-number; two are recorded as instances only. This is the index of which is which.")
    A("")
    A("| # | sub-case | stated at | instances |")
    A("|---|---|---|---|")
    A("| 1–3 | a broken instrument: it ran, and its result was not what its claim said | `CONTEXT.md` §10 | "
      "`F41` (a double that raised from `post()`), `F54` (a fixture that built the core another way), "
      "`F73` (an over-broad tripwire), `F77` (a string scan that read a docstring as a caller) |")
    A("| 4 | **the instrument's SUBJECT was the wrong thing** — a right answer about the wrong subject | "
      "`CONTEXT.md` §10, added 2026-09-25 | `F78`, `F82`, `F86` |")
    A("| 5 | **a raise that does not discriminate** — `pytest.raises(X)` is satisfied by any `X` | "
      "`CONTEXT.md` §10, added 2026-09-25 | NV1 (`PHASE_KEY_TRUST_WORKFLOW.md:163`), and the "
      "inverse fix in `PHASE_CORPUS_INTEGRITY_III.md` §2.1 |")
    A("| 6 | **a guard that pins a STATE rather than a PROPERTY** — it fails for the wrong reason | "
      "`CONTEXT.md` §10, added 2026-09-25 | `F92` (twice), `F96`, `F101` |")
    A("")
    A("---")
    A("")
    A("## (c) The open count, by status")
    A("")
    A(f"Measured 2026-09-25 at `HEAD` by `scripts/derive_current_findings.py` over the {total} rows")
    A("below. A count is canonical only if it is measured after the LAST change to any member")
    A("(**F85**), which is why the generator recomputes it rather than the page stating it.")
    A("")
    A("| status | count |")
    A("|---|---|")
    for word in VOCABULARY:
        A(f"| `{word}` | {counts[word]} |")
    A(f"| **total** | **{total}** |")
    A("")
    # ADR-0062 R3.2: a defined word with no members stays defined, and its emptiness is stated.
    for word in VOCABULARY:
        if counts[word] == 0:
            A(f"**`{word}`** — no finding is currently in this state (ADR-0062 R3.2's rule).")
            A("")
    A(f"**Not closed** — `OPEN` + `UNRESOLVED` — **{openish}** of {total}. "
      f"**{len(ENVIRONMENTAL)}** of them are the **host**, not the architecture: "
      + ", ".join(f"`{e}`" for e in ENVIRONMENTAL) + ".")
    A("Each is a declared dependency that cannot be installed here (`F11`, `F80`, `F88`), a resource")
    A("limit (`F36`), a flaky test (`F17`), or an instrument that cannot be committed (`F75`). The")
    A("remaining `OPEN` findings are architectural and are the ones a decision would move.")
    A("")
    A("---")
    A("")
    A("## §Findings — what this page could not pin, and where sources disagree")
    A("")
    A("This page may **record** a conflict; it may not resolve one. Every entry below is a defect in")
    A("the *artifacts*, not a new finding — no `F`-number is coined here.")
    A("")
    A("### Conflicts between two sources")
    A("")
    for fid, text in CONFLICTS:
        A(f"- **{fid}.** {text}")
    A("")
    A("### Claims that cannot be pinned")
    A("")
    for fid, text in UNPINNABLE:
        A(f"- **{fid}.** {text}")
    A("")
    A("### Claims pinned since")
    A("")
    for fid, text in RESOLVED:
        A(f"- **{fid}.** {text}")
    A("")
    A("### What this page did not do")
    A("")
    A("- **No new finding was created.** The defects in the artifacts recorded above are listed, not")
    A("  numbered, because numbering a finding is a decision about the corpus's log and this page")
    A("  introduces no decision.")
    A("- **No conflict was resolved.** `F8`, `F19`, `F37`, `F39` and `F89` each carry two recorded")
    A("  statuses; the row states the one the *later* source records and the conflict is listed.")
    A("- **The ledger was not edited.** Reorganising the split log is an editorial decision that")
    A("  `WISP_MIGRATION_STATUS.md` §23 already declined once (*“Recorded, not reorganized”*).")
    A("")
    return "\n".join(lines)


def main() -> int:
    problems = _check_sources() + _check_tripwires() + _check_cells()
    if problems:
        print("DERIVATION REFUSED — the data table is not sound:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    ids = [fid for fid, *_ in ROWS]
    expected = [f"F{n}" for n in range(1, 105)]
    if ids != expected:
        missing = sorted(set(expected) - set(ids), key=lambda s: int(s[1:]))
        extra = sorted(set(ids) - set(expected), key=lambda s: int(s[1:]))
        print(f"DERIVATION REFUSED — the register is not total.\n"
              f"  missing: {missing}\n  unexpected: {extra}", file=sys.stderr)
        return 2
    text = render()
    OUT.write_text(text, encoding="utf-8")
    counts = _counts()
    print(f"wrote {OUT.name}: {len(ROWS)} rows, "
          f"{counts['OPEN']} OPEN, {counts['UNRESOLVED']} UNRESOLVED, "
          f"{sum(1 for r in ROWS if r[6] != '—')} rows carry a tripwire")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
