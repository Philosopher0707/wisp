"""Every gated REST route is **classified**, so a new one cannot arrive silently.

ADR-0066 residual 2 named this guard and did not build it:

    *"The set is still a hand-written literal. R3 edited a `frozenset`. A route added without a
    corresponding entry is silent — nothing enumerates the routes and asserts each is classified.
    That guard is what would make this set self-maintaining."*

This is that guard. It parses every `require_tool_allowed(...)` call in `wisp/server/routes/`,
extracts the action name, and asserts the resulting set is **exactly** the pinned one below. Adding a
route — or renaming an action — fails here until the new name is classified, which is the point: the
decision becomes unavoidable rather than optional.

**Two buckets, and every name is in exactly one.**
- `HUMAN_GATED` — the name is in `REST_APPROVAL_ACTIONS`, so the route asks a human when the flag is
  on (ADR-0057, extended by ADR-0066 R3).
- `POLICY_ONLY` — the route passes the policy gate and never asks a human. **Each carries its reason**,
  because "not in the set" is a decision that needs one.

**AST, not a scan.** A string scan would read this file's own docstring, which names every action, and
would pass vacuously. That is the instrument class this session found three times
(`PHASE_DAG_RETIREMENT R1`, `PHASE_LAYER_B_BOUNDARY R2`, and `test_external_input_path`'s scan).
"""
from __future__ import annotations

import ast
import pathlib

from wisp.server.approval_bridge import REST_APPROVAL_ACTIONS

REPO = pathlib.Path(__file__).resolve().parents[2]
ROUTES_DIR = REPO / "wisp" / "server" / "routes"

#: The gate's entrypoint. A route that reaches a tool without calling it is a different defect and
#: is not this guard's subject.
GATE = "require_tool_allowed"

#: Human-gated: in `REST_APPROVAL_ACTIONS`, so the route asks a human.
HUMAN_GATED = frozenset({
    "hooks.create",
    "hooks.test",          # ADR-0066 R3 — executes what create_hook was approved to store
    "mcp.add_server",
    "mcp.test_server",     # ADR-0066 R3 — spawns what add_server was approved to register
    "plugins.install",
})

#: Policy-gated only. Each reason is the decision that keeps it out of the human set.
POLICY_ONLY: dict[str, str] = {
    "write_file": "an agent tool: the mode engine and `SecurityPolicy` are its question (ADR-0066 R1)",
    "edit_file": "an agent tool: the mode engine and `SecurityPolicy` are its question (ADR-0066 R1)",
    "run_bash": "an agent tool: the mode engine and `SecurityPolicy` are its question (ADR-0066 R1)",
    "mcp.remove_server": "narrowing — a removal that needs an approval is one nobody may be present "
                         "to give (ADR-0066 R4)",
    "plugins.uninstall": "narrowing, and named as ADR-0066 R4's cost: it deletes what an install "
                         "approval authorised",
    "plugins.toggle": "neither installing nor removing; in neither set, by ADR-0066 R4",
}

#: A floor. A parse that reaches nothing passes vacuously.
SITE_FLOOR = 10


def _gate_calls() -> list[tuple[str, str, int]]:
    """`(file, action_name, lineno)` for every `require_tool_allowed` call in the routes."""
    out: list[tuple[str, str, int]] = []
    for py in sorted(ROUTES_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != GATE:
                continue
            # The action name is the second positional argument, and must be a literal: a
            # computed name is unclassifiable, which this guard refuses rather than ignores.
            if len(node.args) < 2:
                out.append((py.name, "<no action argument>", node.lineno))
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append((py.name, arg.value, node.lineno))
            else:
                out.append((py.name, f"<non-literal at {type(arg).__name__}>", node.lineno))
    return out


def test_the_route_directory_is_not_empty():
    """Non-vacuity: the guard must have a directory to read."""
    assert ROUTES_DIR.is_dir(), f"{ROUTES_DIR} is not a directory"
    assert sorted(ROUTES_DIR.glob("*.py")), "no route modules found — the glob drifted"


def test_every_gated_route_names_a_literal_action():
    """A computed action name cannot be classified, so it is refused here rather than ignored."""
    computed = [(f, name, ln) for f, name, ln in _gate_calls() if name.startswith("<")]
    assert not computed, (
        f"a route passes a non-literal action name to {GATE}: {computed} — classification is not "
        f"possible, so the route must be made explicit or this guard widened deliberately"
    )


def test_the_gate_is_called_at_enough_sites():
    """Non-vacuity floor (F81's rule: a check that found nothing to check has not run)."""
    sites = _gate_calls()
    assert len(sites) >= SITE_FLOOR, (
        f"only {len(sites)} {GATE} call sites found (floor {SITE_FLOOR}) — the parse drifted, or the "
        f"gate was renamed: {sites}"
    )


def test_every_gated_action_is_classified_exactly_once():
    """The whole point: a new route fails this until its name is classified."""
    names = {name for _f, name, _ln in _gate_calls()}
    classified = HUMAN_GATED | set(POLICY_ONLY)
    unclassified = sorted(names - classified)
    assert not unclassified, (
        f"these actions are gated but classified nowhere: {unclassified}. Add each to HUMAN_GATED "
        f"(and to `REST_APPROVAL_ACTIONS`) or to POLICY_ONLY **with its reason** — ADR-0066 residual 2"
    )
    stale = sorted(classified - names)
    assert not stale, (
        f"these actions are classified but no route calls the gate with them: {stale} — the route "
        f"was renamed or removed; re-pin rather than leaving a dead entry"
    )


def test_the_two_buckets_are_disjoint():
    overlap = sorted(HUMAN_GATED & set(POLICY_ONLY))
    assert not overlap, f"classified in both buckets: {overlap}"


def test_the_human_bucket_is_the_approval_set():
    """`HUMAN_GATED` must be exactly the route names in `REST_APPROVAL_ACTIONS` — no drift either way."""
    names = {name for _f, name, _ln in _gate_calls()}
    assert HUMAN_GATED == (REST_APPROVAL_ACTIONS & names), (
        f"HUMAN_GATED and `REST_APPROVAL_ACTIONS ∩ routes` disagree — "
        f"only-in-bucket: {sorted(HUMAN_GATED - names)}, "
        f"only-in-set: {sorted((REST_APPROVAL_ACTIONS & names) - HUMAN_GATED)}"
    )


def test_every_human_gated_route_actually_calls_the_approval_gate():
    """A name in the set is only human-gated if its route *calls* `require_rest_approval`.

    This is the half that a set-membership test cannot see: `REST_APPROVAL_ACTIONS` says a name
    *should* ask, and this says the route *does*. ADR-0066 R3 was exactly that gap.
    """
    askers: set[str] = set()
    for py in sorted(ROUTES_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "require_rest_approval":
                continue
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                askers.add(node.args[1].value)
    missing = sorted(HUMAN_GATED - askers)
    assert not missing, (
        f"these names are in `REST_APPROVAL_ACTIONS` but their route never calls "
        f"`require_rest_approval`: {missing} — the set says they should ask and the route does not"
    )
