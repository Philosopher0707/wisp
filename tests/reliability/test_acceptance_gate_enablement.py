"""ADR-0054 — the acceptance gate: the consumer of `verdict_keys_on_declared`.

ADR-0053 satisfied ADR-0051 R1's precondition and left one thing open (§10): *"`verdict_keys_on_declared`
is a predicate, and nothing calls it in production … ADR-0051's enablement decision is what would
consume it."* **ADR-0054 is that consumer.**

Three properties, and the first is driven end to end through a **real turn**:

1. **The gate withholds `done`, bounded.** With `acceptance_gate` on and a declaration whose criterion
   fails, the engine asks the runtime's read-only predicate at its pre-`done` gate, withholds, nudges,
   and then **surrenders honestly** — the turn still finishes. That is ADR-0036's delay-not-veto model,
   and it is what makes this enforcement rather than a veto.
2. **`turn_succeeded` is not a `turn_gated` flag.** The withheld turn still ends with `done`, so
   `turn_succeeded` stays a projection of terminal evidence. Asserted, not stated.
3. **Default OFF, and the reason is measured.** ADR-0051 R2–R6's contract requires **≥ 2 capable
   models**; this environment has exactly one (§5 records the enumeration). So the flag ships at OFF
   and the residual is named rather than the closure manufactured.

Non-vacuity: each class was checked by breaking it (removing the engine's gate block, widening the
condition to any verdict, flipping a `PRECEDENCE` row) and confirming the corresponding test fails.
"""
from __future__ import annotations

import asyncio
import pathlib

from wisp.config import WispConfig
from wisp.core.acceptance import Verdict
from wisp.core.engine import WispAgentCore
from wisp.core.goal import (
    PRECEDENCE,
    GoalState,
    derive_goal_state,
    terminal_outcome_from_evidence,
)
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.core.turn_criteria import (
    DeclaredCriteriaGate,
    compose_declared_nudge,
    declared_criteria_gate,
    verdict_keys_on_declared,
)
from wisp.core.verification import (
    FLOOR_CRITERION_ID,
    VerificationFloorGuard,
)
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry

REPO = pathlib.Path(__file__).resolve().parents[2]

APP_PY = "def parse_duration(text):\n    return 0\n"

#: A declaration whose criterion CANNOT be satisfied — the symbol is absent.
DECL_FAILING = (
    "--- criteria ---\n"
    "symbol_defined: app.py::definitely_not_defined_here\n"
    "--- /criteria ---\n"
    "Fix the bug in app.py.\n"
)
DECL_PASSING = (
    "--- criteria ---\n"
    "symbol_defined: app.py::parse_duration\n"
    "--- /criteria ---\n"
    "Fix the bug in app.py.\n"
)


class _Provider:
    """One content + done round, every time it is asked."""

    def generate_stream_events(self, system_prompt, messages, tools=None):
        yield {"type": "content", "text": "done"}
        yield {"type": "done", "done_reason": "stop"}


def _runtime(tmp_path, ws, **flags):
    config = WispConfig().replace(workspace=str(ws), **flags)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)
    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: WispAgentCore(
            config=config, provider=_Provider(),
            security=SecurityPolicy(), tool_executor=None),
        session_repo=repo, config=config)
    return runtime, repo


def _drive(tmp_path, prompt: str, **flags) -> tuple[list[dict], object]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "app.py").write_text(APP_PY)
    runtime, repo = _runtime(tmp_path, ws, **flags)
    session = {"id": "t", "model": "mock", "workspace": str(ws), "messages": []}

    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt=prompt)]

    events = asyncio.run(_main())
    return events, repo


def _nudges(events: list[dict]) -> list[str]:
    out = []
    for ev in events:
        if str(ev.get("type", "")) == "system":
            text = str(ev.get("text") or ev.get("message") or "")
            if "DECLARED CRITERIA" in text:
                out.append(text)
    return out


# ── 1. The gate withholds, bounded, through a REAL turn ────────────────────


class TestTheGateWithholdsBounded:
    def test_a_failing_declaration_withholds_done_and_then_surrenders(self, tmp_path):
        """The whole point: enforcement, not a veto. The turn is withheld, nudged, and
        then finishes — so `turn_succeeded` stays a projection of terminal evidence."""
        events, _ = _drive(tmp_path, DECL_FAILING,
                           turn_criteria_source=True, acceptance_gate=True)
        nudges = _nudges(events)
        assert len(nudges) == 2, (
            f"expected exactly 2 declared-criteria interventions (the shared per-turn "
            f"budget), got {len(nudges)}")
        assert any(str(e.get("type")) == "done" for e in events), (
            "the turn never finished — the gate vetoed instead of delaying")

    def test_a_passing_declaration_does_not_withhold(self, tmp_path):
        events, _ = _drive(tmp_path, DECL_PASSING,
                           turn_criteria_source=True, acceptance_gate=True)
        assert _nudges(events) == [], "a satisfied declaration withheld the turn"

    def test_the_flag_off_withholds_nothing(self, tmp_path):
        """Every existing caller: the declared failure is recorded, never withheld."""
        events, _ = _drive(tmp_path, DECL_FAILING,
                           turn_criteria_source=True, acceptance_gate=False)
        assert _nudges(events) == []
        assert any(str(e.get("type")) == "done" for e in events)

    def test_the_gate_is_inert_without_the_criteria_source(self, tmp_path):
        """Dependent flag: with the source off there are no declared criteria, so the
        gate must not act on a verdict the record does not carry."""
        events, _ = _drive(tmp_path, DECL_FAILING,
                           turn_criteria_source=False, acceptance_gate=True)
        assert _nudges(events) == []

    def test_the_nudge_names_the_condition_and_not_a_plan(self):
        for attempt in (1, 2):
            text = compose_declared_nudge(attempt)
            assert "DECLARED CRITERIA" in text
            assert "not satisfied" in text
            assert compose_declared_nudge(1) != compose_declared_nudge(2)


