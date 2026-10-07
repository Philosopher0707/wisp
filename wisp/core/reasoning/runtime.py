"""The integration layer: one `TurnReasoning` per turn, called from four seams in `WispAgentCore._turn_inner`.

Unlike its siblings this module is not part of the pure layer (tests/reasoning/test_purity.py audits the others): it adapts engine events
into `ObservedEvent`s and keeps the per-turn journal. It adds no policy of its own; every judgement is made by `ledger`, `claims` and `decision`.

INVARIANTS
  RC4  In `observe` the only observable effects are the journal and a debug log line: `observe_*` return the decision for the caller
       to journal and never a text to show or a request to change. Enforcement arrives in phase P3 and is applied by the engine, not here.
  RC6  The core degrades but never fails a turn: every public method catches everything, records a `CORE_ERROR` row, and returns None.
  RC11 Every intervention-grade decision is journaled with its evidence ids and the ledger digest it was made against.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from wisp.core.events import OutcomeClass, classify_result
from wisp.core.gates.verify import classify
from wisp.core.reasoning.claims import audit
from wisp.core.reasoning.decision import Action, Budgets, Decision, Mode, Modes, State, decide_denial, decide_failure, decide_final, failure_signature, plan_request
from wisp.core.reasoning.ledger import Ledger, ObservedEvent
from wisp.core.reasoning.shellwrites import shell_write_paths
from wisp.core.verification import _verify_result_is_success

_SHELL_TOOLS = frozenset({"run_bash", "exec_sandbox"})

logger = logging.getLogger("wisp.reasoning")


def _gate_rule(reason: str) -> str:
    """The rule name inside a gate refusal's `[layer:RULE]` marker, or 'POLICY' when the refusal is not from the invariant gates."""
    start = reason.find("[")
    while start != -1:
        colon, end = reason.find(":", start), reason.find("]", start)
        if 0 < colon < end:
            rule = reason[colon + 1:end]
            if rule.isupper() and rule.replace("_", "").isalpha():
                return rule
        start = reason.find("[", start + 1)
    return "POLICY"


