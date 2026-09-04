# tests/test_release_lock.py — lockfile gen/verify + license audit (M7 T1).
from wisp.release.lock import (
    audit_licenses,
    declared_deps,
    generate_lock,
    verify_lock,
)


def _meta(versions, licenses=None):
    licenses = licenses or {}
    def get_version(name):
        if name not in versions:
            raise KeyError(name)
        return versions[name]
    def get_license(name):
        return licenses.get(name, "MIT")
    return get_version, get_license


def test_declared_deps_include_crypto():
    deps = declared_deps()
    names = [d[0].lower() for d in deps]
    assert "cryptography" in names and "pydantic" in names


def test_generate_lock_pins_versions():
    get_version, _ = _meta({"requests": "2.31.0", "pydantic": "2.5.0"})
    lines = generate_lock([("requests", ">=2.28"), ("pydantic", ">=2.0")],
                          get_version=get_version)
    assert "requests==2.31.0" in lines and "pydantic==2.5.0" in lines


def test_verify_lock_ok_and_mismatch():
    get_version, _ = _meta({"requests": "2.31.0"})
    assert verify_lock([("requests", ">=2.28")], get_version=get_version) == []
    problems = verify_lock([("requests", ">=9.9")], get_version=get_version)
    assert len(problems) == 1 and "2.31.0" in problems[0]


def test_verify_lock_missing():
    get_version, _ = _meta({})
    problems = verify_lock([("requests", ">=2.28")], get_version=get_version)
    assert len(problems) == 1 and "missing" in problems[0].lower()


def test_audit_licenses():
    _, get_license = _meta({}, {"a": "MIT", "b": "GPL-3.0", "c": "UNKNOWN"})
    bad = audit_licenses(["a", "b", "c"], allowed=("MIT", "Apache-2.0"),
                         get_license=get_license)
    assert [b[0] for b in bad] == ["b", "c"]
