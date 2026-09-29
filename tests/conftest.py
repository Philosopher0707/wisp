import os

import pytest
import tempfile
import shutil
from pathlib import Path

# ── Deterministic ANSI palette for the whole test process ────────────────
# Rich's Console sniffs COLORTERM/TERM, and wisp.diff_renderer renders
# through module-level Style singletons whose ANSI codes Rich memoizes on
# FIRST render (Style._make_ansi_codes). If any test renders before the
# golden transcripts do, the ambient terminal's palette (e.g. truecolor)
# gets frozen and byte-compared goldens drift. Pin the canonical standard
# (8-color) palette before rich is ever imported, so the memoized codes
# match the checked-in goldens on every machine.
os.environ.pop("COLORTERM", None)
os.environ.pop("FORCE_COLOR", None)
os.environ.setdefault("TERM", "xterm")



@pytest.fixture
def isolated_wisp_env(monkeypatch, tmp_path):
    """Strip provider env vars and hide user config/home state.

    Machine setups (e.g. WISP_PROVIDER=nvidia plus
    ~/.config/wisp/config.json) leak into tests that assert *default*
    behavior, making them fail on one machine and pass on another. Provider
    API-key and endpoint variables are included because provider selection
    resolves them outside the WISP_* namespace.
    Opt in where hermeticity matters:

        def test_defaults_are_sane(self, isolated_wisp_env):
            cfg = WispConfig()   # pure defaults, no env/config influence
    """
    import os

    import wisp.config as cfg_mod

    provider_env = {
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "NVIDIA_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENROUTER_SITE_URL",
        "OPENROUTER_APP_TITLE",
    }
    for var in [k for k in os.environ
                if k.startswith("WISP_") or k in provider_env]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        cfg_mod, "get_config_path", lambda: tmp_path / "missing-config.json"
    )
    return monkeypatch


@pytest.fixture(scope="session", autouse=True)
def _isolate_workspace_trust_file():
    """Point the workspace-trust file at a throwaway for the whole session.

    `WorkspaceTrustManager.TRUST_FILE` is computed from `Path.home()` at import
    time, so patching HOME later never redirected it. Every test that called
    `trust_workspace(tmp_path)` without `trust_file=` appended its temp dir to
    the developer's real `~/.config/wisp/trusted_workspaces.json` (about 1,500
    entries accumulated). Live E2E runs keep the ambient file.
    """
    if os.environ.get("WISP_E2E_LIVE") == "1":
        yield
        return
    from wisp.trust import WorkspaceTrustManager

    scratch = Path(tempfile.mkdtemp(prefix="wisp-trust-"))
    saved = WorkspaceTrustManager.TRUST_FILE
    WorkspaceTrustManager.TRUST_FILE = scratch / "trusted_workspaces.json"
    try:
        yield
    finally:
        WorkspaceTrustManager.TRUST_FILE = saved
        shutil.rmtree(scratch, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _neutralize_server_auth():
    """Force server dev-mode (no auth) for the unit-test session.

    wisp.server.deps builds its _AuthConfig singleton at import time from
    the ambient WISP_API_KEY. A set key silently flips every server route
    test to 401 (seen live: 14 red tests, all environmental). Live E2E
    runs (WISP_E2E_LIVE=1) keep ambient credentials.
    """
    if os.environ.get("WISP_E2E_LIVE") == "1":
        yield
        return
    import wisp.server.deps as deps_mod

    auth = deps_mod._auth
    saved_key, saved_no_auth = auth._key, auth._no_auth
    saved_valid = dict(auth._valid_keys)
    saved_env = os.environ.pop("WISP_API_KEY", None)
    auth._key = ""
    auth._no_auth = True
    auth._valid_keys = {}
    try:
        yield
    finally:
        auth._key, auth._no_auth = saved_key, saved_no_auth
        auth._valid_keys = saved_valid
        if saved_env is not None:
            os.environ["WISP_API_KEY"] = saved_env


@pytest.fixture(scope="session", autouse=True)
def _neutralize_server_rate_limit():
    """Give the route tests their own rate-limit budget.

    `RATE_LIMITER` is a process-external singleton: a SQLite file at
    `~/.config/wisp/rate_limits.db`, 30 requests per 60 s, keyed by client IP.
    Route tests therefore shared one budget with each other *and with previous
    runs* — once a run made 30 requests to gated routes inside a minute, every
    later route test received 429 instead of the verdict it was asserting.

    Seen live: six tests across `test_server_policy_gate.py` and
    `test_protected_path_guard.py` failed with `429 Too Many Requests` only when
    run together, and stayed red across repeated runs because the budget lives
    outside the process. That is a test-isolation defect, not a product one.

    The limiter's own behaviour is covered directly by
    `tests/test_server_deps.py`, which constructs `SQLiteRateLimiter` instances
    against a `tmp_path` and is unaffected by this patch. Live E2E runs keep
    ambient behaviour.
    """
    if os.environ.get("WISP_E2E_LIVE") == "1":
        yield
        return
    import wisp.server.deps as deps_mod

    class _Unlimited:
        async def __call__(self, request):
            return None

    saved = deps_mod.get_rate_limiter
    deps_mod.get_rate_limiter = lambda: _Unlimited()
    try:
        yield
    finally:
        deps_mod.get_rate_limiter = saved


@pytest.fixture
def auto_edit_hard_deny_witness(monkeypatch):
    """Re-impose an AUTO_EDIT hard deny on the four git/gh writes, as a test witness.

    The 13F.1 guarantee — *a hard DENY is never prompted, and no approval can run it* — is a
    property of the mechanism, and `git_push` was its witness. On 2026-09-28 the default AUTO_EDIT
    deny set became empty (the git/gh writes ask the operator each time instead), so the mechanism
    has no default member to be observed through. This fixture restores the pre-change
    classification for the duration of one test, at every place that reads it:

    * `policy_engine._AUTO_EDIT_DENY_TOOLS` — read at call time by `auth/decision.py`;
    * `policy_engine._AUTO_EDIT_APPROVAL_TOOLS` — read when a policy engine is built;
    * `security._AUTO_EDIT_DENY_TOOLS` — the name `policy_hard_deny` (the executor's gate) reads.

    Build any `SecurityPolicy` / `ToolExecutor` inside the test, after this fixture applies.
    """
    import wisp.infra.policy_engine as pe
    import wisp.infra.security as sec

    deny = frozenset({"git_branch", "git_commit", "git_push", "gh_pr_create"})
    monkeypatch.setattr(pe, "_AUTO_EDIT_DENY_TOOLS", deny)
    monkeypatch.setattr(pe, "_AUTO_EDIT_APPROVAL_TOOLS",
                        frozenset({"run_bash", "spawn", "fanout"}))
    monkeypatch.setattr(sec, "_AUTO_EDIT_DENY_TOOLS", deny)
    return deny


@pytest.fixture
def temp_workspace():
    """Provide a temporary workspace directory for file operations."""
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    shutil.rmtree(str(tmp), ignore_errors=True)


@pytest.fixture
def sample_file(temp_workspace):
    """Create a sample file in the workspace."""
    path = temp_workspace / "sample.txt"
    path.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
    return path


@pytest.fixture
def nested_dir(temp_workspace):
    """Create nested directories inside workspace."""
    d = temp_workspace / "a" / "b"
    d.mkdir(parents=True)
    (d / "deep.txt").write_text("deep")
    return d
