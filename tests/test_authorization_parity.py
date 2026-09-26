"""Authorization parity between the two decision layers (Phase 10, G1; ADR-0055).

Wisp has **two** authorization models:

| Model | Layers | Used by |
|---|---|---|
| `auth/decision.authorize()` | L0 policy bundle, L1 principal capabilities, L2 workspace trust, L3 risk vs sensitivity, **L4 arguments**, L5 approval | `ToolExecutor.execute` (denials only) and `registry.execute_tool` |
| `infra/security.SecurityPolicy.check()` | workspace trust, pluggable mode engine, **policy hooks**, approval | `require_tool_allowed` (REST) and `ApprovalGate` |

**A model is not a path.** ADR-0055 drove both *production paths* and found **0 outcome
divergences of 36**: the agent — run under REST's own condition, `approval_handler=None` —
and `require_tool_allowed` reach the same outcome on every route in every mode. What this
file's `KNOWN_MODEL_DIVERGENCES` records is the **model** comparison, which is a different
question and is kept because a silent change in either model is still worth knowing about.

**ADR-0059: REST consults both models too — for DENIALS.** ADR-0055's 0-of-36 was measured
with **no organization policy loaded**. ADR-0058 then wired one into the agent path, so a
bundle denying an agent tool name was enforced there and **silently unenforced on REST** —
driven, 11 (route, mode) pairs diverge once a bundle denies `write_file`/`edit_file`/
`run_bash`. `require_tool_allowed` now consults `authorize()` with the root's loaded
`effective_policy` and refuses when it denies. The **approval** half is still
`SecurityPolicy`'s alone, and the reason is measured: the two models agree on `allowed` in
all 36 pairs and disagree only on `approval_required`, and only on the three names the agent
cannot reach — so composing approval here would 403 the shipped client with no bundle at
all. The model table above is unchanged; the **path** composition is not. See
`tests/reliability/test_rest_authorization_composition.py`.

## The three approval models (measured — ADR-0055)

The agent's turn path does **not** use `authorize()`'s `approval_required`:
`ToolExecutor.execute` forks on `not _decision.allowed` alone (`tool_executor.py:755`) and
takes its approval requirement from `_get_write_tools(config)` (`:827`) plus
`_needs_forced_approval` (`:1324`). `authorize().approval_required` is consumed by the
*direct registry* path instead (`tools/registry.py:951`).

| Surface | Approval source |
|---|---|
| `ToolExecutor.execute` (the turn path) | `_get_write_tools` + `_needs_forced_approval` |
| `registry.execute_tool` (direct registry) | `authorize().approval_required` |
| `ApprovalGate`, `require_tool_allowed` (REST) | `SecurityPolicy.check().approval_required` |

## The known MODEL divergence

`hooks.create`, `mcp.add_server` and `plugins.install` are **REST-only action names**: not
agent tools, not plugin tools, and with **no row** in `TOOL_RISK_TABLE`. `authorize()`
reports `ALLOW+APPR` for them in `auto_edit`/`ask_all` (its `risk_for_tool` fail-closes to
`EXEC` on an unknown name); `SecurityPolicy` reports `ALLOW`. Measured, in `auto_edit` —
the **default** permission mode (`config.py`):

| action | `authorize()` | `SecurityPolicy` | REST gate |
|---|---|---|---|
| `hooks.create` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `mcp.add_server` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `plugins.install` | ALLOW **+ approval** | ALLOW | **ALLOW** |

**These are not path divergences.** The agent has no operation for any of the three, so
REST's `ALLOW` matches the agent's behaviour (nothing) rather than differing from it.
ADR-0055 chose **Option A**: accept, and correct the record. The six stay pinned here as
*model* divergences; the guard below pins the property that actually matters — path parity.

A new divergence fails here; so does one that silently disappears (which would mean the
table, and any decision made from it, is stale).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

MODES = ("read_only", "auto_edit", "ask_all", "full")

#: (route, action_name, args) — exactly what each REST route passes to
#: `require_tool_allowed` today.
ROUTES: list[tuple[str, str, dict]] = [
    ("POST /api/files", "write_file", {"path": "a.py"}),
    ("POST /api/files/edit", "edit_file", {"path": "a.py"}),
    ("DELETE /api/files", "edit_file", {"path": "a.py", "op": "delete"}),
    ("POST /api/files/rename", "edit_file",
     {"path": "a.py", "op": "rename", "new_path": "b.py"}),
    ("POST /api/bash", "run_bash", {"command": "true"}),
    ("POST /api/hooks", "hooks.create", {"name": "x"}),
    ("POST /api/mcp/servers", "mcp.add_server", {"name": "x"}),
    ("POST /api/plugins/install", "plugins.install", {"path": "p"}),
]

#: **Model** divergences that are known and unresolved. Keyed by
#: (route, mode) -> (authorize_verdict, policy_verdict).
#:
#: All are the same shape: `authorize()` requires approval where
#: `SecurityPolicy` allows outright. **They are not path divergences** — the
#: three actions are REST-only names with no agent implementation (ADR-0055),
#: so there is no agent path to be at parity with. Closing them by making REST
#: consult `authorize()` would deny in modes where the agent denies nothing,
#: i.e. make REST *stricter* than the agent; that is why ADR-0055 chose
#: Option A rather than B.
KNOWN_MODEL_DIVERGENCES: dict[tuple[str, str], tuple[str, str]] = {
    ("POST /api/hooks", "auto_edit"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/hooks", "ask_all"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/mcp/servers", "auto_edit"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/mcp/servers", "ask_all"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/plugins/install", "auto_edit"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/plugins/install", "ask_all"): ("ALLOW+APPR", "ALLOW"),
}

#: Actions that exist only on the REST surface (ADR-0055 §Problem 1). Pinned so
#: that giving one of them an agent implementation fails this file — which is
#: the reversal condition ADR-0055 names for re-opening Option B.
REST_ONLY_ACTIONS = ("hooks.create", "mcp.add_server", "plugins.install")

#: Routes whose action does NOT require approval in any mode — parity is total
#: and must stay that way.
PARITY_ROUTES = (
    "POST /api/files", "POST /api/files/edit", "DELETE /api/files",
    "POST /api/files/rename", "POST /api/bash",
)

#: The `controlling_layer` vocabulary `authorize()` may return. ADR-0055 R8.
CONTROLLING_LAYERS = frozenset({
    "principal", "workspace", "capability", "arguments", "sensitivity",
    "approval", "allow", "organization",
})


def _verdicts(route: str, action: str, args: dict, mode: str, tmp_path):
    """(authorize_verdict, policy_verdict) for one (route, mode)."""
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust
    from wisp.infra.security import Action, Context, SecurityPolicy

    principal = local_principal(workspace=str(tmp_path), profile="local")
    auth = authorize(principal, action, args,
                     workspace_trust=WorkspaceTrust.TRUSTED,
                     permission_mode=mode)
    pol = SecurityPolicy(permission_mode=mode).check(
        Action(name=action, args=dict(args)), Context(workspace=tmp_path))

    def fmt(d):
        return ("ALLOW" if d.allowed else "DENY") + (
            "+APPR" if getattr(d, "approval_required", False) else "")

    return fmt(auth), fmt(pol)


# ── 1. The model comparison (Phase 10's ratchet, kept) ───────────────────────


@pytest.mark.parametrize("route,action,args", ROUTES,
                         ids=[r[0] for r in ROUTES])
def test_authorization_parity_is_pinned(route, action, args, tmp_path):
    """Every (route, mode) verdict pair must be either at parity or known."""
    unexpected = []
    for mode in MODES:
        auth, pol = _verdicts(route, action, args, mode, tmp_path)
        if auth == pol:
            continue
        if KNOWN_MODEL_DIVERGENCES.get((route, mode)) == (auth, pol):
            continue
        unexpected.append((mode, auth, pol))
    assert not unexpected, (
        f"{route} diverges from the agent's authorization in ways that are not "
        f"recorded in KNOWN_MODEL_DIVERGENCES: {unexpected}. Either restore parity or "
        "record the divergence and the decision behind it."
    )


def test_no_known_divergence_has_been_silently_fixed(tmp_path):
    """A divergence that disappears makes the recorded decision stale."""
    gone = []
    for (route, mode), expected in KNOWN_MODEL_DIVERGENCES.items():
        action, args = next((a, g) for r, a, g in ROUTES if r == route)
        actual = _verdicts(route, action, args, mode, tmp_path)
        if actual != expected:
            gone.append((route, mode, expected, actual))
    assert not gone, (
        "a recorded divergence no longer reproduces — update "
        f"KNOWN_MODEL_DIVERGENCES and the audit that depends on it: {gone}"
    )


@pytest.mark.parametrize("route", PARITY_ROUTES)
def test_parity_routes_have_no_divergence_at_all(route, tmp_path):
    """The file and shell surfaces agree across every mode."""
    action, args = next((a, g) for r, a, g in ROUTES if r == route)
    for mode in MODES:
        auth, pol = _verdicts(route, action, args, mode, tmp_path)
        assert auth == pol, f"{route} in {mode}: {auth} != {pol}"


def test_the_divergence_is_only_the_approval_layer(tmp_path):
    """Every known divergence is 'approval required vs allowed outright'.

    If a divergence appears that is not approval-shaped — e.g. one layer
    denies while the other allows — that is a different, worse class and this
    test forces it to be treated as such.
    """
    assert KNOWN_MODEL_DIVERGENCES, "the divergence table is empty — nothing is pinned"
    for (route, mode), (auth, pol) in KNOWN_MODEL_DIVERGENCES.items():
        assert auth == "ALLOW+APPR" and pol == "ALLOW", (
            f"{route} in {mode} diverges as {auth} vs {pol}; only the "
            "approval layer is known to differ"
        )


def test_the_rest_gate_documents_the_approval_contract():
    """The gate claims approval-required denies. If that text changes, the
    divergence above may have been resolved (or the claim abandoned).

    ADR-0055 R4 qualified the claim without removing it: the sentence is kept,
    and the docstring now also states that it cannot fire for the
    executable-config routes and names what their control actually is.
    """
    src = (REPO / "wisp/server/deps.py").read_text(encoding="utf-8")
    assert "approval-required verdicts deny" in src, (
        "require_tool_allowed no longer documents the approval contract — "
        "re-examine KNOWN_MODEL_DIVERGENCES"
    )
    assert "ADR-0055" in src, (
        "require_tool_allowed's docstring no longer carries ADR-0055's "
        "qualification — the claim is again stronger than what the gate does "
        "for the executable-config routes"
    )
    assert "authorization_parity_measurement.py" in src, (
        "the docstring no longer cites the instrument that measured path parity"
    )


def test_the_agent_consults_both_models():
    """Pins the composition **per path** — parsed, not scanned.

    ADR-0055 pinned the asymmetry with a string scan (`"policy_hard_deny(" in src`),
    which finding **F79** named as the weakness: a scan reads the comment that
    describes the order as readily as the order. ADR-0059 made the asymmetry finer,
    so the pin is the call sites themselves.

    | path | denials | approval |
    |---|---|---|
    | the agent (`ToolExecutor.execute`) | both models | the turn path's own set |
    | REST (`require_tool_allowed`) | both models (ADR-0059) | `SecurityPolicy` alone |

    If either path drops a model, this table stops describing reality.
    """
    fn = _tool_executor_execute_ast()
    calls: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in ("policy_hard_deny", "authorize", "_get_write_tools"):
            calls.setdefault(name, node.lineno)
    assert set(calls) == {"policy_hard_deny", "authorize", "_get_write_tools"}, (
        "the agent no longer composes both models plus its approval set: "
        f"{sorted(calls)}"
    )

    deps = _function_ast(REPO / "wisp/server/deps.py", "require_tool_allowed")
    assert "check" in {n.attr for n in ast.walk(deps) if isinstance(n, ast.Attribute)}, (
        "REST no longer consults the mode engine (`SecurityPolicy.check`)"
    )
    called = {n.func.id for n in ast.walk(deps)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_m2_denial" in called, (
        "REST no longer consults the M2 authority — ADR-0059's composition is gone, "
        "and a bundle denial is again silently unenforced on REST"
    )


def test_a_bundle_denial_reaches_both_paths(tmp_path):
    """ADR-0059's property: with a bundle loaded, the paths agree on `allowed`.

    Driven through the **real** REST gate and the **real** `authorize()`. Before
    ADR-0059 the REST side ignored the bundle entirely, so this is the guard on
    the closure — and it fails if the consult is removed or stops reading
    `allowed`.
    """
    from fastapi import HTTPException

    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import classify_workspace
    from wisp.policy.bundle import PolicyBundle
    from wisp.policy.loader import _bundle_to_effective
    from wisp.server.deps import require_tool_allowed

    import time as _time

    ws = pathlib.Path(tmp_path)
    bundle = _bundle_to_effective(
        PolicyBundle(org_id="acme", expires_at=_time.time() + 3600,
                     approval_matrix={"write_file": "deny"}),
        "local file")

    class _Cfg:
        permission_mode = "auto_edit"
        profile = "default"

    class _Root:
        config = _Cfg()
        tool_executor = None

    class _State:
        root = _Root()

    class _App:
        state = _State()

    class _Req:
        app = _App()

    _Req.app.state.root.organization_policy = bundle
    request = _Req()

    principal = local_principal(workspace=str(ws), profile="default")
    agent = authorize(principal, "write_file", {"path": "a.py"},
                      classify_workspace(str(ws)), permission_mode="auto_edit",
                      effective_policy=bundle)
    assert not agent.allowed, "floor: the bundle must deny the agent path"

    try:
        require_tool_allowed(request, "write_file", {"path": "a.py"}, str(ws))
        rest_denied = False
    except HTTPException:
        rest_denied = True

    assert rest_denied, (
        "REST allowed write_file although the loaded bundle denies it and the "
        "agent path refuses it — ADR-0059's composition is not in effect"
    )


def test_default_mode_is_the_affected_one():
    """The divergence lands in `auto_edit`, which is the default.

    If the default changes to `full`, the blast radius of closing this shrinks
    to users who opt into `auto_edit`/`ask_all` — worth knowing before deciding.
    """
    src = (REPO / "wisp/config.py").read_text(encoding="utf-8")
    assert "PermissionMode.AUTO_EDIT.value" in src, (
        "the default permission mode changed — re-evaluate the blast radius of "
        "the approval-layer divergence"
    )


# ── 2. Path parity — the property the table was written to protect (ADR-0055 R5) ──


class _FakeRequest:
    """The minimum `request_policy` reads: app.state.root.config.permission_mode."""

    class _Cfg:
        def __init__(self, mode):
            self.permission_mode = mode

    class _Root:
        def __init__(self, mode):
            self.config = _FakeRequest._Cfg(mode)

    class _State:
        def __init__(self, mode):
            self.root = _FakeRequest._Root(mode)

    class _App:
        def __init__(self, mode):
            self.state = _FakeRequest._State(mode)

    def __init__(self, mode):
        self.app = _FakeRequest._App(mode)


def _rest_outcome(action: str, args: dict, mode: str, ws) -> str:
    from fastapi import HTTPException

    from wisp.server.deps import require_tool_allowed

    try:
        require_tool_allowed(_FakeRequest(mode), action, dict(args), str(ws))
        return "ALLOW"
    except HTTPException:
        return "DENY"


async def _agent_outcome(action: str, args: dict, mode: str, ws) -> str:
    """Drive the real turn path with **no approver** — REST's own condition."""
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor

    cfg = WispConfig()
    object.__setattr__(cfg, "permission_mode", mode)
    events = []
    async for ev in ToolExecutor(cfg).execute(action, dict(args), str(ws),
                                              approval_handler=None):
        events.append(ev)
    text = " ".join(str(e) for e in events)
    if "Unknown tool" in text:
        return "NOT-A-TOOL"
    if any(k in text for k in ("Denied by", "denied", "Blocked", "blocked",
                               "refused", "read_only mode", "requires approval",
                               "no approval handler")):
        return "DENY"
    return "ALLOW"


