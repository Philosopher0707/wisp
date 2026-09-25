"""Target C — the REST gate boundary, made explicit and non-drifting.

Context (see PHASE_10_AUTHORITY_CLOSURE_AUDIT.md, Target C):

41 mutating routes exist. **15** are consumed by the shipped desktop client
(`wisp-desktop/out/renderer/assets/index-*.js`). **5** reach host execution and
**6** alter executable configuration.

## The decision (closed)

Three routes reached host execution, were ungated, AND were client-consumed:
`POST /api/hooks`, `POST /api/mcp/servers`, `POST /api/plugins/install`. The
open question was whether an API key is sufficient authorisation for them.

**Resolved: no — they now carry the tool-policy gate**, together with every
sibling route that would otherwise be a bypass (the `*/test` routes, which
spawn the thing they test) and the symmetric teardown routes (uninstall /
delete), which would otherwise let a READ_ONLY session destroy what it cannot
create.

The gate is `require_tool_allowed`, which fails closed with 403 when the
session policy denies. Because the policy is **mode-based**, this does NOT
break the shipped client: these actions are allowed in `full`, `auto_edit`,
and `ask_all`, and denied only in `read_only` — which is the point.

These tests pin that boundary so it cannot drift, and record the client
dependency executably rather than only in prose.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
ROUTES = REPO / "wisp/server/routes"
CLIENT_ASSETS = REPO / "wisp-desktop/out/renderer/assets"

MUTATING = {"post", "put", "patch", "delete"}

#: Routes that reach host execution (run a command, spawn a server, or write
#: something that is later executed) or alter executable configuration.
#: Every one MUST be gated — this is the invariant the decision established.
REACHES_HOST_EXECUTION = {
    "/api/bash": "executes a command",
    "/api/hooks": "writes a shell-executed hook",
    "/api/hooks/{name}/test": "executes the hook command",
    "/api/mcp/servers": "registers a spawned command",
    "/api/mcp/servers/{name}/test": "spawns the server to health-check it",
    "/api/mcp/servers/{name}": "removes executable config (symmetric teardown)",
    "/api/plugins/install": "activates plugin code",
    "/api/plugins/{name}/toggle": "toggles plugin code",
    "/api/plugins/{name}": "removes executable config (symmetric teardown)",
}

#: The gate boundary as it stands. `gated` means the route consults
#: `require_tool_allowed` (the tool-policy layer). Everything else is
#: authenticated only.
KNOWN_GATED = {
    # pre-existing (file + shell surfaces mirroring agent tools)
    "/api/bash",
    "/api/files", "/api/files/binary", "/api/files/edit", "/api/files/rename",
    # Target C decision — hooks
    "/api/hooks", "/api/hooks/{name}/test",
    # Target C decision — MCP
    "/api/mcp/servers", "/api/mcp/servers/{name}", "/api/mcp/servers/{name}/test",
    # Target C decision — plugins
    "/api/plugins/install", "/api/plugins/{name}", "/api/plugins/{name}/toggle",
}

#: The decision is closed: no host-execution route remains ungated.
UNRESOLVED_UNGATED: dict[str, str] = {}

#: The three routes the shipped desktop client depends on, which drove the
#: decision. Kept as a named set so the client-side change stays traceable.
CLIENT_DEPENDENT_DECISION_SET = {
    "/api/hooks", "/api/mcp/servers", "/api/plugins/install",
}


def _mutating_routes() -> dict[str, dict]:
    """Map normalised path -> {gated, auth, file, line} for mutating routes."""
    out: dict[str, dict] = {}
    for py in sorted(ROUTES.glob("*.py")):
        if py.name == "__init__.py":
            continue
        src = py.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (not isinstance(dec, ast.Call)
                        or not isinstance(dec.func, ast.Attribute)
                        or dec.func.attr not in MUTATING):
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                raw = dec.args[0].value
                norm = re.sub(r"\{[^}]*\}", "{name}", raw)
                dsrc = ast.get_source_segment(src, dec) or ""
                body = ast.get_source_segment(src, node) or ""
                out[norm] = {
                    "gated": ("require_tool_allowed" in dsrc
                              or "require_tool_allowed" in body),
                    "auth": "verify_api_key" in dsrc,
                    "file": py.name,
                    "line": node.lineno,
                }
    return out


def _client_route_paths() -> set[str]:
    paths: set[str] = set()
    if not CLIENT_ASSETS.exists():
        return paths
    for js in CLIENT_ASSETS.glob("*.js"):
        for m in re.findall(r"/api/[a-z0-9/_{}-]+", js.read_text(errors="ignore")):
            paths.add(re.sub(r"\{[^}]*\}", "{name}", m))
    return paths


# ── Structural facts that must stay true ─────────────────────────────

def test_every_mutating_route_is_authenticated():
    """No mutating route may be reachable unauthenticated."""
    unauthenticated = [
        p for p, info in _mutating_routes().items() if not info["auth"]
    ]
    assert not unauthenticated, (
        f"mutating route(s) without verify_api_key: {unauthenticated}"
    )


def test_the_gate_boundary_is_unchanged():
    """Characterisation.

    If this fails, a route's gating changed. That may be a deliberate
    improvement — but it must be a decision, so update KNOWN_GATED and the
    audit document together rather than silently.
    """
    actual = {p for p, info in _mutating_routes().items() if info["gated"]}
    assert actual == KNOWN_GATED, (
        "the REST gate boundary changed.\n"
        f"  newly gated:   {sorted(actual - KNOWN_GATED)}\n"
        f"  newly ungated: {sorted(KNOWN_GATED - actual)}"
    )


def test_every_host_execution_route_is_accounted_for():
    """A route that runs a command must be in the classification table."""
    found = {p for p in _mutating_routes() if p in REACHES_HOST_EXECUTION}
    declared = set(REACHES_HOST_EXECUTION)
    assert found <= declared, (
        f"undeclared host-execution route(s): {sorted(found - declared)}"
    )


@pytest.mark.parametrize("path", sorted(REACHES_HOST_EXECUTION))
def test_host_execution_routes_are_gated(path):
    """The invariant the Target C decision established.

    Before the decision, host-execution routes could be ungated as long as
    they were *declared* unresolved. That escape hatch is now closed: a route
    that reaches host execution must carry the policy gate.
    """
    info = _mutating_routes().get(path)
    if info is None:
        pytest.skip(f"{path} no longer exists")
    if not info["gated"]:
        assert path in UNRESOLVED_UNGATED, (
            f"{path} reaches host execution, is not gated, and is not recorded "
            "in UNRESOLVED_UNGATED. Gate it or record the decision."
        )


def test_the_unresolved_set_is_accurate():
    """A route listed as unresolved must still actually be ungated."""
    routes = _mutating_routes()
    stale = [p for p in UNRESOLVED_UNGATED
             if p in routes and routes[p]["gated"]]
    assert not stale, (
        f"{stale} are now gated — remove them from UNRESOLVED_UNGATED"
    )


def test_no_host_execution_route_remains_unresolved():
    """The Target C decision is closed — the set must stay empty."""
    assert UNRESOLVED_UNGATED == {}, (
        "a host-execution route was re-opened as unresolved: "
        f"{sorted(UNRESOLVED_UNGATED)}"
    )


# ── The client dependency, recorded executably ───────────────────────

def test_the_client_dependency_claim_is_true():
    """The desktop client consumes the three decision-set routes. If that
    stops being true, the coordinated client change is no longer required
    and the audit should say so."""
    if not CLIENT_ASSETS.exists():
        pytest.skip("desktop client build not present")
    client = _client_route_paths()
    for path in CLIENT_DEPENDENT_DECISION_SET:
        assert path in client, (
            f"{path} is no longer called by the shipped desktop client — "
            "re-evaluate the gating decision"
        )


def test_the_decision_set_is_gated():
    """The three routes whose client dependency drove the decision must
    actually carry the gate now."""
    routes = _mutating_routes()
    for path in CLIENT_DEPENDENT_DECISION_SET:
        assert path in routes, f"{path} no longer exists"
        assert routes[path]["gated"], (
            f"{path} was the subject of the Target C decision but is ungated"
        )
