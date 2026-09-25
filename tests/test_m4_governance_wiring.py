"""The M4 governance layer is not wired to the runtime (Phase 10).

`wisp/policy/` implements signed organization policy bundles, narrow-only
merge, provenance, offline continuity, a CLI, and four server routes. **Nothing
in the runtime loads one.** `ToolExecutor.policy` is `None` at every
construction site, and `authorize()`'s L0 layer — which works correctly when
given a bundle — never receives one.

See `PHASE_10_M4_GOVERNANCE_UNWIRED.md` for the full finding, the root cause
(the M4 spec has no wiring section), and why the wiring is deliberately not
implemented here.

These tests **pin the current state**. They are not an endorsement of it: the
tripwire tests below are written to fail the moment someone wires the layer, so
that wiring it and updating the document cannot drift apart.
"""

from __future__ import annotations

import ast
import pathlib
from io import StringIO

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Modules that build or execute a turn. If any of these imports `wisp.policy`,
#: the seam has been connected. `wisp/__main__.py` is excluded on purpose: it
#: imports `wisp.policy.cli` to expose the `policy` subcommand, which is a CLI
#: surface rather than runtime enforcement.
RUNTIME_MODULES = (
    "wisp/composition.py",
    "wisp/tool_executor.py",
    "wisp/acp_session.py",
)


# ── The mechanism is real ────────────────────────────────────────────

def _deny_bundle():
    from wisp.policy.loader import EffectivePolicy

    return EffectivePolicy(
        approval_matrix={"run_bash": "deny"},
        provenance={"approval:run_bash": "organization"},
        org_id="acme",
    )


def test_l0_denies_when_a_bundle_is_supplied(tmp_path):
    """The organization layer works — this is not a theoretical seam."""
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    principal = local_principal(workspace=str(tmp_path), profile="local")
    decision = authorize(principal, "run_bash", {"command": "ls"},
                         workspace_trust=WorkspaceTrust.TRUSTED,
                         permission_mode="full",
                         effective_policy=_deny_bundle())
    assert decision.allowed is False
    assert decision.controlling_layer == "organization"


def test_l0_is_inert_without_a_bundle(tmp_path):
    """And it is inert by default — which is the runtime's situation."""
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    principal = local_principal(workspace=str(tmp_path), profile="local")
    decision = authorize(principal, "run_bash", {"command": "ls"},
                         workspace_trust=WorkspaceTrust.TRUSTED,
                         permission_mode="full",
                         effective_policy=None)
    assert decision.allowed is True


# ── The seam is empty ────────────────────────────────────────────────

def _tool_executor_calls() -> list[tuple[str, int, bool]]:
    """(file, line, passes_policy_kwarg) for every ToolExecutor(...) call."""
    out = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "ToolExecutor":
                continue
            passes = any(kw.arg == "policy" for kw in node.keywords)
            out.append((str(py.relative_to(REPO)), node.lineno, passes))
    return out


def test_tool_executor_construction_sites_are_known():
    """**Three** sites. The count was 2 until `8a7e9ab` added the third.

    `8a7e9ab` (the autonomous-convergence chain) wired an executor into
    `benchmark/runner.py::make_ollama_core_factory`. That is **authorised** —
    it is F54 in **ADR-0045**: `_execute_tool`'s no-executor fallback permits
    `READ` tools only, so `wisp bench` was refusing every mutation and
    reporting FAIL for tasks no agent could pass. The fix was deliberate and
    measured (0 passed/2 failed/2 timeout → 3 passed/0 failed/1 timeout).

    So this count is a **state**, and it went stale the moment a legitimate
    site was authorised — which is exactly why the failure message says
    *"check whether it passes a policy bundle"* rather than *"this must never
    change"*. The property this file exists for is the tripwire **below**:
    no site passes a policy bundle. This assertion only keeps the inventory
    honest, and it names the files so that a fourth site cannot hide behind a
    count that a swap would leave unchanged.
    """
    sites = _tool_executor_calls()
    assert len(sites) == 3, (
        "a new ToolExecutor construction site appeared — check whether it "
        f"passes a policy bundle: {sites}"
    )
    assert {rel for rel, _line, _passes in sites} == {
        "wisp/composition.py",       # the composition root
        "wisp/acp_session.py",       # the ACP session's config-driven fallback
        "wisp/benchmark/runner.py",  # the benchmark factory — ADR-0045, F54
    }, f"the construction-site inventory changed: {sites}"


