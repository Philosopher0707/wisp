"""One authority for the protected-path guard (Phase 10).

`.wisp/hooks` holds commands that Wisp executes itself, before later tool
calls, with the full process environment. Writing there is a
privilege-escalation primitive: an agent that can write a hook can arrange
arbitrary code execution without a further approval. The guard that refuses
this is the mitigation `docs/THREAT-MODEL.md` names for "malicious hook
persistence".

Before this file existed the guard had **four** implementations and the REST
surface had **none**:

| Path | Implementation | Covered |
|---|---|---|
| agent tools | `auth/decision.py` L4 inline substring | yes (but see below) |
| shell commands | `tools/_utils` regex verb scan | yes |
| filesystem tools | `tools/_utils._is_hook_controlled_path` | yes |
| REST (`/api/files`, `/api/bash`, …) | — | **no** |

Verified gaps this file now pins shut:

1. **REST bypass.** `POST /api/files` (and `/edit`, `/binary`, `/rename`,
   `DELETE`) wrote `.wisp/hooks/pwn.json` in `full`/`auto_edit` while the
   equivalent `write_file` *tool call* was refused with "hook-directory
   mutation refused". The REST gate used `SecurityPolicy`, which is
   mode-based and has no argument scan.
2. **`new_path` unscanned.** Renaming `normal.txt` → `.wisp/hooks/x.json`
   was allowed on the agent path: only `path` was checked.
3. **Boundary false positive.** `.wisp/hooksfoo/x.json` was refused, because
   the inline check was a bare substring test.

The predicate now lives in `wisp/pathsec.py`; every path consults it.
"""

from __future__ import annotations

import ast
import base64
import pathlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The one module allowed to name the protected fragment for decision purposes.
GUARD_AUTHORITY = "wisp/pathsec.py"

#: Modules that legitimately mention the fragment for a *different* reason:
#: building the path, scanning a shell command, naming the guard in a
#: refusal message, or a user-facing message.
FRAGMENT_ALLOWLIST = {
    "wisp/pathsec.py",              # the authority
    "wisp/tools/_utils.py",         # shell-verb scan (different mechanism)
    "wisp/server/routes/hooks.py",  # constructs the path it writes
    "wisp/infra/hook_types.py",     # constructs the path it reads
    "wisp/cli/dispatcher.py",       # a help message
    "wisp/server/deps.py",          # names the guard in its 403 detail
}

#: Modules that must NOT re-derive the decision.
DECISION_MODULES = ("wisp/auth", "wisp/server/deps.py")


# ── The predicate's semantics ────────────────────────────────────────

def test_protected_paths_are_detected():
    from wisp.pathsec import is_protected_path

    for candidate in (".wisp/hooks/x.json", ".wisp/hooks",
                      ".wisp/hooks/sub/dir/x.json", "a/.wisp/hooks/x.json",
                      ".wisp\\hooks\\x.json"):  # Windows separators
        assert is_protected_path(candidate), f"{candidate!r} should be protected"


def test_look_alikes_are_not_protected():
    """The fragment must end the path or be followed by a separator."""
    from wisp.pathsec import is_protected_path

    for candidate in (".wisp/hooksfoo/x.json", ".wisp/hooks_backup",
                      "src/hooks/x.json", "hooks/x.json", "normal.txt", ""):
        assert not is_protected_path(candidate), f"{candidate!r} should be allowed"


def test_predicate_is_total():
    """It answers, never raises — callers treat 'cannot tell' as unprotected."""
    from wisp.pathsec import is_protected_path

    for value in (None, "", 0, [], {}):
        assert is_protected_path(value) is False


def test_fragment_list_has_one_definition():
    """A second copy of the fragment list is how the divergence started."""
    definers = []
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in ("PROTECTED_PATH_FRAGMENTS",
                                      "_SENSITIVE_HOOK_DIR_FRAGMENTS"):
                    definers.append(str(py.relative_to(REPO)))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in (
                            "PROTECTED_PATH_FRAGMENTS",
                            "_SENSITIVE_HOOK_DIR_FRAGMENTS"):
                        definers.append(str(py.relative_to(REPO)))
    # `_utils` aliases the canonical tuple rather than rebuilding it.
    assert definers == [GUARD_AUTHORITY, "wisp/tools/_utils.py"], (
        f"the protected-fragment list must be defined once; found {definers}"
    )


def test_utils_alias_is_the_canonical_object():
    """The tools-layer name must *be* the canonical set, not a copy."""
    from wisp.pathsec import PROTECTED_PATH_FRAGMENTS
    from wisp.tools._utils import _SENSITIVE_HOOK_DIR_FRAGMENTS

    assert _SENSITIVE_HOOK_DIR_FRAGMENTS is PROTECTED_PATH_FRAGMENTS


