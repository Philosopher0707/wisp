"""ADR-0059 — the REST gate's authorization composition.

ADR-0055 measured the two production paths and found **0 divergences of 36**. That
measurement was taken with **no organization policy loaded**. ADR-0058 then wired
one into the agent path, so a bundle that denies an agent tool name is enforced
there and was **silently unenforced on REST** — this guard's first test drives
that gap, and its second drives the counterfactual that proves the closure is the
composition's doing rather than the fixture's.

What this ADR decides: the REST gate consults the M2 authority (`authorize()`) for
its **denial** verdict, with the same `effective_policy` the composition root
loaded, **and nothing else**. The approval half is not composed — measured, the
two models agree on `allowed` in all 36 (route, mode) pairs and disagree only on
`approval_required`, and only on the three names the agent cannot reach.

Every test here drives the **real** `require_tool_allowed`; the non-violations are
re-asserted in the state this ADR creates, because the change that could have
violated them is this one.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

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

#: The three names with no agent operation (ADR-0055 §1.2).
REST_ONLY = frozenset({"hooks.create", "mcp.add_server", "plugins.install"})

#: The names REST routes carry that ARE agent tools.
AGENT_TOOL_ROWS = [(r, a, ar) for r, a, ar in ROUTES if a not in REST_ONLY]

#: A floor for the row set. A scan that reaches nothing passes vacuously.
ROW_FLOOR = 8

#: Rows that ignore a denying bundle when no composition is consulted. ADR-0059 measured
#: **11 of 36** (five in `auto_edit`). ADR-0074 then made REST refuse a gated tool with no
#: approver, so `read_only`/`auto_edit`/`ask_all` refuse on their own and only `full` is
#: left — **6 rows, all `full`**. Re-measured on the repair branch: the floor follows the
#: measurement, and *why it moved* is recorded rather than the number being relaxed.
GAP_FLOOR = 6

CONTROLLING_LAYERS = frozenset({
    "principal", "workspace", "capability", "arguments", "sensitivity",
    "approval", "allow", "organization", "local file",
})


def _effective(matrix: dict[str, str] | None):
    """An `EffectivePolicy` carrying `matrix`, or `None`.

    Built through the real loader. `expires_at` is set because the dataclass
    default is falsy and `merge_layers` raises on it (F89, closed by the corpus
    pass that accompanied this ADR).
    """
    if matrix is None:
        return None
    from wisp.policy.bundle import PolicyBundle
    from wisp.policy.loader import _bundle_to_effective

    return _bundle_to_effective(
        PolicyBundle(org_id="acme", expires_at=time.time() + 3600,
                     approval_matrix=matrix),
        "local file")


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    """One real `CompositionRoot`, reused: the matrix below is wide, not deep."""
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    ws = tmp_path_factory.mktemp("ws")
    return CompositionRoot(config=WispConfig().replace(workspace=str(ws)))


def _request(mode, policy, *, root_obj=None):
    """A request shaped like the server's.

    With `root_obj`, the REAL root is used (so `organization_policy` is whatever
    the root holds). Without it, a minimal stand-in carrying `policy`.
    """
    class _Cfg:
        permission_mode = mode
        profile = "default"

    class _Root:
        config = _Cfg()
        tool_executor = None
        organization_policy = policy

    class _State:
        root = _Root()

    class _App:
        state = _State()

    class _Req:
        app = _App()

    if root_obj is not None:
        _Req.app.state.root = root_obj
    return _Req()


def _verdict(request, action, args, ws):
    """(status, detail) from the real gate."""
    from fastapi import HTTPException

    from wisp.server.deps import require_tool_allowed

    try:
        require_tool_allowed(request, action, args, str(ws))
        return "ALLOW", ""
    except HTTPException as e:
        return "403", str(e.detail)


# ── The gap, and its closure ─────────────────────────────────────────

def test_the_row_set_has_a_floor():
    assert len(ROUTES) >= ROW_FLOOR, "the row set shrank below its floor"
    assert AGENT_TOOL_ROWS, "no REST route carries an agent tool name"
    assert REST_ONLY, "the REST-only set is empty — the analysis is vacuous"


def test_the_gap_is_real_when_no_composition_is_consulted(root, tmp_path):
    """**The counterfactual.** Without the consult, a bundle denial is ignored.

    This is the *before* state driven on the same rows the closure test uses, so
    the closure cannot be an artefact of the fixture. Measured before this ADR:
    **11 of 36** (route, mode) pairs diverged from the agent's verdict. ADR-0074
    then made REST refuse a gated tool with no approver, so the rows that still
    ignore a bundle are the `full`-mode ones — `GAP_FLOOR`, re-measured.
    """
    ws = pathlib.Path(tmp_path)
    ignored = 0
    allowed_rows = []
    for route, action, args in AGENT_TOOL_ROWS:
        for mode in MODES:
            # `organization_policy=None` is exactly "the consult does not fire".
            status, _ = _verdict(_request(mode, None), action, args, ws)
            if status == "ALLOW":
                ignored += 1
                allowed_rows.append((action, mode))
    assert ignored >= GAP_FLOOR, (
        f"only {ignored} rows ignore a denying bundle. ADR-0059 measured **11 of 36** "
        f"(five of them in `auto_edit`); ADR-0074 then made REST refuse a gated tool with "
        f"no approver, so only `full` is left — **{GAP_FLOOR}, all `full`**. Re-measure "
        f"before trusting the closure test: {allowed_rows}"
    )


def test_the_gap_closes_when_a_bundle_denies_an_agent_tool(root, tmp_path):
    """R1/R3 — the M2 authority's denial is honoured on the REST gate."""
    ws = pathlib.Path(tmp_path)
    bundle = _effective({"write_file": "deny", "edit_file": "deny",
                         "run_bash": "deny"})
    for route, action, args in AGENT_TOOL_ROWS:
        for mode in MODES:
            status, detail = _verdict(_request(mode, bundle), action, args, ws)
            assert status == "403", (
                f"{route} in {mode} allowed {action} although the loaded bundle "
                "denies it and the agent path refuses it"
            )
            assert "policy layer" in detail, (
                f"{route} in {mode} refused for the wrong reason: {detail!r} — "
                "the refusal must come from the M2 consult, not the mode engine"
            )


