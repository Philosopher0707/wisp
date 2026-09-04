# tests/test_release_sbom.py — CycloneDX-lite generation (M7 T1).
from wisp.release.sbom import audit_component_licenses, generate_sbom


def _meta():
    versions = {"requests": "2.31.0", "pydantic": "2.5.0"}
    licenses = {"requests": "Apache-2.0", "pydantic": "MIT"}
    def get_version(name):
        return versions[name]
    def get_license(name):
        return licenses.get(name, "")
    def get_home(name):
        return {"requests": "https://requests.readthedocs.io"} .get(name, "")
    return get_version, get_license, get_home


def test_sbom_shape():
    get_version, get_license, get_home = _meta()
    sbom = generate_sbom("0.1.0", [("requests", ">=2.28"), ("pydantic", ">=2.0")],
                         get_version=get_version, get_license=get_license,
                         get_home=get_home)
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    assert sbom["metadata"]["component"]["version"] == "0.1.0"
    assert sbom["serialNumber"].startswith("urn:uuid:")
    names = {c["name"]: c["version"] for c in sbom["components"]}
    assert names == {"requests": "2.31.0", "pydantic": "2.5.0"}
    assert sbom["components"][0]["licenses"]


def test_sbom_skips_missing():
    get_version, get_license, get_home = _meta()
    sbom = generate_sbom("0.1.0", [("nope", ">=1")],
                         get_version=get_version, get_license=get_license,
                         get_home=get_home)
    assert sbom["components"] == []


def test_component_license_audit():
    get_version, get_license, get_home = _meta()
    sbom = generate_sbom("0.1.0", [("requests", ">=2.28")],
                         get_version=get_version, get_license=get_license,
                         get_home=get_home)
    assert audit_component_licenses(sbom, allowed=("Apache-2.0",)) == []
    assert audit_component_licenses(sbom, allowed=("MIT",)) == ["requests"]