def test_no_tool_executor_is_constructed_with_a_policy():
    """**TRIPWIRE.** Fails when the M4 layer is wired.

    That is the point. Wiring it is a deliberate change that must be made
    together with the decision recorded in PHASE_10_M4_GOVERNANCE_UNWIRED.md —
    not a silent edit that leaves the document claiming the layer is inert.
    """
    wired = [s for s in _tool_executor_calls() if s[2]]
    assert not wired, (
        "a ToolExecutor now receives a policy bundle — the M4 governance layer "
        "has been wired. Update PHASE_10_M4_GOVERNANCE_UNWIRED.md (and this "
        f"test) to record the decision: {wired}"
    )


def test_the_runtime_never_imports_the_policy_package():
    """The seam is empty, not merely unpopulated."""
    offenders = {}
    for rel in RUNTIME_MODULES:
        py = REPO / rel
        if not py.exists():
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        hits = []
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                if m == "wisp.policy" or m.startswith("wisp.policy."):
                    hits.append(node.lineno)
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "a runtime module now imports the policy package — update the M4 "
        f"finding rather than leaving it stale: {offenders}"
    )


def test_config_has_no_policy_bundle_setting():
    """There is no way to configure a bundle today."""
    src = (REPO / "wisp/config.py").read_text(encoding="utf-8")
    assert "policy" not in src.lower(), (
        "config.py now references a policy setting — the layer may be "
        "configurable. Re-read PHASE_10_M4_GOVERNANCE_UNWIRED.md §6"
    )


def test_the_agent_passes_its_none_policy_through():
    """Documents the exact line that makes L0 inert."""
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    assert "self.policy = policy" in src
    assert "effective_policy=self.policy," in src
    # The parameter defaults to None and no caller overrides it.
    assert "policy: Any = None," in src


# ── The distribution surface is inert too ────────────────────────────

def test_the_publish_route_holds_a_bundle_that_nothing_reads():
    """`app.state.policy_bundle` has exactly one writer and no decision reader.

    Matches the *held bundle* attribute specifically — `ReproManifest` has an
    unrelated `policy_bundle_id` field (see the test below).
    """
    holders = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if "state.policy_bundle" in py.read_text(encoding="utf-8"):
            holders.append(str(py.relative_to(REPO)))
    assert holders == ["wisp/server/routes/policy.py"], (
        "something outside the policy route now reads or writes the held "
        f"bundle — re-examine the finding: {holders}"
    )


def test_the_repro_manifest_policy_field_is_never_populated():
    """Corroboration: the run manifest has a 'which policy governed this run'
    field, declared and serialized, that is always empty — because no policy
    ever governs a run."""
    holders = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if "policy_bundle_id" in py.read_text(encoding="utf-8"):
            holders.append(str(py.relative_to(REPO)))
    assert holders == ["wisp/runs/repro.py"], (
        "policy_bundle_id is now set somewhere — the M4 layer may have been "
        f"wired. Re-read the finding: {holders}"
    )


def test_the_distribution_surface_is_unconfigured_in_production():
    """`policy_pubkey` is set by tests only, so the routes 503 in a real server."""
    setters = []
    for py in list((REPO / "wisp").rglob("*.py")) + list((REPO / "tests").rglob("*.py")):
        if "__pycache__" in py.parts or py.name == pathlib.Path(__file__).name:
            continue
        text = py.read_text(encoding="utf-8")
        if "policy_pubkey =" in text or "policy_pubkey=" in text:
            setters.append(str(py.relative_to(REPO)))
    assert setters == ["tests/test_policy_routes.py"], (
        "policy_pubkey is now set outside tests — the distribution surface may "
        f"be configured in production. Re-read the finding: {setters}"
    )


# ── The root cause ───────────────────────────────────────────────────

def test_the_m4_spec_still_has_no_wiring_section():
    """The root cause is a specification gap, not a scheduling one.

    If a wiring section appears, the spec has been fixed and this document
    should be re-read before acting on it.
    """
    spec = (REPO / "docs/superpowers/specs/2026-09-04-m4-policy-design.md")
    text = spec.read_text(encoding="utf-8")
    headings = [ln for ln in text.splitlines() if ln.startswith("#")]
    assert not any("wiring" in h.lower() or "integration" in h.lower()
                   for h in headings), (
        "the M4 spec now has a wiring/integration section — the root cause has "
        "changed; re-read PHASE_10_M4_GOVERNANCE_UNWIRED.md §5"
    )
    # And the Deferred list does not mention it either.
    deferred = text.split("## 5. Deferred", 1)[-1].lower()
    assert "wir" not in deferred, (
        "runtime wiring was moved into the Deferred list — the finding's root "
        "cause has changed"
    )


