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

#: Modules that build or execute a turn. ADR-0058 wires the organization policy
#: at exactly **one** of them — the composition root — so the seam is a single
#: wire, not an empty space and not a mesh. `wisp/__main__.py` is excluded on
#: purpose: it imports `wisp.policy.cli` to expose the `policy` subcommand, which
#: is a CLI surface rather than runtime enforcement.
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


def test_the_composition_root_passes_the_organization_policy():
    """**The tripwire, inverted** (ADR-0058; the P9/M15 and M11/M13 precedent).

    It used to assert that *no* site passes `policy=`, and it was written to fail
    the moment the layer was wired. The layer is now wired — deliberately, in the
    same change that records the decision — so the assertion is its inverse:
    **exactly one site passes it, and it is the composition root**, ADR-0006's
    single construction site.

    The other two sites are named and must **not** receive it:

    * `wisp/acp_session.py:208` — reached only when there is **no** composition
      root; an ACP-only deployment has no configured runtime to load from.
      Named as a residual in `PHASE_M4_WIRING.md`.
    * `wisp/benchmark/runner.py:82` — a benchmark **harness**, not the runtime.
      Giving it a policy would change what it measures.

    The *value* is `load_organization_policy(self.config)`, which is `None` when
    `WISP_POLICY_BUNDLE` is unset — so the default is unchanged.
    """
    wired = {rel for rel, _line, passes in _tool_executor_calls() if passes}
    assert wired == {"wisp/composition.py"}, (
        "the set of ToolExecutor sites receiving a policy changed. The wiring is "
        "ADR-0058's and the two other sites are named in this docstring — "
        f"re-read PHASE_M4_WIRING.md before changing it: {sorted(wired)}"
    )


def test_only_the_composition_root_imports_the_policy_package():
    """**The tripwire, inverted.** The seam was empty; it is now exactly one wire.

    It used to assert that *no* runtime module imports `wisp.policy`. The
    composition root now does — that **is** the wiring. The inverse keeps the
    property that made the test useful: **exactly one** runtime module, and it is
    the composition root. A second importer would be a second load site, which
    ADR-0006 forbids.
    """
    importers: dict[str, list[int]] = {}
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
            importers[rel] = hits
    assert set(importers) == {"wisp/composition.py"}, (
        "the policy package's runtime importers changed — ADR-0058 wires it at "
        f"exactly one site (ADR-0006): {sorted(importers)}"
    )


def test_config_reads_the_policy_settings(monkeypatch):
    """**The tripwire, inverted.** The settings exist; the defaults are empty.

    It used to assert that `config.py` mentions "policy" nowhere. It now reads
    the two settings ADR-0058 names — and the property that matters is the
    **default**: with neither env var set, both are empty, so nothing is loaded
    and the runtime is today's.
    """
    monkeypatch.delenv("WISP_POLICY_BUNDLE", raising=False)
    monkeypatch.delenv("WISP_POLICY_PUBKEY", raising=False)

    from wisp.config import WispConfig

    cfg = WispConfig()
    assert (cfg.policy_bundle, cfg.policy_pubkey) == ("", ""), (
        "the policy settings no longer default to empty — an unconfigured runtime "
        f"would try to load a bundle: {cfg.policy_bundle!r}, {cfg.policy_pubkey!r}"
    )

    src = (REPO / "wisp/config.py").read_text(encoding="utf-8")
    assert '"env_var": "WISP_POLICY_BUNDLE"' in src
    assert '"env_var": "WISP_POLICY_PUBKEY"' in src


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
    """The **route's** `app.state.policy_pubkey` is set by tests only.

    **Scoped to `state.policy_pubkey`, not the bare name.** The scan used to match
    `policy_pubkey=` anywhere, which conflates the route's attribute with an
    unrelated, same-named **config setting**: ADR-0058 added
    `WispConfig.policy_pubkey` (`WISP_POLICY_PUBKEY`), and a test passing it as a
    keyword argument made this tripwire fire for the wrong reason — a *different*
    thing from "the route is configured in production". The sibling test above
    already scopes to `state.policy_bundle`; this one now matches it.
    """
    setters = []
    for py in list((REPO / "wisp").rglob("*.py")) + list((REPO / "tests").rglob("*.py")):
        if "__pycache__" in py.parts or py.name == pathlib.Path(__file__).name:
            continue
        text = py.read_text(encoding="utf-8")
        if "state.policy_pubkey =" in text or "state.policy_pubkey=" in text:
            setters.append(str(py.relative_to(REPO)))
    assert setters == ["tests/test_policy_routes.py"], (
        "the route's `app.state.policy_pubkey` is now set outside tests — the "
        f"distribution surface may be configured in production: {setters}"
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
    # **Inverted (ADR-0058).** This used to assert that nothing outside the CLI
    # and tests called the loaders. The composition root now does — that is the
    # wiring. Exactly one runtime caller, and it is ADR-0006's site.
    outside = {c for c in callers if c != "wisp/policy/cli.py" and not c.startswith("tests/")}
    assert outside == {"wisp/composition.py"}, (
        "the runtime callers of the bundle loaders changed — ADR-0058 wires it at "
        f"exactly one site (ADR-0006): {sorted(outside)}"
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
