"""Authorization parity between the two decision layers (Phase 10, G1).

Wisp has **two** authorization models:

| Model | Layers | Used by |
|---|---|---|
| `auth/decision.authorize()` | L0 policy bundle, L1 principal capabilities, L2 workspace trust, L3 risk vs sensitivity, **L4 arguments**, L5 approval | the agent (`ToolExecutor.execute`) |
| `infra/security.SecurityPolicy.check()` | workspace trust, pluggable mode engine, **policy hooks**, approval | the REST gate (`server/deps.require_tool_allowed`) and `ApprovalGate` |

The agent consults **both** (`tool_executor.py:691` `policy_hard_deny`, then
`:707` `authorize`, then the approval gate). The REST gate consults **only**
`SecurityPolicy`. So every rule that lives only in `authorize()` is invisible
to REST.

That is how the protected-path bypass happened (see
`PHASE_10_PROTECTED_PATH_GUARD.md`): `authorize()` refuses a write into
`.wisp/hooks`, `SecurityPolicy` has no opinion, and REST took the latter's word.
The guard is now also applied at the REST adapter, so that instance is closed.

**This file measures the remaining divergence and ratchets it.** It does not
assert the divergence is correct — it asserts it is *known*. A new divergence
fails here; so does one that silently disappears (which would mean the table,
and any decision made from it, is stale).

## The known divergence: the approval layer, in the DEFAULT mode

`require_tool_allowed` documents *"REST has no human to approve, so
approval-required verdicts deny"*. It cannot honour that, because
`SecurityPolicy` never reports `approval_required` for the executable-config
actions. Measured, in `auto_edit` — the **default** permission mode
(`config.py:674`):

| action | `authorize()` | `SecurityPolicy` | REST gate today |
|---|---|---|---|
| `hooks.create` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `mcp.add_server` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `plugins.install` | ALLOW **+ approval** | ALLOW | **ALLOW** |

So in the default mode, REST permits registering a hook, an MCP server, or a
plugin **without the approval the agent path requires** — and the gate's own
docstring says it should deny. An approval channel does exist (the WebSocket
transport implements bidirectional approval); the REST routes simply do not
use it.
"""

from __future__ import annotations

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
    ("POST /api/bash", "run_bash", {"command": "ls"}),
    ("POST /api/hooks", "hooks.create", {"name": "x"}),
    ("POST /api/mcp/servers", "mcp.add_server", {"name": "x"}),
    ("POST /api/plugins/install", "plugins.install", {"path": "p"}),
]

#: Divergences that are known and unresolved. Keyed by
#: (route, mode) -> (authorize_verdict, policy_verdict).
#:
#: All are the same shape: `authorize()` requires approval where
#: `SecurityPolicy` allows outright, so the REST gate — which denies
#: approval-required verdicts — currently allows. Closing them changes the
#: default-mode behaviour of the shipped desktop client, so it is a decision,
#: not a patch.
KNOWN_DIVERGENCES: dict[tuple[str, str], tuple[str, str]] = {
    ("POST /api/hooks", "auto_edit"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/hooks", "ask_all"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/mcp/servers", "auto_edit"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/mcp/servers", "ask_all"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/plugins/install", "auto_edit"): ("ALLOW+APPR", "ALLOW"),
    ("POST /api/plugins/install", "ask_all"): ("ALLOW+APPR", "ALLOW"),
}

#: Routes whose action does NOT require approval in any mode — parity is total
#: and must stay that way.
PARITY_ROUTES = (
    "POST /api/files", "POST /api/files/edit", "DELETE /api/files",
    "POST /api/files/rename", "POST /api/bash",
)


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


@pytest.mark.parametrize("route,action,args", ROUTES,
                         ids=[r[0] for r in ROUTES])
def test_authorization_parity_is_pinned(route, action, args, tmp_path):
    """Every (route, mode) verdict pair must be either at parity or known."""
    unexpected = []
    for mode in MODES:
        auth, pol = _verdicts(route, action, args, mode, tmp_path)
        if auth == pol:
            continue
        if KNOWN_DIVERGENCES.get((route, mode)) == (auth, pol):
            continue
        unexpected.append((mode, auth, pol))
    assert not unexpected, (
        f"{route} diverges from the agent's authorization in ways that are not "
        f"recorded in KNOWN_DIVERGENCES: {unexpected}. Either restore parity or "
        "record the divergence and the decision behind it."
    )


def test_no_known_divergence_has_been_silently_fixed(tmp_path):
    """A divergence that disappears makes the recorded decision stale."""
    gone = []
    for (route, mode), expected in KNOWN_DIVERGENCES.items():
        action, args = next((a, g) for r, a, g in ROUTES if r == route)
        actual = _verdicts(route, action, args, mode, tmp_path)
        if actual != expected:
            gone.append((route, mode, expected, actual))
    assert not gone, (
        "a recorded divergence no longer reproduces — update "
        f"KNOWN_DIVERGENCES and the audit that depends on it: {gone}"
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
    for (route, mode), (auth, pol) in KNOWN_DIVERGENCES.items():
        assert auth == "ALLOW+APPR" and pol == "ALLOW", (
            f"{route} in {mode} diverges as {auth} vs {pol}; only the "
            "approval layer is known to differ"
        )


def test_the_rest_gate_documents_the_approval_contract():
    """The gate claims approval-required denies. If that text changes, the
    divergence above may have been resolved (or the claim abandoned)."""
    src = (REPO / "wisp/server/deps.py").read_text(encoding="utf-8")
    assert "approval-required verdicts deny" in src, (
        "require_tool_allowed no longer documents the approval contract — "
        "re-examine KNOWN_DIVERGENCES"
    )


def test_the_agent_consults_both_models():
    """Pins the asymmetry: the agent composes both layers, REST uses one.

    If the agent ever drops one, this parity table stops describing reality.
    """
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    assert "policy_hard_deny(" in src, "the agent no longer consults the mode rules"
    assert "authorize(" in src, "the agent no longer consults the authority layer"


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
