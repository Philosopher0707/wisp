"""Dependency lock + license audit (M7, stdlib only).

Lock semantics: `generate_lock()` pins declared deps to installed
versions (→ requirements.lock); `verify_lock()` reports missing or
out-of-range installs. Metadata accessors are injectable so tests are
hermetic (no dependence on the ambient environment).
"""
from __future__ import annotations
import re
from collections.abc import Callable
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def declared_deps(pyproject: Path | None = None) -> list[tuple[str, str]]:
    """Parse `dependencies` into (name, specifier) pairs."""
    import re as _re
    text = (pyproject or _project_root() / "pyproject.toml").read_text()
    # Closing bracket sits alone on its line; naive split("]") would stop
    # inside extras like uvicorn[standard].
    block = text.split("dependencies = [", 1)[1].split("\n]", 1)[0]
    out = []
    for raw in block.split(","):
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            continue
        m = _re.match(r"([A-Za-z0-9_.\-]+)\s*(.*)", raw)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def _real_version(name: str) -> str:
    from importlib.metadata import version
    return version(name)


def _real_license(name: str) -> str:
    from importlib import metadata
    try:
        return (metadata.metadata(_dist_name(name)).get("License", "") or "").strip()
    except Exception:
        return ""


def _dist_name(name: str) -> str:
    return name


def generate_lock(deps: list[tuple[str, str]] | None = None,
                  get_version: Callable[[str], str] = _real_version) -> list[str]:
    """Pin each declared dep to its installed version."""
    lines = []
    for name, _spec in (deps if deps is not None else declared_deps()):
        try:
            lines.append(f"{name}=={get_version(name)}")
        except Exception:
            lines.append(f"# {name}: not installed")
    return lines


def _parse_version(v: str) -> tuple:
    parts: list = []
    for p in re.split(r"[.\-]", v.strip()):
        parts.append(int(p) if p.isdigit() else p)
    return tuple(parts)


def _cmp(installed: str, op: str, want: str) -> bool:
    a, b = _parse_version(installed), _parse_version(want)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "~=":
        return a >= b and a[: len(b) - 1] == b[: len(b) - 1]
    return False


def _satisfies(installed: str, spec: str) -> bool:
    """Minimal stdlib version check for ==, !=, >=, <=, >, <, ~= (comma=AND)."""
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        m = re.match(r"(>=|<=|==|~=|>|<|!=)\s*(.+)", clause)
        if not m or not _cmp(installed, m.group(1), m.group(2)):
            return False
    return True


def verify_lock(deps: list[tuple[str, str]] | None = None,
                get_version: Callable[[str], str] = _real_version) -> list[str]:
    """Return human-readable problems (empty = lock satisfied)."""
    problems = []
    for name, spec in (deps if deps is not None else declared_deps()):
        try:
            installed = get_version(name)
        except Exception:
            problems.append(f"{name}: missing (wanted {spec or 'any'})")
            continue
        if spec and not _satisfies(installed, spec):
            problems.append(f"{name}: installed {installed} violates {spec}")
    return problems


def audit_licenses(names: list[str], allowed: tuple[str, ...] = ("MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC", "PSF-2.0"),
                   get_license: Callable[[str], str] = _real_license) -> list[tuple[str, str]]:
    """Return [(name, license)] for deps outside the allowlist."""
    bad = []
    for name in names:
        try:
            lic = get_license(name)
        except Exception:
            lic = ""
        if lic not in allowed:
            bad.append((name, lic or "UNKNOWN"))
    return bad
