"""G1 — authorization parity, measured against the REAL paths (ADR-0055).

What the existing ratchet measures
----------------------------------
`tests/test_authorization_parity.py` compares two *models*:

    authorize(...)                    (wisp/auth/decision.py)
    SecurityPolicy(...).check(...)    (wisp/infra/security.py)

and treats the first as "the agent's verdict". That is a hypothesis. This
instrument drives the **two production paths** instead:

    the agent   ToolExecutor.execute(tool, args, ws, approval_handler=None)
                (the REST condition: REST has no approver)
    REST        require_tool_allowed(request, action, args, workspace)

and reports the model-vs-model comparison beside it, so the two readings can
be told apart.

Run:  env -u PYTHONPATH .venv/bin/python scripts/authorization_parity_measurement.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisp.config import WispConfig  # noqa: E402
from wisp.tool_executor import ToolExecutor  # noqa: E402

MODES = ("read_only", "auto_edit", "ask_all", "full")

#: (route, action, args) — exactly what each route passes to the gate today.
ROUTES: list[tuple[str, str, dict]] = [
    ("POST /api/files", "write_file", {"path": "a.py"}),
    ("POST /api/files/edit", "edit_file", {"path": "a.py"}),
    ("POST /api/files/binary", "write_file", {"path": "a.py", "op": "binary"}),
    ("POST /api/files/rename", "edit_file",
     {"path": "a.py", "op": "rename", "new_path": "b.py"}),
    ("DELETE /api/files", "edit_file", {"path": "a.py", "op": "delete"}),
    ("POST /api/bash", "run_bash", {"command": "true"}),
    ("POST /api/hooks", "hooks.create", {"name": "x"}),
    ("POST /api/mcp/servers", "mcp.add_server", {"name": "x"}),
    ("POST /api/plugins/install", "plugins.install", {"path": "p"}),
]


# ── the REST gate, driven ────────────────────────────────────────────

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


def _rest_verdict(action: str, args: dict, mode: str, ws: pathlib.Path) -> str:
    from fastapi import HTTPException

    from wisp.server.deps import require_tool_allowed

    try:
        require_tool_allowed(_FakeRequest(mode), action, dict(args), str(ws))
        return "ALLOW"
    except HTTPException as exc:
        return f"DENY({exc.status_code})"


# ── the agent path, driven ───────────────────────────────────────────

async def _agent_verdict(action: str, args: dict, mode: str,
                         ws: pathlib.Path) -> str:
    cfg = WispConfig()
    object.__setattr__(cfg, "permission_mode", mode)
    ex = ToolExecutor(cfg)
    events = []
    async for ev in ex.execute(action, dict(args), str(ws),
                               approval_handler=None):
        events.append(ev)
    text = " ".join(str(e) for e in events)
    if "Unknown tool" in text:
        return "NOT-A-TOOL"
    if "not approved" in text or "User denied" in text or "no approval handler" in text:
        return "DENY(approval)"
    if any(k in text for k in ("Denied by", "denied", "Blocked", "blocked",
                               "refused", "read_only mode", "requires approval")):
        return "DENY(guard)"
    return "ALLOW"


# ── the two models, as the existing ratchet compares them ────────────

def _model_verdicts(action: str, args: dict, mode: str,
                    ws: pathlib.Path) -> tuple[str, str]:
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust
    from wisp.infra.security import Action, Context, SecurityPolicy

    principal = local_principal(workspace=str(ws), profile="local")
    auth = authorize(principal, action, args,
                     workspace_trust=WorkspaceTrust.TRUSTED,
                     permission_mode=mode)
    pol = SecurityPolicy(permission_mode=mode).check(
        Action(name=action, args=dict(args)), Context(workspace=ws))

    def fmt(d):
        return ("ALLOW" if d.allowed else "DENY") + (
            "+APPR" if getattr(d, "approval_required", False) else "")

    return fmt(auth), fmt(pol)


def main() -> int:
    ws = pathlib.Path(tempfile.mkdtemp(prefix="parity-"))
    (ws / "a.py").write_text("x = 1\n")

    print("=" * 100)
    print("A. IS THE ACTION REACHABLE ON THE AGENT PATH AT ALL?")
    print("=" * 100)
    from wisp.core.contracts import TOOL_RISK_TABLE
    from wisp.tools.registry import TOOL_IMPLS, has_plugin_tool
    from wisp.tool_executor import _get_write_tools
    wt = _get_write_tools(None)
    print(f"{'action':18s} {'agent tool?':12s} {'risk row?':22s} "
          f"{'in _get_write_tools?':20s}")
    for _, action, _ in ROUTES:
        is_tool = action in TOOL_IMPLS or has_plugin_tool(action)
        row = TOOL_RISK_TABLE.get(action)
        print(f"{action:18s} {str(is_tool):12s} {str(row):22s} {str(action in wt):20s}")

    print()
    print("=" * 100)
    print("B. THE TWO REAL PATHS (agent driven with NO approver — the REST condition)")
    print("=" * 100)
    hdr = (f"{'route':26s} {'mode':10s} {'AGENT (real)':16s} {'REST (real)':12s} "
           f"{'parity':8s} {'authorize()':13s} {'SecurityPolicy':15s} "
           f"{'models agree'}")
    print(hdr)
    print("-" * len(hdr))

    rows = 0
    path_div = 0
    model_div = 0
    for route, action, args in ROUTES:
        for mode in MODES:
            agent = asyncio.run(_agent_verdict(action, args, mode, ws))
            rest = _rest_verdict(action, args, mode, ws)
            auth_v, pol_v = _model_verdicts(action, args, mode, ws)
            # Compare OUTCOMES, not labels: `DENY(guard)` and `DENY(403)` are
            # the same outcome reached by different mechanisms. A `NOT-A-TOOL`
            # action has no agent path, so there is nothing to diverge from.
            same_path = (agent == "NOT-A-TOOL") or (
                agent.startswith("DENY") == rest.startswith("DENY"))
            same_model = auth_v == pol_v
            rows += 1
            path_div += int(not same_path)
            model_div += int(not same_model)
            print(f"{route:26s} {mode:10s} {agent:16s} {rest:12s} "
                  f"{'yes' if same_path else 'NO':8s} {auth_v:13s} {pol_v:15s} "
                  f"{'yes' if same_model else 'NO'}")

    print()
    print(f"ROWS={rows}  PATH DIVERGENCES (agent vs REST)={path_div}  "
          f"MODEL DIVERGENCES (authorize vs SecurityPolicy)={model_div}")
    print()
    print("READING")
    print("  'AGENT (real)' is ToolExecutor.execute with no approval_handler —")
    print("  exactly the condition REST runs under (no human to approve).")
    print("  'NOT-A-TOOL' means the action name has no agent-side implementation,")
    print("  so there is no agent path for it to diverge from.")
    print("  The last two columns are what tests/test_authorization_parity.py compares.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
