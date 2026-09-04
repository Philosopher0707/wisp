"""SBOM generation (M7, stdlib only): CycloneDX 1.5-lite JSON from
installed distribution metadata. Signed artifacts + provenance
attestations are CI follow-ups (RELEASE.md); this module produces the
unsigned inventory they will sign.
"""
from __future__ import annotations
import uuid
from collections.abc import Callable
from typing import Any

from wisp.release.lock import _real_license, _real_version


def _real_home(name: str) -> str:
    from importlib import metadata
    try:
        return (metadata.metadata(name).get("Home-page", "") or "").strip()
    except Exception:
        return ""


def generate_sbom(wisp_version: str,
                  deps: list[tuple[str, str]] | None = None,
                  get_version: Callable[[str], str] = _real_version,
                  get_license: Callable[[str], str] = _real_license,
                  get_home: Callable[[str], str] = _real_home) -> dict[str, Any]:
    """Build a CycloneDX 1.5 document for the declared dependencies."""
    if deps is None:
        from wisp.release.lock import declared_deps
        deps = declared_deps()
    components = []
    for name, _spec in deps:
        try:
            version = get_version(name)
        except Exception:
            continue  # not installed → absent from inventory, not an error
        comp: dict[str, Any] = {"type": "library", "name": name,
                                "version": version}
        try:
            lic = get_license(name)
        except Exception:
            lic = ""
        if lic:
            comp["licenses"] = [{"license": {"name": lic}}]
        try:
            home = get_home(name)
        except Exception:
            home = ""
        if home:
            comp["externalReferences"] = [
                {"type": "website", "url": home}]
        components.append(comp)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {"component": {"type": "application",
                                   "name": "wisp",
                                   "version": wisp_version}},
        "components": components,
    }


def audit_component_licenses(sbom: dict[str, Any],
                             allowed: tuple[str, ...]) -> list[str]:
    """Component names whose license is missing or outside the allowlist."""
    bad = []
    for comp in sbom.get("components", []):
        licenses = comp.get("licenses", [])
        name = licenses[0]["license"]["name"] if licenses else ""
        if name not in allowed:
            bad.append(comp["name"])
    return bad