def test_a_bundle_naming_a_rest_only_action_is_honoured(root, tmp_path):
    """The three names the agent cannot reach are still the operator's to deny.

    ADR-0055 §1.2 established that the agent has **no operation** for these, so
    there is no agent verdict to be at parity with — but there is an operator
    rule, and before this ADR nothing consulted it.
    """
    ws = pathlib.Path(tmp_path)
    bundle = _effective({name: "deny" for name in REST_ONLY})
    for route, action, args in ROUTES:
        if action not in REST_ONLY:
            continue
        for mode in ("auto_edit", "ask_all", "full"):
            status, detail = _verdict(_request(mode, bundle), action, args, ws)
            assert status == "403", (
                f"{route} in {mode} ignored a bundle that denies {action}"
            )
            assert "policy layer" in detail


# ── The two properties that keep the change minimal ──────────────────

def test_no_bundle_the_consult_contributes_nothing(root, tmp_path, monkeypatch):
    """R2 — with no policy loaded, ADR-0059's consult cannot change a verdict.

    **The property, and why this test was rewritten.** The first version reconstructed
    HEAD's gate (guard -> `SecurityPolicy.check()`) and required the real gate to match it
    byte-for-byte. That anchored R2 to a **baseline**, and the baseline then moved twice:
    ADR-0068 added the workspace-trust check, ADR-0074 the `approval_needed` check. Both are
    deliberate and neither is ADR-0059's business — but the reconstruction went stale and
    this test went red on the repair branch. That is the test doing its job on a premise that
    had expired, so the premise is what changed.

    This version anchors to **ADR-0059 itself**: it drives the real gate and the same gate
    with the consult forced to decline, and requires them to agree. It cannot go stale when
    another ADR extends the gate (both sides move together), and it still fails on every way
    R2 can break — a consult that fires with no bundle, or one whose short-circuit is removed
    so `authorize()` runs with `effective_policy=None` (which denies in `read_only` and
    changes the 403 detail).
    """
    from wisp.server import deps

    ws = pathlib.Path(tmp_path)
    rows = ROUTES + [("POST /api/files", "write_file", {"path": ".wisp/hooks/x.sh"})]

    # The consult really is inert here — asserted before the stub replaces it, or this
    # would be a statement about the stub.
    for route, action, args in rows:
        for mode in MODES:
            assert deps._m2_denial(_request(mode, None), action, args, str(ws)) is None, (
                f"{action} in {mode}: the consult refuses with no bundle loaded — "
                "R2's inert case is not inert"
            )

    real = [(route, action, args, mode, _verdict(_request(mode, None), action, args, ws))
            for route, action, args in rows for mode in MODES]

    monkeypatch.setattr(deps, "_m2_denial", lambda *a, **k: None)
    stub = [(route, action, args, mode, _verdict(_request(mode, None), action, args, ws))
            for route, action, args in rows for mode in MODES]

    assert len(real) >= ROW_FLOOR * len(MODES), "the differential shrank"
    for r, s in zip(real, stub):
        assert r == s, (
            f"{r[0]} in {r[3]} differs when the consult is forced to decline: "
            f"{r[4]} -> {s[4]}. With no bundle loaded the consult must contribute nothing "
            "(ADR-0059 R2)."
        )


