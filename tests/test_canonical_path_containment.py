"""Canonical path containment: every call site must agree.

Context (see PHASE_BOUNDARY_FORENSIC.md B.../F3):

`wisp.pathsec.resolve_contained` is the canonical containment primitive and
three call sites already used it. Two others — `server/routes/files._resolve_path`
and `sandbox.resolve_sandbox_cwd` — re-implemented the realpath+prefix
comparison independently and, in doing so, accepted control characters that
`pathsec` deliberately rejects (terminal/log injection through any path that
gets displayed).

Those two now delegate. These tests make the invariant executable: a
differential corpus over every containment entry point, plus a structural pin
so a third re-implementation cannot appear silently.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from pathlib import Path

import pytest

from wisp.pathsec import resolve_contained

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def tree():
    """A workspace root, a sibling with a shared prefix, and an outside dir."""
    tmp = tempfile.mkdtemp(prefix="wisp_contain_test_")
    root = os.path.join(tmp, "ws")
    os.makedirs(root)
    os.makedirs(os.path.join(tmp, "ws2"))          # sibling-prefix collision
    outside = os.path.join(tmp, "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "secret.txt"), "w") as fh:
        fh.write("SECRET")
    os.symlink(outside, os.path.join(root, "link_out"))
    os.symlink(os.path.join(outside, "secret.txt"), os.path.join(root, "link_file"))
    return {"tmp": tmp, "root": root, "outside": outside}


# Security-relevant corpus: every entry point must AGREE on these.
DENY_CASES = [
    "../outside/secret.txt",
    "a/../../outside/secret.txt",
    "../ws2/x.txt",              # sibling-prefix collision
    "/etc/passwd",
    "link_out/secret.txt",       # symlink directory escape
    "link_file",                 # symlink file escape
    "ok\x01.txt",                # control character
    "ok\x00.txt",                # NUL
]

ALLOW_CASES = ["ok.txt", "a/b/c.txt", "."]


def _canonical(root: str, cand: str) -> str:
    try:
        resolve_contained(root, cand)
        return "ALLOW"
    except ValueError:
        return "DENY"


def _utils(root: str, cand: str) -> str:
    from wisp.tools import _utils
    try:
        _utils._resolve_path(cand, workspace=root)
        return "ALLOW"
    except Exception:
        return "DENY"


def _artifacts(root: str, cand: str) -> str:
    from wisp.graph.artifacts import _contain
    try:
        _contain(root, cand)
        return "ALLOW"
    except ValueError:
        return "DENY"


def _workspace(root: str, cand: str) -> str:
    from wisp.workspace import _contain
    try:
        _contain(root, cand)
        return "ALLOW"
    except ValueError:
        return "DENY"


def _server(root: str, cand: str) -> str:
    import wisp.server.routes.files as F
    F.WORKSPACE_ROOT = Path(root)
    try:
        F._resolve_path(cand)
        return "ALLOW"
    except Exception:
        return "DENY"


def _sandbox(root: str, cand: str) -> str:
    from wisp.sandbox import resolve_sandbox_cwd
    return "ALLOW" if resolve_sandbox_cwd(root, cand) is not None else "DENY"


ENTRY_POINTS = {
    "pathsec": _canonical,
    "tools._utils": _utils,
    "graph.artifacts": _artifacts,
    "workspace": _workspace,
    "server.routes.files": _server,
    "sandbox.cwd": _sandbox,
}


# ── The invariant: agreement on every security-relevant input ────────

@pytest.mark.parametrize("cand", DENY_CASES)
def test_all_entry_points_deny(tree, cand):
    """Each entry point must deny independently — a divergence here is the
    exact class of bug this file exists to prevent."""
    results = {name: fn(tree["root"], cand) for name, fn in ENTRY_POINTS.items()}
    allowed = [name for name, r in results.items() if r == "ALLOW"]
    assert not allowed, (
        f"{cand!r} was ALLOWED by {allowed}; every containment entry point "
        f"must deny it. Full results: {results}"
    )


@pytest.mark.parametrize("cand", ALLOW_CASES)
def test_all_entry_points_allow_legitimate_paths(tree, cand):
    """Tightening must not break ordinary paths."""
    results = {name: fn(tree["root"], cand) for name, fn in ENTRY_POINTS.items()}
    denied = [name for name, r in results.items() if r == "DENY"]
    assert not denied, (
        f"{cand!r} was DENIED by {denied}; containment must not reject "
        f"legitimate in-workspace paths. Full results: {results}"
    )


def test_control_characters_are_rejected_everywhere(tree):
    """Regression pin for F3: two implementations used to accept these."""
    for cand in ("ok\x01.txt", "ok\x1f.txt", "ok\x7f.txt"):
        for name, fn in ENTRY_POINTS.items():
            assert fn(tree["root"], cand) == "DENY", (
                f"{name} accepted control character {cand!r}"
            )


def test_symlink_escapes_are_rejected_everywhere(tree):
    for cand in ("link_out/secret.txt", "link_file"):
        for name, fn in ENTRY_POINTS.items():
            assert fn(tree["root"], cand) == "DENY", (
                f"{name} accepted symlink escape {cand!r}"
            )


def test_root_itself_is_allowed(tree):
    assert _canonical(tree["root"], ".") == "ALLOW"


# ── Structural: no third re-implementation may appear ────────────────

_CONTAINMENT_ALLOWLIST = {
    "wisp/pathsec.py",                 # the canonical authority
    "wisp/server/routes/files.py",     # delegates
    "wisp/sandbox/__init__.py",        # delegates
    "wisp/graph/cli.py",               # delegates (found by this test)
}


def test_no_module_reimplements_realpath_prefix_containment():
    """A local `realpath(...)` + `startswith(...)` prefix comparison is the
    signature of a re-implemented containment check. Only the canonical
    helper may contain one; wrappers delegate.

    Files that legitimately compare realpaths for other reasons are listed
    in the allowlist below with a reason.
    """
    # Legitimate non-containment realpath uses (display, diagnostics, tests).
    unrelated_ok = {
        "wisp/core/doctor.py",          # diagnostics reporting
        "wisp/workspace.py",            # delegates to pathsec
        "wisp/graph/artifacts.py",      # delegates to pathsec
        "wisp/tools/_utils.py",         # delegates to pathsec
    }
    offenders = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(REPO))
        if rel in _CONTAINMENT_ALLOWLIST or rel in unrelated_ok:
            continue
        src = py.read_text(encoding="utf-8")
        if "realpath(" in src and "startswith(" in src and (
                "os.sep" in src or "base + " in src):
            offenders.append(rel)
    assert not offenders, (
        "possible re-implemented containment detected in "
        f"{offenders}; containment semantics belong in wisp.pathsec"
    )
