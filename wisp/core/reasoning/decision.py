"""Decide: a pure function from the audited evidence to one action out of a closed set. No model, clock, environment or I/O.

INVARIANTS (docs/harness/reasoning-core-design.md, section 5)
  RC1  Deterministic: a decision is a function of its arguments only.
  RC2  The model's prose is never an input: `decide_final` takes audit results (facts about the text), never the text.
  RC3  No new authority: a STOP carries a `GoalState`, an ESCALATE a `RecoveryRung`, a failure a `FailureClass`; `Decision` refuses anything else.
  RC5  Every intervention is budgeted, so a turn can always end: each rule spends from `Budgets`, and past the budget it returns CONTINUE.
  RC9  Affordability: at most one retry per request, never above the learned ceiling; a limit below `min_useful` stops honestly.
  RC13 A typo in the mode never makes the core more intrusive: unknown means OBSERVE.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from enum import StrEnum

from wisp.core.goal import GoalState
from wisp.core.reasoning.claims import Audit, ClaimKind, Verdict
from wisp.core.recovery import FailureClass, RecoveryRung

_VERIFICATION_KINDS = frozenset({ClaimKind.TESTS_PASS, ClaimKind.BUILD_OK, ClaimKind.LINT_OK, ClaimKind.FIXED})


class Mode(StrEnum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


def parse_mode(value: object) -> Mode:
    """Unknown or malformed means OBSERVE (RC13): a typo must not switch on an intervention, and must not switch the record off either."""
    text = str(value or "").strip().lower()
    for mode in Mode:
        if text == mode.value:
            return mode
    return Mode.OBSERVE


RULES = ("R1", "R2", "R3", "R4", "R5")


@dataclass(frozen=True)
class Modes:
    """One mode per rule, so each heuristic is flipped to `enforce` on its own once its baseline looks right. `default` applies to every rule
    without an override; `off` as the default switches the whole core off whatever the overrides say."""

    default: Mode = Mode.OBSERVE
    overrides: tuple[tuple[str, Mode], ...] = ()

    def for_rule(self, rule: str) -> Mode:
        if self.default is Mode.OFF:
            return Mode.OFF
        return dict(self.overrides).get(rule, self.default)


# What an operator who sets nothing gets: the rules the owner chose to enforce (R1 and R4 on 2026-10-07, R5 on 2026-10-10); R2 and R3 are decision-only and stay observed.
# Any explicit `reasoning_core` or `reasoning_core_rules` setting replaces this list, so `WISP_REASONING_CORE=observe` or `off` is the kill switch.
DEFAULT_ENFORCED_RULES = "R1=enforce,R4=enforce,R5=enforce"


def parse_modes(default: object, rules: object = "") -> Modes:
    """`rules` is `R1=enforce,R4=observe`. An unknown rule is ignored; an unknown mode for a known rule is OBSERVE (RC13): neither a typo
    nor a stray token can make a rule more intrusive than the operator wrote."""
    out: dict[str, Mode] = {}
    for token in str(rules or "").replace(":", "=").split(","):
        name, _, value = token.partition("=")
        name = name.strip().upper()
        if name in RULES:
            out[name] = parse_mode(value)
    return Modes(parse_mode(default), tuple(sorted(out.items())))


def render_modes(m: Modes) -> str:
    return ",".join(f"{r}={mode.value}" for r, mode in m.overrides)


class Action(StrEnum):
    CONTINUE = "continue"
    NUDGE = "nudge"
    WITHHOLD_DONE = "withhold_done"
    ANNOTATE_FINAL = "annotate_final"
    RETRY_REQUEST = "retry_request"
    ESCALATE = "escalate"
    STOP = "stop"


class NudgeKind(StrEnum):
    CHANGE_HYPOTHESIS = "change_hypothesis"
    REFUSED_BY_POLICY = "refused_by_policy"
    NO_NEW_INFORMATION = "no_new_information"


@dataclass(frozen=True)
class Decision:
    rule: str  # R1..R4
    action: Action
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()
    goal_state: GoalState | None = None
    failure_class: FailureClass | None = None
    rung: RecoveryRung | None = None
    nudge: NudgeKind | None = None
    max_tokens: int | None = None
    note: str = ""  # the one line the harness would append or send; the model's text is never copied into it

    def __post_init__(self) -> None:
        if self.action is Action.STOP and not isinstance(self.goal_state, GoalState):
            raise ValueError("STOP must carry a GoalState")
        if self.action is Action.ESCALATE and not isinstance(self.rung, RecoveryRung):
            raise ValueError("ESCALATE must carry a RecoveryRung")
        if self.action is Action.NUDGE and not isinstance(self.nudge, NudgeKind):
            raise ValueError("NUDGE must carry a NudgeKind")
        if self.action is Action.RETRY_REQUEST and not (isinstance(self.max_tokens, int) and self.max_tokens > 0):
            raise ValueError("RETRY_REQUEST must carry a positive max_tokens")
        if self.failure_class is not None and not isinstance(self.failure_class, FailureClass):
            raise ValueError("failure_class must be a FailureClass")
        if self.action is not Action.CONTINUE and not self.reason:
            raise ValueError("an intervention must say why")


CONTINUE_R1 = Decision("R1", Action.CONTINUE)


@dataclass(frozen=True)
class Budgets:
    annotate_final: int = 1
    withhold_done: int = 1
    nudge_per_signature: int = 1
    retry_request: int = 1
    min_useful_tokens: int = 256
    margin_tokens: int = 64
    repeat_threshold: int = 2
    stale_repeat_shell: int = 2  # R5: shell and test tools nudge on the 2nd identical output
    stale_repeat_other: int = 3  # R5: every other tool on the 3rd


@dataclass(frozen=True)
class State:
    """Counters for one turn. Frozen: every rule returns a new State, so a replay from the journal reproduces the same sequence (RC11)."""

    annotated: int = 0
    withheld: int = 0
    retries: int = 0
    failures: tuple[tuple[str, int], ...] = ()
    denials: tuple[tuple[str, int], ...] = ()
    nudged: tuple[str, ...] = ()
    stale: tuple[tuple[str, int], ...] = ()  # R5: "<mutation epoch>:<output digest>" -> times seen
    ceiling: int | None = None  # learned per-provider affordability ceiling


def _bump(pairs: tuple[tuple[str, int], ...], key: str) -> tuple[tuple[tuple[str, int], ...], int]:
    d = dict(pairs)
    d[key] = d.get(key, 0) + 1
    return tuple(sorted(d.items())), d[key]


# ── R1: completion claims against the ledger ──
def _one_line(unsupported: tuple[Audit, ...], contradicted: tuple[Audit, ...]) -> str:
    kinds = lambda xs: ", ".join(sorted({a.claim.kind.value for a in xs}))  # noqa: E731
    parts = []
    if contradicted:
        parts.append(f"contradicted by an observed run ({kinds(contradicted)})")
    if unsupported:
        parts.append(f"not backed by any verification observed after the last edit ({kinds(unsupported)})")
    return "Harness note: the completion claim above is " + " and ".join(parts) + "."


def decide_final(audits: tuple[Audit, ...], mode: Mode, state: State, budgets: Budgets = Budgets()) -> tuple[Decision, State]:
    """R1. Only verification-type claims are judged; FILE_CHANGED and COMMAND_RAN are recorded in the audit but never intervene on their own."""
    judged = tuple(a for a in audits if a.claim.kind in _VERIFICATION_KINDS)
    contradicted = tuple(a for a in judged if a.verdict is Verdict.CONTRADICTED)
    unsupported = tuple(a for a in judged if a.verdict is Verdict.UNSUPPORTED)
    if mode is Mode.OFF or not (contradicted or unsupported):
        return CONTINUE_R1, state
    ids = tuple(dict.fromkeys(i for a in contradicted + unsupported for i in a.fact_ids))
    note = _one_line(unsupported, contradicted)
    if mode is Mode.ENFORCE and state.withheld < budgets.withhold_done:
        return Decision("R1", Action.WITHHOLD_DONE, "a recognised success claim lacks fresh passing evidence", ids, note=note), replace(state, withheld=state.withheld + 1)
    if mode is Mode.ENFORCE and state.withheld >= budgets.withhold_done:
        return Decision("R1", Action.STOP, "the claim is still unbacked after one withhold", ids, goal_state=GoalState.GOAL_UNVERIFIED, note=note), state
    if state.annotated < budgets.annotate_final:
        return Decision("R1", Action.ANNOTATE_FINAL, "a recognised success claim lacks fresh passing evidence", ids, note=note), replace(state, annotated=state.annotated + 1)
    return CONTINUE_R1, state


def withhold_nudge(note: str) -> str:
    """What the model is told when `done` is withheld: the harness's own line plus the two ways out. Contains none of the model's text (RC2)."""
    return f"{note} Run the relevant verification now and report what it printed, or restate the result without that claim."


# ── R2: the same failure again ──
_NUM = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{7,}\b|\d+")
_PATHISH = re.compile(r"(?:/[\w.\-]+){2,}")
_SPACE = re.compile(r"\s+")


def failure_signature(tool: str, error_text: str) -> str:
    """Stable across paths, numbers, hashes and whitespace, so "line 41" and "line 52" of the same error are one failure."""
    t = _PATHISH.sub("<path>", error_text or "")
    t = _NUM.sub("<n>", t)
    t = _SPACE.sub(" ", t).strip().lower()[:300]
    return hashlib.sha256(f"{tool}|{t}".encode()).hexdigest()[:16]


def decide_failure(signature: str, fact_id: str, state: State, budgets: Budgets = Budgets()) -> tuple[Decision, State]:
    """R2. The second occurrence nudges once; the third escalates one rung (the ladder, not the core, owns what may be tried next)."""
    failures, count = _bump(state.failures, signature)
    state = replace(state, failures=failures)
    ids = (fact_id,)
    if count >= budgets.repeat_threshold + 1:
        return Decision("R2", Action.ESCALATE, f"the same failure has occurred {count} times", ids, failure_class=FailureClass.REPEATED, rung=RecoveryRung.LOCAL_REPLAN,
                        note="Harness note: the same failure has repeated; stop retrying this shape and replan."), state
    if count == budgets.repeat_threshold:  # exactly once per signature: the next occurrence escalates
        return Decision("R2", Action.NUDGE, f"the same failure has occurred {count} times", ids, failure_class=FailureClass.REPEATED, nudge=NudgeKind.CHANGE_HYPOTHESIS,
                        note="Harness note: this failed the same way before; change the hypothesis rather than repeating the action."), state
    return Decision("R2", Action.CONTINUE), state


# ── R5: the same observation again, with nothing changed in between ──
_VOLATILE = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{8}_\d{6}\b"), "<ts>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|secs?|seconds?|s)\b"), "<dur>"),
)
_DIGEST_INPUT_CAP = 200_000
_POLLING_TOOLS = frozenset({"subagent_wait", "subagent_list", "subagent_result", "subagent_send", "subagent_cancel"})  # the same "still running" is the right answer there
_REPEAT_TWICE_TOOLS = frozenset({"run_bash", "exec_sandbox", "run_tests"})


def observation_digest(text: object) -> str:
    """A short digest of what a tool said, with only run-to-run noise removed (durations, timestamps, whitespace). Counts and paths stay: they are information.
    Empty output is no observation (""), so a command that prints nothing can never look like a loop."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    elif not isinstance(text, str):
        text = str(text)
    out = text[:_DIGEST_INPUT_CAP].replace("\r", "")
    for pattern, token in _VOLATILE:
        out = pattern.sub(token, out)
    lines = [ln.rstrip() for ln in out.split("\n")]
    out = re.sub(r"\n{2,}", "\n", "\n".join(lines)).strip()
    return hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()[:16] if out else ""