def test_a_bundle_approve_level_is_inert_on_rest(root, tmp_path):
    """**Residual 1, pinned as a property.** The approval half is not composed.

    If this ever starts denying, the composition grew an approval layer — which
    is a decision (ADR-0059 §A/§B), not a side effect. The test fails so that it
    has to be made deliberately.
    """
    ws = pathlib.Path(tmp_path)
    bundle = _effective({"write_file": "approve", "hooks.create": "approve"})
    for action, args in (("write_file", {"path": "a.py"}),
                         ("hooks.create", {"name": "x"})):
        for mode in MODES:
            with_approve, _ = _verdict(_request(mode, bundle), action, args, ws)
            without, _ = _verdict(_request(mode, None), action, args, ws)
            assert with_approve == without, (
                f"{action} in {mode}: a bundle `approve` level changed the REST "
                f"verdict ({without} -> {with_approve}). The approval half is "
                "not composed (ADR-0059 R3) — composing it 403s the shipped "
                "client with no bundle at all."
            )


def test_the_protected_path_guard_message_survives(root, tmp_path):
    """The guard's message is pinned by `test_protected_path_guard.py`."""
    status, detail = _verdict(
        _request("full", _effective({"write_file": "deny"})),
        "write_file", {"path": ".wisp/hooks/x.sh"}, pathlib.Path(tmp_path))
    assert status == "403"
    assert "protected-path guard" in detail, (
        "the protected-path guard no longer produces its own message — the M2 "
        f"consult now precedes it: {detail!r}"
    )


def test_the_consult_reads_allowed_and_never_approval_required():
    """R3, as a property of the code rather than of a run.

    `approval_required` must not be read in `_m2_denial` — if it is, the gate
    has grown the approval composition that 403s the client. Parsed, not scanned.
    """
    src = (REPO / "wisp/server/deps.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_m2_denial"), None)
    assert fn is not None, "_m2_denial left deps.py"
    names = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "allowed" in names, "_m2_denial no longer reads `.allowed`"
    assert "approval_required" not in names, (
        "_m2_denial reads `.approval_required` — the approval half is now "
        "composed. That is ADR-0059 §A, rejected on measurement."
    )


def test_the_consult_is_gated_on_a_loaded_policy():
    """R2's mechanism, as a property: no policy => the consult returns early."""
    from wisp.server.deps import _m2_denial, organization_policy

    req = _request("auto_edit", None)
    assert organization_policy(req) is None
    assert _m2_denial(req, "write_file", {"path": "a.py"}, "/tmp") is None

    # A request with no root at all (tests, embeddings) must not raise.
    class _Bare:
        class _App:
            class _State:
                pass
            state = _State()
        app = _App()

    assert organization_policy(_Bare()) is None
    assert _m2_denial(_Bare(), "write_file", {"path": "a.py"}, "/tmp") is None


# ── The single load site, and the shared principal ───────────────────

def test_rest_reads_the_roots_single_loaded_policy(root):
    """R5 — one load site, two readers (ADR-0006)."""
    assert root.organization_policy is root.tool_executor.policy, (
        "the root and its executor hold different policy objects — REST would "
        "then be governed by a bundle the agent path is not"
    )
    from wisp.server.deps import organization_policy

    assert organization_policy(_request("auto_edit", None, root_obj=root)) \
        is root.organization_policy


def test_rest_does_not_load_its_own_policy():
    """The REST layer must not become a second load site."""
    src = (REPO / "wisp/server/deps.py").read_text(encoding="utf-8")
    assert "load_local" not in src and "load_managed" not in src, (
        "deps.py now loads a bundle itself — that is a second load site, and "
        "ADR-0006 names exactly one"
    )
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert not [m for m in mods if m == "wisp.policy" or m.startswith("wisp.policy.")], (
        f"deps.py imports the policy package: {sorted(mods)}"
    )