def test_utils_predicate_delegates():
    from wisp.pathsec import is_protected_path
    from wisp.tools._utils import _is_hook_controlled_path

    for candidate in (".wisp/hooks/x.json", ".wisp/hooksfoo/x.json",
                      "normal.txt", ".wisp\\hooks\\x.json"):
        assert _is_hook_controlled_path(candidate) == is_protected_path(candidate)


# ── The decision has one authority ───────────────────────────────────

def _mentions_fragment(py: pathlib.Path) -> list[int]:
    """Line numbers where the protected fragment appears as a string literal."""
    hits: list[int] = []
    tree = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if ".wisp/hooks" in node.value or ".wisp\\hooks" in node.value:
                hits.append(node.lineno)
    return hits


def _compares_against_fragment(py: pathlib.Path) -> list[int]:
    """Line numbers of comparisons whose operand is the fragment literal.

    This is the precise signature of a re-derived guard — the old bug was
    literally `if ".wisp/hooks" in target`. A fragment inside a docstring or
    a refusal message is not a comparison and is not flagged.
    """
    hits: list[int] = []
    tree = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if (isinstance(operand, ast.Constant)
                    and isinstance(operand.value, str)
                    and (".wisp/hooks" in operand.value
                         or ".wisp\\hooks" in operand.value)):
                hits.append(node.lineno)
    return hits


def test_authorization_modules_do_not_compare_against_the_fragment():
    """`auth/` and the REST gate must ask the predicate, not match strings.

    This is the invariant that stops a second guard appearing: the decision
    lives in `wisp/pathsec`, and these modules consume it.
    """
    offenders: dict[str, list[int]] = {}
    for target in DECISION_MODULES:
        base = REPO / target
        paths = sorted(base.rglob("*.py")) if base.is_dir() else [base]
        for py in paths:
            if "__pycache__" in py.parts or not py.exists():
                continue
            hits = _compares_against_fragment(py)
            if hits:
                offenders[str(py.relative_to(REPO))] = hits
    assert not offenders, (
        "an authorization module compares against the protected fragment "
        f"instead of consulting wisp.pathsec.is_protected_path: {offenders}"
    )


def test_no_unexpected_module_mentions_the_fragment():
    """Ratchets the allowlist: a new mention is a deliberate decision."""
    found = set()
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if _mentions_fragment(py):
            found.add(str(py.relative_to(REPO)))
    assert found <= FRAGMENT_ALLOWLIST, (
        "a module newly references the protected fragment — either route it "
        f"through wisp.pathsec or add it to the allowlist with a reason: "
        f"{sorted(found - FRAGMENT_ALLOWLIST)}"
    )


# ── Agent path ───────────────────────────────────────────────────────

def _agent_allows(tool: str, args: dict, tmp_path) -> bool:
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    principal = local_principal(workspace=str(tmp_path), profile="local")
    return authorize(principal, tool, args,
                     workspace_trust=WorkspaceTrust.TRUSTED,
                     permission_mode="full").allowed


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "delete_file"])
def test_agent_path_refuses_hook_dir_mutation(tool, tmp_path):
    assert not _agent_allows(tool, {"path": ".wisp/hooks/x.json"}, tmp_path)


def test_agent_path_refuses_rename_into_the_hook_dir(tmp_path):
    """Gap 2 — only `path` used to be scanned, so the destination escaped."""
    assert not _agent_allows(
        "edit_file",
        {"path": "normal.txt", "op": "rename", "new_path": ".wisp/hooks/x.json"},
        tmp_path,
    )


def test_agent_path_refuses_when_only_the_destination_is_set(tmp_path):
    assert not _agent_allows(
        "edit_file", {"new_path": ".wisp/hooks/x.json", "op": "rename"}, tmp_path
    )


def test_agent_path_allows_the_look_alike(tmp_path):
    """Gap 3 — this was a false positive before the boundary check."""
    assert _agent_allows("write_file", {"path": ".wisp/hooksfoo/x.json"}, tmp_path)


def test_agent_path_allows_reads_of_the_hook_dir(tmp_path):
    """The guard is about mutation; reading hooks is legitimate."""
    assert _agent_allows("read_file", {"path": ".wisp/hooks/x.json"}, tmp_path)


# ── REST path (functional, through the real routes) ──────────────────

@pytest.fixture()
def files_client(tmp_path, monkeypatch):
    import wisp.server.routes.files as files_mod

    monkeypatch.setattr(files_mod, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / ".wisp" / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".wisp" / "hooks" / "pwn.json").write_text("ORIGINAL")

    app = FastAPI()
    app.include_router(files_mod.router)
    app.state.root = SimpleNamespace(
        config=SimpleNamespace(permission_mode="full"))
    return TestClient(app), tmp_path


def test_rest_write_to_hook_dir_is_refused(files_client):
    """Gap 1 — this returned 200 and wrote the hook."""
    client, tmp_path = files_client
    r = client.post("/api/files", params={"path": ".wisp/hooks/pwn.json"},
                    json={"content": "OWNED"})
    assert r.status_code == 403
    assert (tmp_path / ".wisp" / "hooks" / "pwn.json").read_text() == "ORIGINAL"