def decide_stale(tool: str, digest: str, epoch: int, state: State, budgets: Budgets = Budgets()) -> tuple[Decision, State]:
    """R5. `epoch` is the number of file mutations so far, so an edit between two identical runs makes the second one new. The nudge fires once per
    (epoch, output), one more identical result escalates one rung (the same one R2 uses), and past that the rule is silent (RC5)."""
    if not digest or tool in _POLLING_TOOLS:
        return Decision("R5", Action.CONTINUE), state
    stale, count = _bump(state.stale, f"{epoch}:{digest}")
    state = replace(state, stale=stale)
    threshold = budgets.stale_repeat_shell if tool in _REPEAT_TWICE_TOOLS else budgets.stale_repeat_other
    if count == threshold:
        return Decision("R5", Action.NUDGE, f"the same output has come back {count} times with no file change in between", failure_class=FailureClass.REPEATED,
                        nudge=NudgeKind.NO_NEW_INFORMATION,
                        note="Harness note: this returned the same output as before and nothing has changed since. Use what you already have to answer, or take a different action."), state
    if count == threshold + 1:
        return Decision("R5", Action.ESCALATE, f"the same output has come back {count} times with no file change in between", failure_class=FailureClass.REPEATED,
                        rung=RecoveryRung.LOCAL_REPLAN,
                        note="Harness note: the same output has now come back repeatedly with nothing changed; stop repeating it and replan."), state
    return Decision("R5", Action.CONTINUE), state