class TurnReasoning:
    def __init__(self, mode: Mode | Modes, budgets: Budgets = Budgets(), journal_path: str = "") -> None:
        self._journal_path = str(journal_path or "")
        self.modes = mode if isinstance(mode, Modes) else Modes(mode)
        self.budgets = budgets
        self.ledger = Ledger()
        self.state = State()
        self._rows: list[dict[str, Any]] = []
        self._calls = 0
        self._pending: dict[str, Decision] = {}
        self._final_audits: tuple[Any, ...] | None = None

    @property
    def mode(self) -> Mode:
        """The default mode; a rule may differ (`modes.for_rule`)."""
        return self.modes.default

    @property
    def journal(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._rows)

    def _record(self, seam: str, d: Decision | None, **extra: Any) -> None:
        row: dict[str, Any] = {"seq": len(self._rows) + 1, "seam": seam, "mode": (self.modes.for_rule(d.rule) if d is not None else self.modes.default).value, "ledger": self.ledger.snapshot_hash()}
        if d is not None:
            row.update(rule=d.rule, action=d.action.value, reason=d.reason, evidence_ids=list(d.evidence_ids), note=d.note,
                       goal_state=d.goal_state.value if d.goal_state else None, failure_class=d.failure_class.value if d.failure_class else None,
                       rung=int(d.rung) if d.rung else None, max_tokens=d.max_tokens)
        row.update(extra)
        self._rows.append(row)
        logger.debug("reasoning %s", row)
        self._persist(row)

    def _persist(self, row: dict[str, Any]) -> None:
        """Append one JSON line to the operator's journal file, if one is configured. Best effort: a full disk must not fail a turn (RC6)."""
        if not self._journal_path:
            return
        try:
            with open(self._journal_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str, sort_keys=True) + "\n")
        except Exception:  # noqa: BLE001
            logger.debug("reasoning journal write failed", exc_info=True)

    def _core_error(self, seam: str, exc: BaseException) -> None:
        try:
            row = {"seq": len(self._rows) + 1, "seam": seam, "mode": self.mode.value, "action": "core_error", "reason": f"{type(exc).__name__}: {exc}"[:200]}
            self._rows.append(row)
            self._persist(row)
        except Exception:  # noqa: BLE001 — recording the error must not itself raise
            pass

    # ── seam 1: after each tool_result ──
    def observe_tool_result(self, event: dict[str, Any], output: Any, args: Any) -> Decision | None:
        try:
            name = str(event.get("name", ""))
            result = event.get("result")
            cls = classify_result(result)
            denied = cls in (OutcomeClass.POLICY_DENIAL, OutcomeClass.DENIAL)
            failed = cls is not OutcomeClass.SUCCESS
            if not failed and name in _SHELL_TOOLS and isinstance(output, str) and not _verify_result_is_success(output):
                failed = True  # a shell tool succeeds as a TOOL while its command exits non-zero: the exit marker is in the text
            self._calls += 1
            source = str(event.get("tool_call_id") or f"{name}#{self._calls}")
            a = args if isinstance(args, dict) else {}
            command = a.get("command") if isinstance(a.get("command"), str) else None
            path = next((str(a[k]) for k in ("path", "new_path", "dest", "target") if isinstance(a.get(k), str) and a.get(k)), "")
            paths = (path,) if path else ()
            text = output if isinstance(output, str) else ("" if output is None else str(output))
            mutates = False
            if command is not None and not denied and not failed and not classify(command).ok:  # a verification run that logs to a file is still a run
                written = shell_write_paths(command)
                if written:
                    mutates, paths = True, written
            fact = self.ledger.observe(ObservedEvent("tool_result", name, source, result_text=text, command=command, paths=paths, failed=failed, denied=denied, mutates=mutates))
            if denied:
                if self.modes.for_rule("R3") is Mode.OFF:
                    return None
                reason = result.get("reason", "") if isinstance(result, dict) else ""
                d, self.state = decide_denial(_gate_rule(str(reason)), fact.id, self.state, self.budgets)
            elif failed:
                if self.modes.for_rule("R2") is Mode.OFF:
                    return None
                d, self.state = decide_failure(failure_signature(name, text or str(result)), fact.id, self.state, self.budgets)
            else:
                return None
            if d.action is not Action.CONTINUE:
                self._record("tool_result", d)
            return d
        except Exception as exc:  # noqa: BLE001 — RC6
            self._core_error("tool_result", exc)
            return None

    def observe_refusal(self, event: dict[str, Any], args: Any) -> Decision | None:
        """Seam 1b: a call the gates, the role filter or the human refused. It never ran, so there is no output."""
        return self.observe_tool_result(event, None, args)

    def take_enforced(self, seam: str) -> Decision | None:
        """The decision the engine should APPLY at `seam`, once. Always None unless the mode is `enforce` (RC4), and never raises (RC6)."""
        try:
            if seam == "final":
                audits, self._final_audits = self._final_audits, None
                if audits is None or self.modes.for_rule("R1") is not Mode.ENFORCE:
                    return None
                d, self.state = decide_final(audits, Mode.ENFORCE, self.state, self.budgets)
                if d.action is Action.CONTINUE:
                    return None
                self._record(seam, d, applied=True)
                return d
            d = self._pending.pop(seam, None)
            if d is None or self.modes.for_rule(d.rule) is not Mode.ENFORCE:
                return None
            self._record(seam, d, applied=True)
            return d
        except Exception as exc:  # noqa: BLE001 — RC6
            self._core_error(seam, exc)
            return None

    # ── seam 3: the final answer ──
    def observe_final(self, text: str) -> Decision | None:
        try:
            r1 = self.modes.for_rule("R1")
            if r1 is Mode.OFF:
                return None
            audits = audit(text, self.ledger)
            if r1 is Mode.ENFORCE:
                # Decided later, at the last gate before `done` (take_enforced): the verification floor and the other gates act first, and a round
                # they handled must not spend R1's budget.
                self._final_audits = audits
                if audits:
                    self._record("final", None, claims=[{"kind": a.claim.kind.value, "verdict": a.verdict.value, "evidence_ids": list(a.fact_ids)} for a in audits])
                return None
            d, self.state = decide_final(audits, r1, self.state, self.budgets)
            if audits:
                self._record("final", d, claims=[{"kind": a.claim.kind.value, "verdict": a.verdict.value, "evidence_ids": list(a.fact_ids)} for a in audits])
            elif d.action is not Action.CONTINUE:
                self._record("final", d)
            return d
        except Exception as exc:  # noqa: BLE001 — RC6
            self._core_error("final", exc)
            return None

    # ── seam 2: a provider error ──
    def observe_provider_error(self, message: str, requested: int) -> Decision | None:
        try:
            if self.modes.for_rule("R4") is Mode.OFF:
                return None
            self._calls += 1
            fact = self.ledger.observe(ObservedEvent("provider_error", "provider", f"provider#{self._calls}", result_text=message or ""))
            d, self.state = plan_request(message or "", int(requested or 0), fact.id, self.state, self.budgets)
            if d.action is not Action.CONTINUE:
                self._record("provider_error", d)
                self._pending["provider_error"] = d
            return d
        except Exception as exc:  # noqa: BLE001 — RC6
            self._core_error("provider_error", exc)
            return None