def test_rest_authorizes_as_the_same_principal_as_the_agent(root, tmp_path,
                                                            monkeypatch):
    """R4 — **observed through the real call**, not reconstructed beside it.

    The first version of this test computed the REST principal itself and
    compared it with the agent's. That passed even when `deps.py` was mutated to
    pass `None` — it asserted a property of a *reconstruction*, not of the
    production call. The non-vacuity probe caught it (NV4). This version captures
    the argument the real `_m2_denial` actually passes.
    """
    import wisp.auth.principal as P

    from wisp.server.deps import _m2_denial

    real = P.executor_principal
    captured: dict = {}

    def spy(tool_executor, **kwargs):
        captured["executor"] = tool_executor
        captured["kwargs"] = kwargs
        return real(tool_executor, **kwargs)

    monkeypatch.setattr(P, "executor_principal", spy)

    ws = str(tmp_path)
    bundle = _effective({"write_file": "deny"})
    original = root.organization_policy
    root.organization_policy = bundle
    try:
        decision = _m2_denial(_request("auto_edit", None, root_obj=root),
                              "write_file", {"path": "a.py"}, ws)
    finally:
        root.organization_policy = original

    assert decision is not None, (
        "floor: the consult did not reach a decision, so nothing was observed"
    )
    assert "executor" in captured, "_m2_denial no longer calls executor_principal"
    assert captured["executor"] is root.tool_executor, (
        "the REST gate derives its principal from something other than the "
        "composition root's executor — the two paths can now disagree about who "
        "an effect is attributed to (ADR-0059 R4)"
    )
    assert real(root.tool_executor, **captured["kwargs"]) \
        == root.tool_executor._effective_principal(ws), (
        "the REST gate and the agent path authorize as different principals"
    )


# ── The four non-violations, re-asserted in the composed state ───────

def test_non_violation_1_authorize_is_unchanged(tmp_path):
    from wisp.auth.decision import AuthorizationDecision, authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    assert list(inspect.signature(authorize).parameters) == [
        "principal", "tool_name", "args", "workspace_trust",
        "permission_mode", "sensitivity", "effective_policy",
    ]
    assert {"allowed", "reason", "controlling_layer",
            "approval_required"} <= set(AuthorizationDecision.__dataclass_fields__)

    principal = local_principal(workspace=str(tmp_path), profile="local")
    observed = set()
    for mode in MODES:
        for action, args in (("write_file", {"path": "a.py"}),
                             ("run_bash", {"command": "ls"}),
                             ("read_file", {"path": "a.py"})):
            observed.add(authorize(principal, action, args,
                                   workspace_trust=WorkspaceTrust.TRUSTED,
                                   permission_mode=mode).controlling_layer)
    assert observed, "floor: no layer exercised"
    assert len(observed) >= 2, f"only one layer reachable: {observed}"
    assert observed <= CONTROLLING_LAYERS, (
        f"authorize() returned a layer outside the vocabulary: "
        f"{observed - CONTROLLING_LAYERS}"
    )


def test_non_violation_2_security_policy_check_is_unchanged():
    from wisp.infra.security import SecurityPolicy

    assert list(inspect.signature(SecurityPolicy.check).parameters) == [
        "self", "action", "context",
    ], "SecurityPolicy.check's signature moved — REST may now receive a bundle"
    assert not [n for n in dir(SecurityPolicy) if "policy" in n.lower()], (
        "SecurityPolicy grew a policy-shaped attribute. ADR-0059 did NOT take "
        "that route: REST reads the root's loaded policy, not a SecurityPolicy "
        "slot — so this appearing means a second design landed."
    )


def test_non_violation_3_the_agent_gate_chain_is_unchanged():
    """`policy_hard_deny` -> `authorize` -> approval, parsed not scanned."""
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "execute")
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
        f"the agent's gate chain was re-ordered: {first}"
    )


def test_non_violation_4_turn_authorities_are_untouched():
    """`turn_succeeded`, `VerificationFloorGuard`, `goal.PRECEDENCE`."""
    from wisp.core.goal import PRECEDENCE
    from wisp.core.verification import VerificationFloorGuard

    assert len(PRECEDENCE) == 8, "the canonical precedence table is eight rows 0-7"
    assert [row[0] for row in PRECEDENCE] == list(range(8))
    assert PRECEDENCE[4][2] == "GOAL_FAILED" and "no P3 PASS" in PRECEDENCE[4][1], (
        "row 4 (the fatal clause) changed — resolve 'row N' by content (ADR-0049)"
    )
    for method in ("note_tool_result", "rejection", "reset_turn"):
        assert callable(getattr(VerificationFloorGuard, method, None)), (
            f"VerificationFloorGuard.{method} left the class"
        )
    runtime = (REPO / "wisp/core/runtime.py").read_text(encoding="utf-8")
    assert "_goal_outcome is TerminalOutcome.SUCCEEDED" in runtime, (
        "turn_succeeded no longer derives from the terminal outcome"
    )