# ── R3: the same gate refusal again ──
def decide_denial(rule: str, fact_id: str, state: State, budgets: Budgets = Budgets()) -> tuple[Decision, State]:
    denials, count = _bump(state.denials, rule)
    state = replace(state, denials=denials)
    key = f"deny:{rule}"
    if count >= budgets.repeat_threshold and key not in state.nudged:
        return Decision("R3", Action.NUDGE, f"the gate has refused rule {rule} {count} times", (fact_id,), failure_class=FailureClass.SECURITY, nudge=NudgeKind.REFUSED_BY_POLICY,
                        note=f"Harness note: rule {rule} refused this twice; choose a narrower route, not another attempt of the same shape."), replace(state, nudged=state.nudged + (key,))
    return Decision("R3", Action.CONTINUE), state


# ── R4: affordability ──
_AFFORD = re.compile(r"can\s+only\s+afford\s+(\d{1,9})\b", re.IGNORECASE)


def affordability_limit(error_text: str) -> int | None:
    m = _AFFORD.search(error_text or "")
    return int(m.group(1)) if m else None


def plan_request(error_text: str, requested: int, fact_id: str, state: State, budgets: Budgets = Budgets()) -> tuple[Decision, State]:
    """R4 (RC9). A provider that names what it can afford gets one smaller request, never a larger one, never a second retry."""
    limit = affordability_limit(error_text)
    if limit is None:
        return Decision("R4", Action.CONTINUE), state
    ceiling = limit if state.ceiling is None else min(state.ceiling, limit)
    state = replace(state, ceiling=ceiling)
    target = ceiling - budgets.margin_tokens
    if state.retries >= budgets.retry_request:
        return Decision("R4", Action.STOP, "the provider limit was already retried once", (fact_id,), goal_state=GoalState.ESCALATED_TO_HUMAN, failure_class=FailureClass.ENVIRONMENT,
                        note=f"Harness note: the provider can only afford {ceiling} tokens; one smaller retry already failed."), state
    if target >= budgets.min_useful_tokens and target < requested:
        return Decision("R4", Action.RETRY_REQUEST, f"the provider can only afford {ceiling} tokens", (fact_id,), failure_class=FailureClass.ENVIRONMENT, max_tokens=target,
                        note=f"Harness note: retrying once with max_tokens={target}."), replace(state, retries=state.retries + 1)
    return Decision("R4", Action.STOP, f"the provider can only afford {ceiling} tokens, below the {budgets.min_useful_tokens} that make an answer useful", (fact_id,), goal_state=GoalState.ESCALATED_TO_HUMAN,
                    failure_class=FailureClass.ENVIRONMENT, note=f"Harness note: the provider can only afford {ceiling} tokens; that is too few to continue. This is a billing limit, not a model fault."), state
