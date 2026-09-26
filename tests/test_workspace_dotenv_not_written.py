"""The workspace `.env` is not a configuration source, so wisp does not write one.

`PHASE_WORKSPACE_DOTENV.md` §2 measured the following:

* `provider_select._persist_env` wrote `WISP_PROVIDER`, `WISP_MODEL`, `WISP_API_BASE` and
  `WISP_OLLAMA_URL` to `<workspace>/.env`, and nothing read that file.
* If it had been read, even with the operator's file winning, a cloned repository's
  `WISP_API_BASE` would have sent the operator's key to the repository's endpoint, and its
  `WISP_OLLAMA_URL` would have sent the prompt there.

The decision is that the file is not read, and the writer is removed. This guard holds both
halves:

* **The writer.** `persist()`, and `store_key()` through it, create or modify no file outside
  `~/.config/wisp/`. That covers a fresh workspace, a project's own pre-existing `.env` (kept
  byte-identical and at its own mode), and the cwd fallback.
* **The reader.** F57's `load_user_env` stays the only `.env` reader and is called with no path, so
  it reads the operator's file and nothing else.

**The observation point is the production path** (F96). Each write test runs the real `persist()` in
a subprocess with a private `HOME`, so the operator's real files are never touched.

**The floor** (F81): every write test also asserts that `~/.config/wisp/.env` *was* written, with
the keys passed. A `persist()` that wrote nothing at all would otherwise pass "wrote no workspace
file" vacuously.

**Silent on a legitimate change** (F92): the tests assert what lands on disk, not how
`_persist_env` is spelled.
"""
from __future__ import annotations

import ast
import os
import pathlib
import stat
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

_WRITER_VARS = ("WISP_PROVIDER", "WISP_MODEL", "WISP_API_KEY", "WISP_API_BASE", "WISP_OLLAMA_URL",
                "OPENAI_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "WISP_WORKSPACE")

#: Every config key `_persist_env` maps to a variable, non-secret and secret alike.
_ALL_KEYS = ('{"provider": "openai", "model": "m-1", "api_key": "k-1", '
             '"api_base": "https://b.example/v1", "ollama_url": "http://o.example:11434"}')


def _persist(tmp_path: pathlib.Path, code: str, *, cwd: pathlib.Path,
             workspace_env: pathlib.Path | None = None) -> pathlib.Path:
    """Run `code` in a child with a private HOME; return that HOME."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in _WRITER_VARS and k != "PYTHONPATH"}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO))
    if workspace_env is not None:
        env["WISP_WORKSPACE"] = str(workspace_env)
    proc = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return home


def _user_env_keys(home: pathlib.Path) -> set[str]:
    path = home / ".config" / "wisp" / ".env"
    assert path.exists(), "floor: persist() must still write the operator's own ~/.config/wisp/.env"
    return {ln.split("=", 1)[0] for ln in path.read_text().splitlines() if "=" in ln}


class TestTheWriterDoesNotTouchTheWorkspace:
    def test_a_named_workspace_gets_no_env(self, tmp_path: pathlib.Path) -> None:
        ws, cwd = tmp_path / "ws", tmp_path / "cwd"
        ws.mkdir()
        cwd.mkdir()
        home = _persist(tmp_path, "from wisp.provider_select import persist\n"
                        f"persist({{**{_ALL_KEYS}, 'workspace': {str(ws)!r}}})", cwd=cwd)
        assert not (ws / ".env").exists()
        assert not (cwd / ".env").exists()
        assert {"WISP_PROVIDER", "WISP_MODEL", "WISP_API_BASE", "WISP_OLLAMA_URL",
                "WISP_API_KEY"} <= _user_env_keys(home)

    def test_the_workspace_from_the_environment_gets_no_env(self, tmp_path: pathlib.Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        home = _persist(tmp_path, "from wisp.provider_select import persist\n"
                        "persist({'provider': 'openrouter', 'model': 'x/y'})",
                        cwd=ws, workspace_env=ws)
        assert not (ws / ".env").exists()
        assert {"WISP_PROVIDER", "WISP_MODEL"} <= _user_env_keys(home)

    def test_a_projects_own_env_is_left_byte_identical(self, tmp_path: pathlib.Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        own = project / ".env"
        own.write_bytes(b"DATABASE_URL=postgres://app\nDEBUG=1\n")
        own.chmod(0o644)
        before = own.read_bytes()
        home = _persist(tmp_path, "from wisp.provider_select import persist\n"
                        f"persist({{'provider': 'openrouter', 'model': 'x/y', "
                        f"'workspace': {str(project)!r}}})", cwd=project)
        assert own.read_bytes() == before
        assert stat.S_IMODE(own.stat().st_mode) == 0o644
        assert {"WISP_PROVIDER", "WISP_MODEL"} <= _user_env_keys(home)

    def test_store_key_writes_only_the_operators_file(self, tmp_path: pathlib.Path) -> None:
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        home = _persist(tmp_path, "from wisp.provider_select import store_key\n"
                        "store_key('openrouter', 'kr-1')", cwd=cwd)
        assert not (cwd / ".env").exists()
        assert {"WISP_API_KEY", "OPENROUTER_API_KEY"} <= _user_env_keys(home)

    def test_no_file_is_created_outside_the_operators_config(self, tmp_path: pathlib.Path) -> None:
        """The whole tree, not a list of expected paths: a new write site anywhere is caught."""
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        home = _persist(tmp_path, "from wisp.provider_select import persist\n"
                        f"persist({_ALL_KEYS})", cwd=cwd)
        config_dir = home / ".config" / "wisp"
        stray = [p for p in tmp_path.rglob("*")
                 if p.is_file() and config_dir not in p.parents]
        assert stray == []
        assert _user_env_keys(home)


def _load_user_env_calls() -> list[ast.Call]:
    calls = []
    for path in (REPO / "wisp").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name == "load_user_env":
                    calls.append(node)
    return calls


class TestTheOnlyReaderReadsTheOperatorsFile:
    def test_load_user_env_is_called_without_a_path(self) -> None:
        calls = _load_user_env_calls()
        assert calls, "floor: F57's load site must be found, or this check has not run"
        assert all(not c.args and not c.keywords for c in calls), (
            "load_user_env is called with a path: a workspace .env would become a configuration "
            "source, which PHASE_WORKSPACE_DOTENV.md §2 measured as a key-exfiltration path")

    def test_the_default_path_is_the_operators_own(self, tmp_path: pathlib.Path,
                                                   monkeypatch) -> None:
        from wisp.user_env import user_env_path

        monkeypatch.setenv("HOME", str(tmp_path))
        assert user_env_path() == tmp_path / ".config" / "wisp" / ".env"