# ── 2. The predicate the engine asks ──────────────────────────────────────


class TestThePredicateTheEngineAsks:
    def test_it_is_false_when_a_declared_criterion_fails(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "app.py").write_text(APP_PY)
        gate = declared_criteria_gate(DECL_FAILING, str(ws))
        assert gate is not None
        assert gate() is False
        assert gate.evaluations == 1
        assert gate.last_measurement is not None

    def test_it_is_true_when_the_declaration_is_satisfied(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "app.py").write_text(APP_PY)
        gate = declared_criteria_gate(DECL_PASSING, str(ws))
        assert gate is not None and gate() is True

    def test_no_declaration_gives_no_gate(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        assert declared_criteria_gate("Fix the bug.\n", str(ws)) is None

    def test_the_gate_is_a_bare_callable(self, tmp_path):
        """R1: the engine gets a callable and nothing else — no `observe()`, no criteria
        it can mutate. The public surface is `__call__` plus two read-only attributes."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "app.py").write_text(APP_PY)
        gate = declared_criteria_gate(DECL_FAILING, str(ws))
        assert isinstance(gate, DeclaredCriteriaGate)
        assert callable(gate)
        public = {n for n in dir(gate) if not n.startswith("_")}
        assert public == {"last_measurement", "evaluations"}, (
            f"the gate exposes more than a read-only surface: {sorted(public)}")

    def test_the_cached_measurement_is_reused(self, tmp_path):
        """One turn pays for the declared command once: the gate's measurement is what
        the runtime's verdict site reuses (ADR-0054 R3)."""
        from wisp.core.turn_criteria import turn_acceptance_verdict

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "app.py").write_text(APP_PY)
        gate = declared_criteria_gate(DECL_FAILING, str(ws))
        gate()
        cached = gate.last_measurement
        guard = VerificationFloorGuard()
        verdict, tc = turn_acceptance_verdict(
            guard, DECL_FAILING, str(ws), enabled=True, measurement=cached)
        assert verdict.verdict is Verdict.FAIL
        assert "declared:symbol0" in tc.declared_ids

    def test_the_gate_uses_the_same_condition_as_the_adr(self, tmp_path):
        """`verdict_keys_on_declared` is the condition, asked of a declared-only set —
        driven here, not merely named."""
        from wisp.core.acceptance import evaluate

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "app.py").write_text(APP_PY)
        gate = declared_criteria_gate(DECL_FAILING, str(ws))
        assert gate is not None
        assert gate() is False
        measurement = gate.last_measurement
        verdict = evaluate(gate._criteria, measurement.evidence,  # noqa: SLF001
                           measurement.observations)
        assert verdict.verdict is Verdict.FAIL
        assert verdict_keys_on_declared(verdict) is True, (
            "the gate withheld on a condition `verdict_keys_on_declared` does not name")
        assert FLOOR_CRITERION_ID not in str(gate._criteria)  # noqa: SLF001


# ── 3. The three non-violations (ADR-0051 R8) ─────────────────────────────


class TestTheThreeNonViolations:
    def test_turn_succeeded_is_still_terminal_evidence(self):
        assert terminal_outcome_from_evidence(
            saw_done=True, saw_fatal_error=False).value == "succeeded"
        assert terminal_outcome_from_evidence(
            saw_done=False, saw_fatal_error=False).value == "incomplete"

    def test_the_withheld_turn_still_ends_and_is_not_a_second_success_signal(self, tmp_path):
        """A `turn_gated` flag would be a second success signal. It is not: the withheld
        turn ends with `done`, and the goal state is derived from the verdict as before."""
        events, _ = _drive(tmp_path, DECL_FAILING,
                           turn_criteria_source=True, acceptance_gate=True)
        assert any(str(e.get("type")) == "done" for e in events)
        # The declared failure still lands GOAL_FAILED through the unchanged arbiter.
        assert derive_goal_state(
            terminal_outcome="succeeded", acceptance_verdict=Verdict.FAIL,
            turn_succeeded=True) is GoalState.GOAL_FAILED

    def test_the_floor_guards_own_semantics_are_unchanged(self):
        assert VerificationFloorGuard(wrote_code=False).rejection() is None
        assert VerificationFloorGuard(
            wrote_code=True, verify_ok_after_edit=True).rejection() is None
        assert VerificationFloorGuard(
            wrote_code=True, verify_ok_after_edit=False).rejection() is not None

    def test_precedence_is_unchanged_in_content_and_count(self):
        assert [row[0] for row in PRECEDENCE] == list(range(8))
        assert derive_goal_state(
            terminal_outcome="succeeded", acceptance_verdict=Verdict.PASS,
            turn_succeeded=True) is GoalState.GOAL_MET
        assert derive_goal_state(
            terminal_outcome="failed", acceptance_verdict=Verdict.PASS,
            turn_succeeded=True) is GoalState.GOAL_MET


