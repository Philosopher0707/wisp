"""ADR-0058 — the key-trust workflow, its premises, and the three non-violations.

Context: `PHASE_KEY_TRUST_WORKFLOW.md`.

The ADR decides a *workflow*, and every rule it states rests on something the code
already does. These tests pin the premises (so the decision cannot be silently
invalidated by a change to `wisp/policy/`) and the three non-violations the ADR
names. They also pin the two rules that are mechanically checkable — R3
(invalidity refuses) and R4 (expiry narrows, it does not refuse) — and the two
that are structural: R6 (offline) and R7 (no private key enters Wisp).

**What this file cannot drive, and says so.** `cryptography` is absent from this
environment (`pyproject.toml:27` declares it; `uv.lock:472` pins 50.0.1), so
`verify_bundle` returns `False` for everything — its `except Exception` swallows
the failed import. Consequently `load_local` **always raises** here, and a real
signature round-trip cannot be exercised. That is a finding, not a test gap; see
`PHASE_KEY_TRUST_WORKFLOW.md` §6. The rules are pinned through the pure
functions and the signatures, which do not need the dependency.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

DECISION = REPO / "WISP_ARCHITECTURE_DECISIONS.md"
LOADER = REPO / "wisp/policy/loader.py"
CLI = REPO / "wisp/policy/cli.py"

#: ADR-0058's eight rules, by id. A rule deleted from the ADR is a rule the
#: decision no longer states.
RULE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# ── The premises the decision rests on ───────────────────────────────
#
# Driven facts, not restatements. If any of these changes, the workflow the ADR
# describes changes with it — and the ADR is what says so.

def test_the_pubkey_is_key_material_not_a_path():
    """`WISP_POLICY_PUBKEY` carries the key, it does not name a file.

    This single fact decides the workflow shape: *"export this value"*, not
    *"place a file at this path"*. The commissioning brief's worked example had
    it as a path; driven, it is base64 key material.
    """
    from wisp.policy.bundle import verify_bundle
    from wisp.policy.loader import load_local

    assert list(inspect.signature(load_local).parameters) == [
        "bundle_path", "public_key_b64",
    ], "load_local's second parameter is the key itself, not a path to it"
    assert list(inspect.signature(verify_bundle).parameters) == [
        "bundle", "signature_b64", "public_key_b64",
    ]
    assert "WISP_POLICY_PUBKEY  base64 org public key" in CLI.read_text(encoding="utf-8")


def test_the_bundle_setting_is_a_path():
    """`WISP_POLICY_BUNDLE` names a file, and its signature is a sibling."""
    from wisp.policy.loader import _sig_path

    assert _sig_path(pathlib.Path("/x/bundle.json")) == pathlib.Path("/x/bundle.json.sig")
    assert "WISP_POLICY_BUNDLE  path to bundle.json (+ .sig sibling)" in (
        CLI.read_text(encoding="utf-8")
    )


# ── R3 — invalidity refuses to boot ──────────────────────────────────

def test_an_unverifiable_bundle_raises_rather_than_degrading(tmp_path):
    """R3. The loader raises; the rule this ADR adds is that the caller propagates.

    Driven with a bundle whose `.sig` is present but not a signature.

    **The assertion pins the MESSAGE, not just the type.** Written as a bare
    `pytest.raises(ValueError)` this test does not falsify its own claim: the
    loader reaches `_bundle_to_effective` for a bundle whose `expires_at` is
    falsy and raises `ValueError("min() iterable argument is empty")` from an
    unrelated defect, which satisfies the type. The non-vacuity probe (NV1)
    found exactly that. Matching the message pins the *cause*.
    """
    import json

    from wisp.policy.bundle import PolicyBundle
    from wisp.policy.loader import load_local

    bundle_file = tmp_path / "bundle.json"
    bundle_file.write_text(json.dumps(PolicyBundle(org_id="acme").to_dict()),
                           encoding="utf-8")
    (tmp_path / "bundle.json.sig").write_text("not-a-signature", encoding="utf-8")

    with pytest.raises(ValueError, match="signature invalid"):
        load_local(bundle_file, "not-a-key")


def test_a_missing_signature_also_raises(tmp_path):
    """A bundle with no `.sig` sibling is not 'unsigned and therefore permissive'."""
    import json

    from wisp.policy.bundle import PolicyBundle
    from wisp.policy.loader import load_local

    bundle_file = tmp_path / "bundle.json"
    bundle_file.write_text(json.dumps(PolicyBundle(org_id="acme").to_dict()),
                           encoding="utf-8")
    with pytest.raises(ValueError, match="signature invalid"):
        load_local(bundle_file, "not-a-key")


# ── R4 — expiry narrows; it does not refuse ──────────────────────────

def test_expiry_narrows_every_approval_and_the_network():
    """R4. A stale-but-honest bundle stays *applied*, at its strictest.

    Pinned on the pure function, which needs no dependency. Refusing to boot on
    expiry would turn a stale bundle into an outage; trimming keeps the control
    on while the operator re-issues.
    """
    from wisp.policy.loader import EffectivePolicy, trim_expired

    eff = EffectivePolicy(
        approval_matrix={"run_bash": "allow", "write_file": "approve"},
        network_policy={"mode": "on"}, org_id="acme", expires_at=0.0,
    )
    out = trim_expired(eff)

    assert out.approval_matrix, "floor: the matrix must be non-empty to be narrowed"
    assert set(out.approval_matrix.values()) == {"deny"}, (
        "every approval must become `deny` after expiry — never a silent allow"
    )
    assert out.network_policy == {"mode": "off"}
    assert out.provenance.get("expired-trim") == "built-in floor"
    assert out.org_id == "acme", "trimming narrows; it does not discard identity"


def test_is_expired_is_true_at_and_after_the_instant():
    import time

    from wisp.policy.bundle import PolicyBundle

    assert PolicyBundle(org_id="a").is_expired() is True, "expires_at=0.0 is expired"
    assert PolicyBundle(org_id="a", expires_at=time.time() + 3600).is_expired() is False


# ── R6 — offline by construction ─────────────────────────────────────

def test_the_local_loader_is_offline_and_imports_no_network_module():
    """R6. `local-only` is the spec's §3 mode, and the module says so twice."""
    src = LOADER.read_text(encoding="utf-8")
    assert "No network, ever" in src, "load_local's docstring no longer promises offline"

    network = {
        "socket", "ssl", "http", "urllib", "urllib.request", "urllib3",
        "requests", "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib",
    }
    imported: set[str] = set()
    for node in ast.walk(_tree(LOADER)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert imported, "floor: the module imports something"
    assert not (imported & network), (
        f"the local loader now imports a network module: {sorted(imported & network)}"
    )


# ── R7 — the private key is never Wisp's to hold ─────────────────────

def test_no_runtime_surface_accepts_a_private_key():
    """R7. Wisp reads a public key; signing is the operator's, outside the runtime.

    `sign_bundle(bundle, private_key)` exists in `wisp/policy/` — that is the
    operator's tool, reachable only from the CLI and tests. No module that runs
    a turn takes a private key.
    """
    runtime_modules = (
        "wisp/composition.py", "wisp/config.py", "wisp/tool_executor.py",
        "wisp/acp_session.py", "wisp/core/stateless.py", "wisp/core/runtime.py",
    )
    offenders: dict[str, list[str]] = {}
    for rel in runtime_modules:
        path = REPO / rel
        if not path.exists():
            continue
        hits = [
            f"{node.name}({', '.join(a.arg for a in node.args.args)})"
            for node in ast.walk(_tree(path))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "private_key" in [a.arg for a in node.args.args]
        ]
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        f"a runtime module now accepts a private key: {offenders}"
    )


