"""ADR-0058's wiring — the four-step recipe in `PHASE_10_M4_GOVERNANCE_UNWIRED.md` §6.

The layer is wired **off by default**: with `WISP_POLICY_BUNDLE` unset, no
`wisp.policy` code runs and the runtime is byte-for-byte today's behaviour (R2).
It engages when the path is set (R1), refuses to boot when the bundle cannot be
verified (R3), and is served *trimmed* when the bundle has expired (R4).

**What runs on the real path, and what does not.** The no-op and the refusal
cases drive the **real** `CompositionRoot` and the **real** loader. The *engages*
case cannot: a verifiable bundle needs `cryptography`, which is absent from this
environment (declared at `pyproject.toml:27`, pinned at `uv.lock:472`; finding
**F88**), so `load_local` always raises here. That one test replaces the
**acquisition** step with a stub and says so — what it exercises is the wiring,
not the signature check, and the signature check is pinned separately in
`tests/reliability/test_key_trust_workflow.py` (R3, by message).

The three non-violations the ADR names are re-asserted here **in the wired
state**. `test_key_trust_workflow.py` asserts them before the wiring exists; the
pair is the point — the change that could have violated them is this one.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

UNSET = ("WISP_POLICY_BUNDLE", "WISP_POLICY_PUBKEY")


@pytest.fixture
def clean_env(monkeypatch):
    """No policy configured — the default the wiring must be inert under."""
    for name in UNSET:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _root(tmp_path, **overrides):
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    cfg = WispConfig().replace(workspace=str(tmp_path), **overrides)
    return CompositionRoot(config=cfg)


def _bundle_files(tmp_path, *, sig: str | None = "not-a-signature"):
    import json

    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"org_id": "acme", "expires_at": 1}), encoding="utf-8")
    if sig is not None:
        (tmp_path / "bundle.json.sig").write_text(sig, encoding="utf-8")
    return bundle


# ── R2 — absence is inert, on the real path ──────────────────────────

def test_unset_is_a_no_op_on_the_real_composition_root(tmp_path, clean_env):
    """The default. No env vars → no bundle → `ToolExecutor.policy is None`.

    Driven through a real `CompositionRoot`, not through the helper: the property
    is that the *runtime* is unchanged, and only the runtime can show that.
    """
    from wisp.composition import load_organization_policy
    from wisp.config import WispConfig

    assert load_organization_policy(WispConfig()) is None

    root = _root(tmp_path)
    assert root.tool_executor.policy is None, (
        "with no bundle configured the executor must carry no policy — the "
        "default is the whole safety property of this wiring"
    )


# ── R3 — invalidity refuses to boot, on the real path ────────────────

def test_a_named_but_unverifiable_bundle_refuses_to_boot(tmp_path, clean_env):
    """R3. `CompositionRoot(...)` raises; the process does not start."""
    bundle = _bundle_files(tmp_path)
    with pytest.raises(ValueError, match="signature invalid"):
        _root(tmp_path, policy_bundle=str(bundle), policy_pubkey="not-a-key")


def test_a_missing_bundle_file_refuses_to_boot(tmp_path, clean_env):
    """A path that names nothing is a misconfiguration, not an absence."""
    with pytest.raises(FileNotFoundError):
        _root(tmp_path, policy_bundle=str(tmp_path / "absent.json"), policy_pubkey="k")


def test_a_bundle_with_no_signature_refuses_to_boot(tmp_path, clean_env):
    """No `.sig` sibling is not 'unsigned and therefore permissive'."""
    bundle = _bundle_files(tmp_path, sig=None)
    with pytest.raises(ValueError, match="signature invalid"):
        _root(tmp_path, policy_bundle=str(bundle), policy_pubkey="not-a-key")


# ── R1 — a configured bundle reaches the executor ────────────────────

def test_a_configured_bundle_reaches_the_executor(tmp_path, clean_env, monkeypatch):
    """The wiring half, with the acquisition half stubbed.

    `load_local` is replaced because a verifiable bundle needs `cryptography`,
    which is absent (F88). The stub returns a real `EffectivePolicy`, so what is
    exercised — *does the composition root pass the loader's result to the single
    construction site?* — is the production path with its acquisition step
    swapped. The acquisition step's own contract is pinned in
    `test_key_trust_workflow.py`.
    """
    from wisp.policy.loader import EffectivePolicy

    sentinel = EffectivePolicy(
        approval_matrix={"run_bash": "deny"},
        provenance={"approval:run_bash": "organization"},
        org_id="acme",
    )
    import wisp.policy.loader as loader

    monkeypatch.setattr(loader, "load_local", lambda path, key: sentinel)

    root = _root(tmp_path, policy_bundle="/etc/wisp/bundle.json", policy_pubkey="a2V5")
    assert root.tool_executor.policy is sentinel, (
        "the composition root did not pass the loaded bundle to the executor"
    )


def test_the_loaded_bundle_actually_denies(tmp_path, clean_env):
    """And the value is not inert where it lands: `authorize()`'s L0 consumes it.

    A wiring that hands over a policy nothing reads would be a new instance of
    the pattern `PHASE_10_M4_GOVERNANCE_UNWIRED.md` §4 diagnoses.
    """
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust
    from wisp.policy.loader import EffectivePolicy

    bundle = EffectivePolicy(approval_matrix={"run_bash": "deny"},
                             provenance={"approval:run_bash": "organization"},
                             org_id="acme")
    principal = local_principal(workspace=str(tmp_path), profile="local")
    decision = authorize(principal, "run_bash", {"command": "ls"},
                         workspace_trust=WorkspaceTrust.TRUSTED, permission_mode="full",
                         effective_policy=bundle)
    assert decision.allowed is False
    assert decision.controlling_layer == "organization"


# ── REST receives L0 — through the root, NOT through a SecurityPolicy slot ──

def test_rest_receives_l0_through_the_root_not_a_security_policy_slot():
    """**The pin, inverted** (ADR-0059; the P9/M15 and M11/M13 precedent).

    It used to assert that REST does **not** receive L0, because
    `SecurityPolicy` has no organization slot and a bundle loaded into
    `request_policy` would have been **dead data** — a new instance of the
    pattern the M4 finding diagnoses.

    ADR-0059 closes the gap **without** taking that route: `require_tool_allowed`
    consults `authorize()` with the policy the **composition root** loaded, so
    the two assertions below are still true and still worth pinning — they now
    say *which route was not taken* rather than *that the gap is open*. If
    `SecurityPolicy` ever grows a policy slot, a second design has landed and
    this test fails so that it is noticed.
    """
    from wisp.infra.security import SecurityPolicy

    assert not [n for n in dir(SecurityPolicy) if "policy" in n.lower()], (
        "SecurityPolicy grew a policy-shaped attribute — a SECOND way for REST to "
        "receive L0 has appeared. ADR-0059 chose the root's loaded policy "
        "(one load site, ADR-0006); re-read PHASE_REST_AUTHORIZATION_COMPOSITION.md."
    )
    assert list(inspect.signature(SecurityPolicy.check).parameters) == [
        "self", "action", "context",
    ], "SecurityPolicy.check's signature moved — it may now take a bundle"

    # And the route that WAS taken, as a property of the code.
    src = (REPO / "wisp/server/deps.py").read_text(encoding="utf-8")
    assert "organization_policy" in src, (
        "require_tool_allowed no longer reaches the root's loaded policy — "
        "ADR-0059's composition is gone and the L0 gap is open again"
    )
    assert "load_local" not in src and "load_managed" not in src, (
        "deps.py now loads a bundle itself: that is a SECOND load site, and "
        "ADR-0006 names exactly one"
    )


# ── The three non-violations, re-asserted in the WIRED state ─────────

def test_non_violation_1_authorize_is_unchanged_after_the_wiring(tmp_path):
    from wisp.auth.decision import AuthorizationDecision, authorize

    assert list(inspect.signature(authorize).parameters) == [
        "principal", "tool_name", "args", "workspace_trust",
        "permission_mode", "sensitivity", "effective_policy",
    ]
    assert {"allowed", "reason", "controlling_layer",
            "approval_required"} <= set(AuthorizationDecision.__dataclass_fields__)


def test_non_violation_2_the_policy_package_is_unchanged_by_the_wiring():
    """The wiring adds a **caller**; it does not change the module."""
    from wisp.policy import __all__ as exported
    from wisp.policy.loader import load_local

    assert exported, "floor: the package exports something"
    assert list(inspect.signature(load_local).parameters) == ["bundle_path", "public_key_b64"]
    src = (REPO / "wisp/policy/loader.py").read_text(encoding="utf-8")
    assert "No network, ever" in src, "load_local's offline promise was changed"


def test_non_violation_3_the_executor_policy_still_defaults_to_none():
    """The wiring passes `policy=` **only** from the composition root."""
    from wisp.tool_executor import ToolExecutor

    assert inspect.signature(ToolExecutor.__init__).parameters["policy"].default is None
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    assert "self.policy = policy" in src
    assert "effective_policy=self.policy," in src