def test_the_loader_entry_points_have_no_runtime_caller():
    """`load_local`/`load_managed` are reachable only from the CLI and tests.

    **The assertion is the property, not a file list.** It used to pin the exact
    set `{wisp/policy/cli.py, tests/test_policy_modes.py}` — a *state*, and it
    fired on the next legitimate addition: a second test file driving the loader
    (`tests/reliability/test_key_trust_workflow.py`, ADR-0058's guard) is not
    *"the layer may be wired"*, and the sentence above already says tests are
    expected. Stated as a rule: **the CLI, and tests — nothing else.**
    """
    callers = set()
    for base in ("wisp", "tests"):
        for py in (REPO / base).rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if name in ("load_local", "load_managed", "merge_all"):
                        callers.add(str(py.relative_to(REPO)))
    assert callers, "floor: the scan must reach the callers it already knows about"
    assert "wisp/policy/cli.py" in callers, (
        "the CLI no longer calls the bundle loaders — the scan or the CLI changed"
    )
    outside = {c for c in callers if c != "wisp/policy/cli.py" and not c.startswith("tests/")}
    assert not outside, (
        "a caller of the bundle loaders appeared outside the CLI and tests — the "
        f"runtime layer may be wired: {sorted(outside)}"
    )


def test_the_finding_is_documented():
    """The document this file pins must exist."""
    doc = REPO / "PHASE_10_M4_GOVERNANCE_UNWIRED.md"
    assert doc.exists(), "the M4 finding document was removed"
    assert "not wired" in doc.read_text(encoding="utf-8").lower()


# ── Option C: nothing may imply enforcement ──────────────────────────
#
# The harm of the finding is false assurance. These tests pin the honesty
# pass, so it cannot be silently reverted while the layer stays unwired.

@pytest.mark.parametrize("command", ["inspect", "verify", "explain", "dry-run",
                                     "health"])
def test_every_evaluating_command_warns_that_it_is_not_enforced(command):
    """Each command whose output reads as an in-force verdict says otherwise."""
    from wisp.policy.cli import main

    out = StringIO()
    main([command], out=out)
    assert "NOT ENFORCED" in out.getvalue(), (
        f"`wisp policy {command}` no longer warns that the bundle is not "
        "applied at runtime"
    )


def test_transport_commands_do_not_need_the_warning():
    """`export` moves a file; it makes no claim about permissions."""
    from wisp.policy.cli import main

    out = StringIO()
    main(["export"], out=out)
    assert "NOT ENFORCED" not in out.getvalue()


def test_the_notice_names_the_finding_document():
    """A warning without a pointer is not actionable."""
    from wisp.policy.cli import _NOT_ENFORCED_NOTICE

    assert "PHASE_10_M4_GOVERNANCE_UNWIRED.md" in _NOT_ENFORCED_NOTICE


def test_explain_states_a_rule_not_a_result():
    """`Denied X` reports something that did not happen.

    The function describes a bundle, so it must phrase its output as a rule.
    """
    from wisp.policy.explain import explain_denial
    from wisp.policy.loader import EffectivePolicy

    eff = EffectivePolicy(approval_matrix={"run_bash": "deny"},
                          provenance={"approval:run_bash": "organization"})
    text = explain_denial("run_bash", {"command": "ls"}, eff)
    assert text.startswith("Rule: deny")
    assert "Denied" not in text, "explain_denial reports a denial as if it happened"
    assert "organization" in text  # provenance is still surfaced


@pytest.mark.parametrize("rel, marker", [
    ("README.md", "not yet applied at runtime"),
    ("AGENTS.md", "Not wired to the runtime"),
    ("docs/SECURITY.md", "Not enforced at runtime"),
])
def test_the_docs_do_not_claim_the_policy_is_enforced(rel, marker):
    """Every doc that advertises the governance layer carries the qualifier."""
    text = (REPO / rel).read_text(encoding="utf-8")
    assert marker in text, (
        f"{rel} advertises the policy layer without saying it is not enforced"
    )


def test_the_readme_does_not_claim_an_active_policy_for_graphs():
    """`validate_graph` reads the graph's own policy — there is no external one.

    The earlier wording ("narrow-only over the active policy") implied an org
    policy the graph narrows against, which does not exist.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "narrow-only over the active policy" not in readme