# ── The three non-violations, asserted not merely stated ─────────────

def test_non_violation_1_authorize_is_unchanged(tmp_path):
    """`authorize()`: same parameters, same decision fields, same L0 slot."""
    from wisp.auth.decision import AuthorizationDecision, authorize

    assert list(inspect.signature(authorize).parameters) == [
        "principal", "tool_name", "args", "workspace_trust",
        "permission_mode", "sensitivity", "effective_policy",
    ], "authorize()'s signature moved"
    assert {"allowed", "reason", "controlling_layer", "approval_required"} <= set(
        AuthorizationDecision.__dataclass_fields__
    )

    # L0 still exists, is still optional, and still names the organization layer.
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust
    from wisp.policy.loader import EffectivePolicy

    principal = local_principal(workspace=str(tmp_path), profile="local")
    inert = authorize(principal, "run_bash", {"command": "ls"},
                      workspace_trust=WorkspaceTrust.TRUSTED, permission_mode="full")
    assert inert.allowed is True, "with no bundle, L0 has no opinion"

    deny = EffectivePolicy(approval_matrix={"run_bash": "deny"},
                           provenance={"approval:run_bash": "organization"},
                           org_id="acme")
    decision = authorize(principal, "run_bash", {"command": "ls"},
                         workspace_trust=WorkspaceTrust.TRUSTED, permission_mode="full",
                         effective_policy=deny)
    assert decision.allowed is False
    assert decision.controlling_layer == "organization"


def test_non_violation_2_the_policy_package_is_unchanged():
    """`wisp/policy/`'s entry points and its exported surface."""
    from wisp.policy import __all__ as exported
    from wisp.policy.loader import load_local, load_managed, merge_all

    assert exported, "floor: the package exports something"
    assert set(exported) == {
        "BUNDLE_VERSION", "EffectivePolicy", "PolicyBundle", "canonical_bytes",
        "dry_run", "explain_denial", "generate_keypair", "load_local",
        "load_managed", "merge_all", "merge_layers", "private_bytes_raw",
        "sign_bundle", "trim_expired", "verify_bundle",
    }, "wisp/policy/'s exported surface moved"

    assert list(inspect.signature(load_local).parameters) == ["bundle_path", "public_key_b64"]
    assert list(inspect.signature(load_managed).parameters)[:2] == [
        "cache_dir", "public_key_b64",
    ]
    assert list(inspect.signature(merge_all).parameters) == ["layers"]


def test_non_violation_3_the_executor_policy_still_defaults_to_none():
    """The wiring is Deliverable 2's; the default is not touched here."""
    from wisp.tool_executor import ToolExecutor

    assert inspect.signature(ToolExecutor.__init__).parameters["policy"].default is None
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    assert "self.policy = policy" in src
    assert "effective_policy=self.policy," in src, (
        "the executor no longer hands its policy to authorize()'s L0"
    )


# ── The decision is recorded ─────────────────────────────────────────

def test_the_decision_is_recorded_and_states_its_rules():
    """**Scoped to ADR-0058's own section.** Searching the whole file is
    non-falsifying: `**R4 —` occurs in a dozen other ADRs, so renaming this
    ADR's R4 would still be found somewhere (found by probe NV7)."""
    text = DECISION.read_text(encoding="utf-8")
    assert "## ADR-0058 —" in text, "ADR-0058 is missing"
    body = text.split("## ADR-0058", 1)[1].split("\n---\n", 1)[0]
    assert len(body) > 2000, "floor: the ADR's section was not extracted"
    for rule in RULE_IDS:
        assert f"**{rule} —" in body, f"ADR-0058 no longer states {rule}"
    assert "**Status:** ACCEPTED" in body[:400]
    assert "| 0058 |" in text, "ADR-0058 has no index row"