def test_the_route_list_has_a_floor():
    """A check whose subject is a collection needs a floor (CONTEXT.md §10)."""
    assert len(ROUTES) >= 8, "the route list shrank — the guard would check less"
    assert len(MODES) == 4
    assert REST_ONLY_ACTIONS, "the REST-only action list is empty"


def test_the_real_paths_agree_on_every_route_in_every_mode(tmp_path):
    """**The property ADR-0055 measured, pinned.** Drives both production
    surfaces — not the two models — and asserts outcome parity.

    A `NOT-A-TOOL` result means the action name has no agent-side
    implementation, so there is no agent path for it to diverge from; that is
    a fact about the action, not a divergence, and it is pinned separately by
    `test_the_rest_only_actions_have_no_agent_path`.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n")

    divergent = []
    for route, action, args in ROUTES:
        for mode in MODES:
            agent = asyncio.run(_agent_outcome(action, args, mode, ws))
            rest = _rest_outcome(action, args, mode, ws)
            if agent == "NOT-A-TOOL":
                continue
            if (agent == "ALLOW") != (rest == "ALLOW"):
                divergent.append((route, mode, agent, rest))
    assert not divergent, (
        "the agent path and the REST gate no longer agree on an outcome: "
        f"{divergent}. ADR-0055 R1 pins path parity; a change here is either a "
        "regression or a decision, and it is not this test's job to accept it "
        "silently."
    )


def test_the_rest_only_actions_have_no_agent_path():
    """The premise of ADR-0055 R2/R3, pinned as a property.

    These three names are the entire content of `KNOWN_MODEL_DIVERGENCES`.
    They are REST-only. **If one of them gains an agent implementation, this
    test fails** — which is exactly the reversal condition ADR-0055 names for
    re-opening Option B: `authorize()` would then be describing a real
    operation, and "should REST approve what the agent approves?" becomes a
    question with an answer.
    """
    from wisp.core.contracts import TOOL_RISK_TABLE
    from wisp.tools.registry import TOOL_IMPLS, has_plugin_tool
    from wisp.tool_executor import _get_write_tools

    assert REST_ONLY_ACTIONS, "floor"
    write_tools = _get_write_tools(None)
    assert write_tools, "floor: the write-tool set is empty"

    for action in REST_ONLY_ACTIONS:
        assert action not in TOOL_IMPLS, (
            f"{action} is now an agent tool — re-open ADR-0055 R3 (Option B) and "
            "re-run scripts/authorization_parity_measurement.py"
        )
        assert not has_plugin_tool(action), f"{action} is now a plugin tool"
        assert action not in TOOL_RISK_TABLE, (
            f"{action} now has a risk-table row — it is no longer REST-only, so "
            "the model divergence may now be a path divergence"
        )
        assert action not in write_tools, (
            f"{action} is now in the turn path's approval set — the three "
            "approval models no longer agree about what is absent"
        )


def test_the_turn_path_approval_set_is_not_authorize_approval_required(tmp_path):
    """**A model is not a path.** Pins the split ADR-0055 §Problem 2 measured.

    If these ever agree, the turn path's approval authority moved — which is
    its own ADR, not a silent simplification.
    """
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust
    from wisp.tool_executor import _get_write_tools

    write_tools = _get_write_tools(None)
    assert write_tools, "floor"

    principal = local_principal(workspace=str(tmp_path), profile="local")
    decision = authorize(principal, "write_file", {"path": "a.py"},
                         workspace_trust=WorkspaceTrust.TRUSTED,
                         permission_mode="auto_edit")

    # The turn path gates `write_file` in auto_edit ...
    assert "write_file" in write_tools
    # ... while `authorize()` reports no approval requirement for it.
    assert decision.allowed and not decision.approval_required, (
        "authorize() now agrees with the turn path about write_file in auto_edit — "
        "ADR-0055 §Problem 2's split has changed; re-run the measurement"
    )


# ── 3. The three non-violations, asserted (ADR-0055 R8) ──────────────────────


def test_authorize_is_unchanged(tmp_path):
    """`authorize()`: same parameters, same layer vocabulary, same layer count."""
    from wisp.auth.decision import AuthorizationDecision, authorize

    assert list(inspect.signature(authorize).parameters) == [
        "principal", "tool_name", "args", "workspace_trust",
        "permission_mode", "sensitivity", "effective_policy",
    ]
    fields = set(AuthorizationDecision.__dataclass_fields__)
    assert {"allowed", "reason", "controlling_layer",
            "approval_required"} <= fields

    # The layer vocabulary, observed rather than scanned: drive a matrix wide
    # enough to exercise several layers, then assert the floor and the ceiling.
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    principal = local_principal(workspace=str(tmp_path), profile="local")
    observed = set()
    for mode in MODES:
        for action, args in (("write_file", {"path": "a.py"}),
                             ("run_bash", {"command": "ls"}),
                             ("read_file", {"path": "a.py"})):
            d = authorize(principal, action, args,
                          workspace_trust=WorkspaceTrust.TRUSTED,
                          permission_mode=mode)
            observed.add(d.controlling_layer)
    assert observed, "floor: no layer was exercised"
    assert len(observed) >= 2, (
        f"only one controlling layer was reachable ({observed}) — the matrix "
        "is too narrow to detect a layer change"
    )
    assert observed <= CONTROLLING_LAYERS, (
        f"authorize() returned a layer outside the pinned vocabulary: "
        f"{observed - CONTROLLING_LAYERS}"
    )


def test_security_policy_check_is_unchanged():
    """`SecurityPolicy.check()`: same signature, same set relationships.

    Pins the *relationships* rather than the literal contents, so a legitimate
    addition to a block set does not fail the guard — but a removal does.
    """
    from wisp.infra.policy_engine import _AUTO_EDIT_DENY_TOOLS
    from wisp.infra.security import (
        _ASK_ALL_BLOCK_TOOLS,
        _AUTO_EDIT_BLOCK_TOOLS,
        SecurityPolicy,
    )

    assert list(inspect.signature(SecurityPolicy.check).parameters) == [
        "self", "action", "context",
    ]
    assert _AUTO_EDIT_BLOCK_TOOLS, "floor"
    assert _ASK_ALL_BLOCK_TOOLS, "floor"
    # Every tool the mode can never run is also one it would otherwise gate.
    assert _AUTO_EDIT_DENY_TOOLS <= _AUTO_EDIT_BLOCK_TOOLS, (
        "the hard-DENY set escaped the auto_edit block set — a tool can now be "
        "denied without being gated"
    )
    # ask_all gates everything auto_edit gates.
    assert _AUTO_EDIT_BLOCK_TOOLS <= _ASK_ALL_BLOCK_TOOLS, (
        "ask_all is now weaker than auto_edit for some tool"
    )


def _tool_executor_execute_ast() -> ast.AST:
    tree = ast.parse((REPO / "wisp/tool_executor.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ToolExecutor":
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and sub.name == "execute":
                    return sub
    raise AssertionError("ToolExecutor.execute not found — the guard is stale")


def _function_ast(path: pathlib.Path, name: str) -> ast.AST:
    """The named top-level function's AST, or a clear failure (ADR-0059)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name} — the guard is stale")


def test_tool_executor_gate_chain_order_is_unchanged():
    """The agent's gate chain, **parsed not scanned** (CONTEXT.md §10).

    A string scan would read the comment that describes the order. The AST
    gives the first call site of each gate, and their order is the property:
    `policy_hard_deny` -> `authorize` -> the `_get_write_tools` approval test.
    """
    fn = _tool_executor_execute_ast()
    first: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in ("policy_hard_deny", "authorize", "_get_write_tools"):
            first.setdefault(name, node.lineno)

    assert set(first) == {"policy_hard_deny", "authorize", "_get_write_tools"}, (
        f"a gate left ToolExecutor.execute: {sorted(first)}"
    )
    assert first["policy_hard_deny"] < first["authorize"] < first["_get_write_tools"], (
        f"the gate chain was re-ordered: {first}. ADR-0055 R8 pins "
        "policy_hard_deny -> authorize -> approval; a change is a new ADR."
    )
