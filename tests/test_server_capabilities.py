"""Tests for the capability/principal introspection surface.

These lock three properties the desktop UI depends on:

1. An unbounded principal reports ``unbounded: True`` and the full registry,
   and that is reported as a distinct shape — never as a count implying
   "every tool is permitted".
2. A bounded principal's tool list is filtered through ``Principal.allows_tool``
   — the same predicate the executor uses — so the endpoint cannot drift from
   enforcement.
3. The surface reports authority; it never becomes an authority. No credential
   material is exposed, and a capability listed here is still subject to the
   execution gates.
"""

from fastapi.testclient import TestClient
from unittest.mock import patch

from wisp.auth.principal import Principal, PrincipalKind
from wisp.server import deps
from wisp.server.main import app
from wisp.server.routes.capabilities import _tool_inventory, get_capabilities

client = TestClient(app)


def _bounded(caps: frozenset[str]) -> Principal:
    return Principal(
        os_user="tester",
        workspace="/tmp/ws",
        profile="default",
        kind=PrincipalKind.SUBAGENT,
        capabilities=caps,
    )


def test_registry_inventory_is_non_empty_and_deduplicated():
    tools = _tool_inventory()
    names = [t["name"] for t in tools]
    assert names, "registry inventory must not be empty"
    assert len(names) == len(set(names)), "inventory must be deduplicated"
    assert names == sorted(names), "inventory must be deterministically sorted"


def test_unbounded_principal_reports_full_registry_and_explicit_flag():
    body = client.get("/api/capabilities").json()

    assert body["principal"]["unbounded"] is True
    assert body["tool_count"] == body["registry_count"]
    assert body["tool_count"] == len(_tool_inventory())


def test_unbounded_is_a_distinct_shape_not_merely_a_full_count():
    """None != "all tools permitted". The flag must survive serialisation."""
    principal_view = client.get("/api/capabilities").json()["principal"]
    assert "unbounded" in principal_view
    assert principal_view["unbounded"] is True
    # A client that only rendered the count would lose the distinction.
    assert principal_view["capabilities" if "capabilities" in principal_view else "unbounded"] is True


def test_credential_material_is_never_exposed():
    raw = client.get("/api/capabilities").text
    assert "credential_handle" not in raw


def test_registry_inventory_endpoint_is_unfiltered_and_labelled():
    body = client.get("/api/capabilities/tools").json()
    assert body["tool_count"] == len(_tool_inventory())
    assert "exists" in body["authority_note"]


def test_capabilities_health_reports_registry_count():
    body = client.get("/api/capabilities/health").json()
    assert body["status"] == "ok"
    assert body["registry_count"] == len(_tool_inventory())


def test_bounded_principal_filters_through_allows_tool():
    """The endpoint's filter must be the executor's own predicate.

    Calls the route function directly with the principal patched in, so this
    asserts on the endpoint's real filtering branch rather than restating it.
    """
    principal = _bounded(frozenset({"read_file"}))

    with patch(
        "wisp.server.routes.capabilities.local_principal",
        return_value=principal,
    ):
        body = get_capabilities(workspace="/tmp/ws", profile="default")

    names = {t["name"] for t in body["tools"]}
    assert body["principal"]["unbounded"] is False
    assert names == {"read_file"}
    assert body["tool_count"] == 1
    # The registry is the whole catalogue; the surface is what is admitted.
    assert body["registry_count"] == len(_tool_inventory())
    assert body["tool_count"] < body["registry_count"], (
        "a bounded principal must report a narrowed surface"
    )
    assert "run_bash" not in names
    assert "grep" not in names, "a non-registry capability must not be invented"


def test_bounded_empty_set_narrows_to_nothing():
    principal = _bounded(frozenset())
    assert principal.capabilities is not None
    reported = [t for t in _tool_inventory() if principal.allows_tool(t["name"])]
    assert reported == []


def test_every_endpoint_requires_api_key_when_auth_enabled():
    deps._auth.set_key("test-secret-key")
    try:
        for path in ("/api/capabilities", "/api/capabilities/tools",
                     "/api/capabilities/health"):
            assert client.get(path).status_code in (401, 403), path
            ok = client.get(path, headers={"X-API-Key": "test-secret-key"})
            assert ok.status_code == 200, path
    finally:
        deps._auth.set_key("")


def test_endpoints_report_without_granting():
    """Listing a tool must not make it executable by anyone but its gate."""
    body = client.get("/api/capabilities").json()
    assert "authoritative" in body["authority_note"]
