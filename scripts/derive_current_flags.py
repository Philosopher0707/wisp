#!/usr/bin/env python3
"""Derive `CURRENT_FLAGS.md` from `wisp/config.py` and the tree.

**Why committed.** Same reason as its siblings: `CONTEXT.md` §0.0.9's finding **F75** —
*"an instrument that cannot be committed is not a re-runnable measurement."*

**What is derived and what is data.** The **default** is read from `wisp.config.get_schema()`
— the production path — not from prose, and not from this file. The **read sites** are held
here as `path:line` and checked against the tree on every run. Everything else (what a flag
gates, which ADR introduced it, which flag it depends on) is transcribed from the source.

**The totality property.** Every `bool` setting in `WISP_CONFIG`'s schema gets a row, plus the
three switches that are read from the environment and are not `WispConfig` fields. That is
mechanical, so the guard can assert it rather than trusting the table.

Run: `env -u PYTHONPATH .venv/bin/python scripts/derive_current_flags.py`
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "CURRENT_FLAGS.md"

#: The three switches read from `os.environ` that are **not** `WispConfig` fields. Each is a
#: rollback flag by ADR-0002's purpose and a deviation from its *shape* — see §(a).
ENV_ONLY = ("criteria_strict_derivation", "criteria_structured_declaration", "ws_auto_approve")

#: `(name, env, default, read_at, gates, depends_on, adr, tripwire)`.
#:
#: `default` is **checked against `config.py`** by `_check()` — the value here is the page's
#: claim and the schema is the authority, so a disagreement fails the derivation.
ROWS: list[tuple[str, str, str, str, str, str, str, str]] = [
    # ── P0's three (ADR-0002), the flags the convention was written for ─────
    ("durable_runs", "WISP_DURABLE_RUNS", "ON", "`wisp/composition.py:294`",
     "persist a durable `RunRecord` per turn and give `BackgroundAgentManager` its SQLite store",
     "—", "ADR-0002", "—"),
    ("session_event_fidelity", "WISP_SESSION_EVENT_FIDELITY", "ON", "`wisp/core/runtime.py:649`",
     "journal `assistant_message` / `tool_call` / `tool_result` so replay can reconstruct a turn",
     "—", "ADR-0002", "—"),
    ("turn_spans", "WISP_TURN_SPANS", "ON",
     "`wisp/composition.py:323`, `wisp/core/runtime.py:1440`",
     "emit a trace span per turn and per tool call", "—", "ADR-0002", "—"),

    # ── P1–P4 ───────────────────────────────────────────────────────────────
    ("turn_journal", "WISP_TURN_JOURNAL", "ON", "`wisp/core/runtime.py:651`",
     "journal each tool exchange the moment it closes, so a crash mid-turn keeps it",
     "—", "ADR-0010", "—"),
    ("proposal_boundary", "WISP_PROPOSAL_BOUNDARY", "ON", "`wisp/core/runtime.py:658`",
     "record a `ToolRequest` proposal and a `ToolResult` outcome for every call, rejections included",
     "—", "ADR-0011", "tests/test_proposal_boundary_records.py"),
    ("record_verdict", "WISP_RECORD_VERDICT", "OFF", "`wisp/core/runtime.py:664`",
     "record a completion verdict (PASS/FAIL/INCONCLUSIVE) at turn end — **records only**",
     "—", "ADR-0016", "tests/test_verdict_layer_recorded.py"),
    ("task_graph", "WISP_TASK_GRAPH", "OFF", "`wisp/core/runtime.py:671`",
     "materialize each turn as a task graph of `AGENT` nodes, recorded as `TASK_GRAPH` + `NODE_TRANSITION`",
     "—", "ADR-0019", "tests/test_task_graph_materialization.py"),

    # ── The POST-M13 completion and recovery flags ──────────────────────────
    ("recovery_ladder", "WISP_RECOVERY_LADDER", "OFF", "`wisp/core/runtime.py:680`",
     "consult the recovery ladder at the turn boundary and journal the rung it chooses",
     "—", "ADR-0035", "tests/test_recovery_ladder.py"),
    ("goal_state", "WISP_GOAL_STATE", "OFF", "`wisp/core/runtime.py:689`",
     "derive and record the goal state as a journal-only `GOAL_STATE` record — **records only**",
     "—", "ADR-0035", "tests/test_acceptance_verdict.py"),
    ("stagnation_gate", "WISP_STAGNATION_GATE", "OFF", "`wisp/core/runtime.py:731`",
     "let the stagnation predicate withhold `done` for a bounded number of replan interventions",
     "graph_oscillation_guard (the two are the **recording** and **enforcing** levels of one concern)",
     "ADR-0036", "tests/reliability/test_post_m13_stagnation_gate_validation.py"),
    ("graph_oscillation_guard", "WISP_GRAPH_OSCILLATION_GUARD", "ON", "`wisp/core/stagnation.py:235`",
     "construct the oscillation detector at all — the **recording** level of the stagnation concern",
     "—", "ADR-0034", "tests/test_stagnation_detection.py"),

    # ── The criteria and gate chain ─────────────────────────────────────────
    ("turn_criteria_source", "WISP_TURN_CRITERIA_SOURCE", "OFF", "`wisp/core/runtime.py:707`",
     "let the turn path's required-criteria set carry the objective's declared criteria",
     "—", "ADR-0053", "tests/reliability/test_criteria_source_on_turn_path.py"),
    ("acceptance_gate", "WISP_ACCEPTANCE_GATE", "OFF", "`wisp/core/runtime.py:722`",
     "withhold `done` at the engine's pre-`done` gate when the declared criteria are unsatisfied",
     "**turn_criteria_source** — with the source off there are no declared criteria in the set",
     "ADR-0054", "tests/reliability/test_acceptance_gate_enablement.py"),

    # ── The REST and WebSocket approval flags ───────────────────────────────
    ("rest_approval", "WISP_REST_APPROVAL", "OFF", "`wisp/server/deps.py:574`",
     "route a REST request for an executable-config action through the WebSocket channel for a human decision",
     "—", "ADR-0057", "tests/reliability/test_rest_approval.py"),

    # ── The verification loop ───────────────────────────────────────────────
    ("verification_loop", "WISP_VERIFICATION_LOOP", "ON",
     "`wisp/core/stateless.py:530`, `wisp/core/stateless.py:1301`",
     "require an exit-0 verification after code edits before a turn may complete",
     "—", "ADR-0016", "—"),

    # ── Env-only switches — rollback flags that are not `WispConfig` fields ──
    ("criteria_strict_derivation", "WISP_CRITERIA_STRICT_DERIVATION", "OFF",
     "`wisp/autonomous.py:58`",
     "make an undeterminable acceptance requirement `INCONCLUSIVE` instead of promoting it — "
     "closes ADR-0048's MODE A on the derived path. Read by `_strict_derivation_enabled()` "
     "(`wisp/autonomous.py:77-79`), which names it through the `STRICT_DERIVATION_ENV` constant",
     "—", "ADR-0048", "tests/reliability/test_criteria_derivation_authority.py"),
    ("criteria_structured_declaration", "WISP_CRITERIA_STRUCTURED_DECLARATION", "OFF",
     "`wisp/autonomous.py:68`",
     "parse an objective's `--- criteria ---` block and measure against it, rejecting a declaration "
     "it cannot use rather than reinterpreting it. Read by `_structured_declaration_enabled()` "
     "(`wisp/autonomous.py:82-84`)",
     "—", "ADR-0050", "tests/reliability/test_structured_criteria.py"),
    ("ws_auto_approve", "WISP_WS_AUTO_APPROVE", "OFF", "`wisp/transport/websocket.py:127`",
     "auto-approve a WebSocket approval request with no client connected — the one explicit opt-in",
     "—", "ADR-0057", "tests/reliability/test_external_input_path.py"),

    # ── Knobs: bool settings that configure rendering, prompting or a provider ──
    ("auto_approve", "WISP_AUTO_APPROVE", "OFF", "—",
     "auto-approve tool calls without prompting", "—", "—", "—"),
    ("capability_filtering", "WISP_CAPABILITY_FILTERING", "OFF", "—",
     "filter provider-bound tool schemas by `permission_mode`", "—", "—", "—"),
    ("show_thinking", "WISP_SHOW_THINKING", "ON", "—",
     "show the model's reasoning trace inline", "—", "—", "—"),
    ("show_tool_output", "WISP_SHOW_TOOL_OUTPUT", "ON", "—",
     "show full tool output rather than one-liners", "—", "—", "—"),
    ("compact_mode", "WISP_COMPACT_MODE", "OFF", "—",
     "minimal rendering — no boxes, flat output", "—", "—", "—"),
    ("env_context", "WISP_ENV_CONTEXT", "ON", "—",
     "inject live environment facts into the system prompt", "—", "—", "—"),
    ("auto_compact", "WISP_AUTO_COMPACT", "ON", "—",
     "automatically compact sessions when they grow too long", "—", "—", "—"),
    ("autonomous", "WISP_AUTONOMOUS", "OFF", "—",
     "fully autonomous mode — auto-approves safe writes/bash without human prompts", "—", "—", "—"),
]

#: The interactions the corpus states. `(a, b, the interaction, the ADR)`.
INTERACTIONS: list[tuple[str, str, str, str]] = [
    ("acceptance_gate", "turn_criteria_source",
     "**Dependency.** The gate consumes `verdict_keys_on_declared`, which only exists when the "
     "turn path's criteria set carries the objective's declared criteria. With the source off "
     "the gate would withhold on a verdict the record does not carry.",
     "ADR-0054"),
    ("turn_criteria_source", "criteria_structured_declaration",
     "**Independent.** ADR-0056 drove the 2×2 matrix: the two flags are separate concerns "
     "(one flag per concern, ADR-0002), and neither changes the other's behaviour.",
     "ADR-0056"),
    ("criteria_strict_derivation", "criteria_structured_declaration",
     "**Independent on the objective path; the interaction is derivation *order*.** A valid "
     "declaration PRE-EMPTS `strict`, which is then recorded and inert.",
     "ADR-0056"),
    ("stagnation_gate", "graph_oscillation_guard",
     "**Two levels of one concern.** `graph_oscillation_guard` decides whether the detector is "
     "constructed (recording); `stagnation_gate` decides whether its verdict withholds `done` "
     "(enforcing). Recording and enforcing are different concerns (ADR-0002), so the rollback "
     "has two levels.",
     "ADR-0036"),
    ("durable_runs", "session_event_fidelity",
     "**Independent.** ADR-0002 declares one flag per concern; P0 spans four independent "
     "concerns, so a single flag would make partial rollback impossible.",
     "ADR-0002"),
    ("session_event_fidelity", "turn_journal",
     "**Adjacent, not coupled.** P0's fidelity flag decides *what* is journaled; P1's journal "
     "flag decides *when* (per exchange, or once at turn end).",
     "ADR-0010"),
    ("rest_approval", "ws_auto_approve",
     "**The same channel, two flags.** `rest_approval` decides whether a REST action asks a "
     "human; `ws_auto_approve` decides what the agent path does when *no* client is connected. "
     "They compose — a REST request with no client is denied (ADR-0057), and the agent path's "
     "opt-in does not reach it.",
     "ADR-0057"),
]


def _schema() -> dict:
    sys.path.insert(0, str(REPO))
    from wisp.config import get_schema

    return get_schema()


def _bools() -> dict[str, dict]:
    return {k: v for k, v in _schema().items() if v.get("type") is bool}


def _check() -> list[str]:
    """Totality, defaults against the schema, and every read site against the tree."""
    broken: list[str] = []
    bools = _bools()
    names = [r[0] for r in ROWS]
    missing = sorted(set(bools) - set(names))
    extra = sorted(set(names) - set(bools) - set(ENV_ONLY))
    if missing:
        broken.append(f"bool setting(s) in config.py with no row: {missing}")
    if extra:
        broken.append(f"row(s) naming no bool setting and not env-only: {extra}")
    for name, env, default, read_at, _g, _d, _a, trip in ROWS:
        if name in bools:
            actual = bools[name].get("default")
            want = default == "ON"
            if actual is not want:
                broken.append(
                    f"{name}: the page says {default}; config.py's default is {actual}")
            if bools[name].get("env_var") != env:
                broken.append(
                    f"{name}: the page says {env}; config.py says {bools[name].get('env_var')}")
        if trip != "—" and not (REPO / trip.split("::", 1)[0]).exists():
            broken.append(f"{name}: tripwire {trip} does not exist")
        if "|" in read_at or "|" in _g or "|" in _d:
            broken.append(f"{name}: a cell contains `|`, which splits the markdown table")
        for loc in read_at.split(", "):
            loc = loc.strip().strip("`")
            if loc == "—" or ":" not in loc:
                continue
            path, _, num = loc.rpartition(":")
            if not (REPO / path).exists():
                broken.append(f"{name}: read_at names {path}, which does not exist")
                continue
            lines = (REPO / path).read_text(encoding="utf-8").splitlines()
            try:
                n = int(num)
            except ValueError:
                broken.append(f"{name}: read_at line {num!r} is not a number")
                continue
            if not (1 <= n <= len(lines)):
                broken.append(f"{name}: {path}:{n} is out of range ({len(lines)} lines)")
                continue
            # The site must actually name the flag — by its setting name, its env var name, or
            # the constant that holds the env var. A read site that names none of the three is
            # stale. (`wisp/autonomous.py:58` is `STRICT_DERIVATION_ENV = "WISP_CRITERIA_…"` —
            # it names the env var, not the setting; `websocket.py:127` reads the env var
            # directly. Requiring the setting name would call both stale.)
            window = " ".join(lines[max(0, n - 3):n + 2])
            if not any(token in window for token in (name, env, env.removeprefix("WISP_"))):
                broken.append(
                    f"{name}: {path}:{n} names neither `{name}` nor `{env}` within ±3 lines "
                    f"— stale pin")
    return broken


def _head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.strip()


def render() -> str:
    on = sum(1 for r in ROWS if r[2] == "ON")
    off = len(ROWS) - on
    L: list[str] = []
    A = L.append
    A("# CURRENT_FLAGS.md — every switch, its default, and where it is read")
    A("")
    A("> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**")
    A("> Regenerate with `env -u PYTHONPATH .venv/bin/python scripts/derive_current_flags.py`.")
    A("> Every `default` below is **read from `wisp/config.py`** at generation time, not from")
    A("> prose — a disagreement between this page and the schema fails the derivation. Every")
    A("> `read_at` is a real location, checked on every run.")
    A(">")
    A("> This page states each switch's **current** configuration. It **introduces no decision**:")
    A("> it adds no flag and changes no default. A claim that cannot be pinned is a §Findings")
    A("> entry, not a row.")
    A(">")
    A("> **Sibling registers:** `CURRENT_AUTHORITIES.md` (what each authority's current state is),")
    A("> `CURRENT_FINDINGS.md` (every recorded finding and its status), `CURRENT_OPEN_ITEMS.md`")
    A("> (what is open). All four are derived; none may decide.")
    A(">")
    A(f"> Generated 2026-09-25 at `{_head_sha()}` · **{len(ROWS)} switches** "
      f"({len(_bools())} `bool` settings in `WispConfig` + {len(ENV_ONLY)} read from the "
      f"environment) · **{on} ON**, {off} OFF.")
    A("")
    A("---")
    A("")
    A("## The register")
    A("")
    A("| name | env | default | read_at | gates | depends_on | adr | tripwire |")
    A("|---|---|---|---|---|---|---|---|")
    for name, env, default, read_at, gates, dep, adr, trip in ROWS:
        A(f"| `{name}` | `{env}` | **{default}** | {read_at} | {gates} | {dep} | {adr} | {trip} |")
    A("")
    A("---")
    A("")
    A("## (a) The reading rule — ADR-0002, and where the tree departs from it")
    A("")
    A("**The rule, as ADR-0002 states it** (not as it is often paraphrased):")
    A("")
    A("> *\"each read at the **consumption site** as `getattr(config, name, True)`, and each")
    A("> resolvable from the environment (`WISP_*`) via `get_setting`\"*")
    A("")
    A("So the rule has **three** parts, and all three are checkable:")
    A("")
    A("1. **One flag per concern** — no flag gates two concerns, and no concern has two flags.")
    A("2. **Read at the consumption site**, via `getattr(config, name, <safe default>)` — so a")
    A("   test double that predates the flag gets the new behaviour by default (ADR-0002's own")
    A("   Consequence clause).")
    A("3. **Resolvable from the environment** via `get_setting`, so the operator can roll back")
    A("   without editing code.")
    A("")
    A("**Measured departures.** Three, all recorded and none repaired — repairing any of them is")
    A("a decision, and this page introduces none.")
    A("")
    A("1. **Three rollback flags are read from `os.environ` directly, not via `getattr(config, …)`.**")
    A("   `criteria_strict_derivation` and `criteria_structured_declaration`")
    A("   (`wisp/autonomous.py:71-84`) and `ws_auto_approve` (`wisp/transport/websocket.py:127`)")
    A("   are **not `WispConfig` fields at all** — they are resolved by `_env_truthy()` /")
    A("   `os.environ.get()`. They are therefore **invisible to `config.py`, to `wisp doctor`,")
    A("   and to any consumer that reads a config object.** Each one's docstring says the choice")
    A("   was deliberate (*\"Read at this composition point for the same reason as the strict")
    A("   flag: the pure function stays testable without env\"*), which makes it a **stated")
    A("   deviation**, not an accident — but the consequence stands: ADR-0002 part 2 does not")
    A("   apply to them, and a test double cannot opt out by setting an attribute.")
    A("2. **`verification_loop` has two consumption sites on the turn path**")
    A("   (`wisp/core/stateless.py:530` and `:1301`) and `turn_spans` has two")
    A("   (`wisp/composition.py:323`, `wisp/core/runtime.py:1440`). ADR-0002 says *\"read at the")
    A("   consumption site\"* — **plural sites are consistent with the rule**, so this is")
    A("   recorded as a fact rather than a violation. It is worth stating because the brief for")
    A("   this mission paraphrases the rule as *\"read once, at the composition point\"*, which")
    A("   would make both of these violations. **The paraphrase is wrong and the ADR is the")
    A("   authority.** See §Findings.")
    A("3. **`graph_oscillation_guard` defaults ON and is the only flag whose *description* names")
    A("   no ADR**, although ADR-0034 and ADR-0036 both cite it and `stagnation_gate`'s")
    A("   description names it as its paired level. Its provenance is recorded in this table")
    A("   rather than in `config.py`.")
    A("")
    A("**The knobs, stated separately.** Eight of the rows are `bool` settings that configure")
    A("rendering, prompting or a provider rather than gating a concern the corpus decided:")
    A("`auto_approve`, `capability_filtering`, `show_thinking`, `show_tool_output`,")
    A("`compact_mode`, `env_context`, `auto_compact`, `autonomous`. They are on this page because")
    A("the register's totality property is *\"every `bool` setting in `config.py`\"* — a")
    A("mechanical rule the guard can check — rather than a judgement about which ones are")
    A("rollback flags, which would be a judgement the page could not be held to.")
    A("")
    A("---")
    A("")
    A("## (b) The interaction matrix")
    A("")
    A("Every pair the corpus states an interaction for, with the ADR that states it. A pair that")
    A("interacts **without** a stated interaction would be a §Findings entry; none was found,")
    A("and that is a measured result over the pairs the corpus names.")
    A("")
    A("| a | b | the interaction | ADR |")
    A("|---|---|---|---|")
    for a, b, text, adr in INTERACTIONS:
        A(f"| `{a}` | `{b}` | {text} | **{adr}** |")
    A("")
    A("**The rule the matrix is checked against:** a dependency is a pair where one flag's")
    A("*behaviour* changes when the other is off. Independent flags may still both be read on the")
    A("same path — `turn_criteria_source` and `criteria_structured_declaration` are read on")
    A("different paths (turn vs objective) and ADR-0056 drove the 2×2 matrix to show neither")
    A("changes the other.")
    A("")
    A("---")
    A("")
    A("## §Findings — what this page could not pin")
    A("")
    A("- **The brief's paraphrase of ADR-0002 is wrong, and it would have produced a false")
    A("  finding.** The mission brief states the reading rule as *\"read once, at the composition")
    A("  point\"*; ADR-0002 states it as *\"read at the consumption site\"*. The difference is not")
    A("  cosmetic: ADR-0002's own Consequence clause explains why the rule is *per consumption")
    A("  site* — the point of `getattr` is that a test double which predates the flag still gets")
    A("  the new behaviour. Under the brief's paraphrase, `verification_loop`'s two sites and")
    A("  `turn_spans`'s two sites would each be a violation of a rule the corpus does not have.")
    A("  **Decided by ADR-0062 R4**, which records ADR-0002's rule verbatim, names the paraphrase as")
    A("  the defect, and traces its source to ADR-0056's local *\"read once, independently\"*.")
    A("- **Five flags the brief names do not exist.** The brief lists `verification_gate`,")
    A("  `graph_mutation`, `criteria_strict_derivation`, `criteria_structured_declaration` and")
    A("  `ws_auto_approve` among the flags. Measured: **`verification_gate` and `graph_mutation`")
    A("  appear nowhere in `wisp/`** — the real names are `verification_loop` and")
    A("  (for the P5 concern) `task_graph`. The other three exist but **are not `WispConfig`")
    A("  fields** (§(a) departure 1). The brief's list is a hypothesis, and five of its nineteen")
    A("  names do not resolve. **Decided by ADR-0062 R5:** the two are **wrong names** — not aliases,")
    A("  not deprecated names — and no alias is added.")
    A("- **Eight switches carry `—` in `read_at`.** `auto_approve`, `capability_filtering`,")
    A("  `show_thinking`, `show_tool_output`, `compact_mode`, `env_context`, `auto_compact` and")
    A("  `autonomous` are read by the transports, the renderer and the prompt assembler — surfaces")
    A("  outside this deliverable's subject — and their exact lines were not traced. `—` states")
    A("  that the location is **not pinned**, rather than naming a module this page cannot verify.")
    A("  An unpinnable location is a finding, not a claim.")
    A("")
    A("### What this page did not do")
    A("")
    A("- **No flag added, no default changed.** The register's totality is asserted, not")
    A("  extended.")
    A("- **No departure repaired.** The three env-only switches are recorded, not converted to")
    A("  `WispConfig` fields — that would be a behaviour change and its own decision.")
    A("- **No ADR.** Nothing here surfaced a conflict that requires one.")
    A("")
    return "\n".join(L)


def main() -> int:
    problems = _check()
    if problems:
        print("DERIVATION REFUSED — the data table is not sound:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    OUT.write_text(render(), encoding="utf-8")
    on = sum(1 for r in ROWS if r[2] == "ON")
    print(f"wrote {OUT.name}: {len(ROWS)} switches ({on} ON, {len(ROWS) - on} OFF); "
          f"{len(INTERACTIONS)} stated interactions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