# ── 4. The flag contract ──────────────────────────────────────────────────


class TestTheFlagContract:
    def test_it_defaults_off(self):
        assert WispConfig().acceptance_gate is False

    def test_it_is_read_once(self):
        src = (REPO / "wisp/core/runtime.py").read_text()
        assert src.count('"acceptance_gate"') == 1

    def test_the_engine_receives_a_read_only_callable(self):
        """The engine gets a callable, never the criteria or the probe — the ADR-0036
        shape. `DeclaredCriteriaGate` is a callable object with no mutation surface the
        engine can reach."""
        src = (REPO / "wisp/core/stateless.py").read_text()
        assert "declared_gate: Any = None" in src
        assert "bool(declared_gate())" in src

    def test_the_gate_block_is_bounded_and_shares_the_turn_budget(self):
        src = (REPO / "wisp/core/stateless.py").read_text()
        i = src.index("declared_gate is not None")
        window = src[i:i + 600]
        assert "_MAX_STAGNATION_INTERVENTIONS" in window, "the declared gate is unbounded"
        assert "iteration + 1 < max_iterations" in window, (
            "the declared gate may withhold on the last iteration, which would turn its "
            "own surrender into a CODE_ITERATION_BUDGET failure")
        assert "stagnation_interventions_used" in window, (
            "the declared gate does not share the turn's extension budget")

    def test_the_gate_fails_open(self):
        src = (REPO / "wisp/core/stateless.py").read_text()
        i = src.index("declared_gate is not None")
        assert "except Exception" in src[i:i + 600], (
            "a broken predicate would become a hung turn")

    def test_the_runtime_builds_the_gate_before_the_turn(self):
        """It must be built outside any handler, so a rejected declaration propagates
        (ADR-0050 R4) instead of becoming a silent floor-only run."""
        src = (REPO / "wisp/core/runtime.py").read_text()
        assert "declared_criteria_gate(" in src
        i = src.index("declared_gate = declared_criteria_gate(")
        assert "except Exception" not in src[max(0, i - 400):i], (
            "the gate is built inside a broad handler — a rejection would be swallowed")

    def test_the_gate_is_passed_only_when_present(self):
        """The ADR-0036 precedent: `turn()` is an implementation point, and a core that
        implements the older signature must not receive the new keyword."""
        src = (REPO / "wisp/core/runtime.py").read_text()
        assert 'if declared_gate is not None:' in src
        assert 'turn_kwargs["declared_gate"] = declared_gate' in src


# ── 5. The measurement: why the flag is OFF ───────────────────────────────


class TestTheMeasurementIsNotSatisfiable:
    """ADR-0051 R4 requires **≥ 2 capable models**. Recorded as the reason the flag
    ships OFF, so the residual is measured rather than assumed."""

    #: Enumerated 2026-09-25 against the local Ollama daemon. A "capable" model is one
    #: that can drive Wisp's 42-tool surface (the F8-era measurement: 0/4 and 1/4
    #: schema-valid calls for the two local models).
    CAPABLE = ("nemotron-3-ultra:cloud",)
    DEGENERATE = ("llama3.2:3b", "qwen2.5:0.5b")
    RETIRED = ("qwen3.5:cloud", "deepseek-v4-flash:cloud", "gemini-3-flash-preview:cloud",
               "kimi-k2.5:cloud", "glm-5.1:cloud")
    PAYWALLED = ("glm-5.2:cloud", "kimi-k3:cloud", "minimax-m2.7:cloud",
                 "deepseek-v4-pro:cloud", "kimi-k2.6:cloud")

    def test_the_population_has_fewer_than_two_capable_models(self):
        assert len(self.CAPABLE) < 2, (
            "the population now has >= 2 capable models — ADR-0051 R2's measurement "
            "contract may be satisfiable; re-run the population before changing the "
            "default")
        assert len(self.CAPABLE) + len(self.DEGENERATE) + len(self.RETIRED) \
            + len(self.PAYWALLED) == 13, "the enumeration is stale — re-enumerate"

    def test_the_flag_default_is_the_measured_conclusion(self):
        """The default is OFF *because* the population is short, not by preference."""
        assert WispConfig().acceptance_gate is False

    def test_the_instrument_records_the_enumeration(self):
        assert (REPO / "scripts/acceptance_gate_population.py").exists(), (
            "the population enumeration must be committed so it can be re-run (F75)")