def test_rest_edit_to_hook_dir_is_refused(files_client):
    client, tmp_path = files_client
    r = client.post("/api/files/edit", params={"path": ".wisp/hooks/pwn.json"},
                    json={"old_text": "ORIGINAL", "new_text": "OWNED"})
    assert r.status_code == 403
    assert (tmp_path / ".wisp" / "hooks" / "pwn.json").read_text() == "ORIGINAL"


def test_rest_binary_write_to_hook_dir_is_refused(files_client):
    client, tmp_path = files_client
    r = client.post("/api/files/binary", params={"path": ".wisp/hooks/pwn.json"},
                    json={"content_base64": base64.b64encode(b"OWNED").decode()})
    assert r.status_code == 403
    assert (tmp_path / ".wisp" / "hooks" / "pwn.json").read_bytes() == b"ORIGINAL"


def test_rest_delete_of_hook_is_refused(files_client):
    client, tmp_path = files_client
    r = client.delete("/api/files", params={"path": ".wisp/hooks/pwn.json"})
    assert r.status_code == 403
    assert (tmp_path / ".wisp" / "hooks" / "pwn.json").exists()


def test_rest_rename_into_the_hook_dir_is_refused(files_client):
    client, tmp_path = files_client
    (tmp_path / "normal.txt").write_text("hi")
    r = client.post("/api/files/rename", params={"path": "normal.txt"},
                    json={"new_path": ".wisp/hooks/injected.json"})
    assert r.status_code == 403
    assert not (tmp_path / ".wisp" / "hooks" / "injected.json").exists()


def test_rest_ordinary_writes_still_work(files_client):
    """The guard must not become a blanket block on file writes."""
    client, tmp_path = files_client
    r = client.post("/api/files", params={"path": "src/ok.txt"},
                    json={"content": "fine"})
    assert r.status_code == 200
    assert (tmp_path / "src" / "ok.txt").read_text() == "fine"


def test_rest_look_alike_is_allowed(files_client):
    client, tmp_path = files_client
    r = client.post("/api/files", params={"path": ".wisp/hooksfoo/ok.txt"},
                    json={"content": "fine"})
    assert r.status_code == 200


def test_rest_refusal_names_the_guard(files_client):
    """The operator must be able to tell this apart from a mode denial."""
    client, _ = files_client
    r = client.post("/api/files", params={"path": ".wisp/hooks/x.json"},
                    json={"content": "x"})
    assert r.status_code == 403
    assert "protected-path guard" in r.json()["detail"]


def test_guard_applies_even_in_full_mode(files_client):
    """`full` relaxes approval, not the escalation guard."""
    client, _ = files_client
    assert client.post("/api/files", params={"path": ".wisp/hooks/x.json"},
                       json={"content": "x"}).status_code == 403


def test_bash_route_still_refuses_hook_writes(tmp_path, monkeypatch):
    """The shell path has its own verb scan; it must keep working.

    The canonical guard now fires first (403) where the verb scan used to
    (400) — both refuse, and the write never happens.
    """
    import wisp.server.routes.bash as bash_mod

    monkeypatch.setattr(bash_mod, "WORKSPACE_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(bash_mod.router)
    app.state.root = SimpleNamespace(
        config=SimpleNamespace(permission_mode="full"))
    client = TestClient(app)

    for command in ("touch .wisp/hooks/pwn.json",
                    "echo hi > .wisp/hooks/pwn.json",
                    "cp /dev/null .wisp/hooks/pwn.json"):
        r = client.post("/api/bash", json={"command": command})
        assert r.status_code in (400, 403), f"{command!r} was not refused"
    assert not (tmp_path / ".wisp" / "hooks" / "pwn.json").exists()


def test_rest_bash_is_deliberately_stricter_than_the_agent_bash(tmp_path, monkeypatch):
    """Documented delta: REST refuses *any* hook-dir command, including a read.

    The agent's bash path distinguishes read from write with a verb scan, so
    `cat .wisp/hooks/x.json` is allowed there. REST has no approver, so its
    guard is unconditional — and a caller wanting to inspect hooks has
    `GET /api/hooks` for that. Pinned so the difference is a decision, not an
    accident.
    """
    import wisp.server.routes.bash as bash_mod

    monkeypatch.setattr(bash_mod, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / ".wisp" / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".wisp" / "hooks" / "x.json").write_text("{}")

    app = FastAPI()
    app.include_router(bash_mod.router)
    app.state.root = SimpleNamespace(
        config=SimpleNamespace(permission_mode="full"))
    client = TestClient(app)

    r = client.post("/api/bash", json={"command": "cat .wisp/hooks/x.json"})
    assert r.status_code == 403
