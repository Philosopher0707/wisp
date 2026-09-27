"""F57: `~/.config/wisp/.env` is written by `provider_select.store_key()` — and must be read.

Until this guard, nothing in `wisp/` read the file, so an API key placed there had no effect:
an operator's valid OpenRouter key produced `401 User not found`. The file is now loaded once,
by `wisp.user_env.load_user_env`, as the first statement of the console entry point
(`wisp.__main__:main`) — before any `WispConfig` or provider reads the environment.

**The observation point is the production path** (F96): each test runs the real entry point in a
subprocess, with a private `HOME`, and asks the real consumer — `provider_select.resolve_key`, the
function every provider-key lookup goes through — what it sees. A subprocess, because `main()`
installs signal handlers and logging that must not leak into this test session.

Semantics under test (ADR-0058's rule for a file one component writes and another reads —
*absence is a configuration, invalidity is a refusal*, with the brief's tolerance for a
best-effort writer):

* absent file → no-op, the environment byte-for-byte unchanged (a differential, not a claim);
* the **environment wins** — an exported key is never overwritten by the file;
* an unreadable file warns and does not raise;
* a malformed line warns with its line number, and the rest of the file still loads;
* a value is never written to a log line (the file holds secrets);
* one load site (AST, F73), and it runs before anything else in `main()`.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Every variable the writer maps to (`provider_select._persist_env`), cleared from the child's
#: environment so the test controls exactly which ones are "exported".
_WRITER_VARS = ("WISP_PROVIDER", "WISP_MODEL", "WISP_API_KEY", "WISP_API_BASE", "WISP_OLLAMA_URL",
                "OPENAI_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY")

_PROBE = r"""
import json, os, sys
before = dict(os.environ)
sys.argv = ["wisp", "--version"]
from wisp.__main__ import main
main()
from wisp.provider_select import resolve_key
after = dict(os.environ)
print("RESULT " + json.dumps({
    "openrouter": resolve_key("openrouter"),
    "openai": resolve_key("openai"),
    "added": sorted(set(after) - set(before)),
    "changed": sorted(k for k in before if k in after and before[k] != after[k]),
    "removed": sorted(set(before) - set(after)),
}))
"""


def _run(tmp_path: pathlib.Path, dotenv: str | bytes | None = None, *,
         exported: dict[str, str] | None = None, mode: int | None = None,
         as_directory: bool = False) -> tuple[dict, str]:
    home = tmp_path / "home"
    target = home / ".config" / "wisp" / ".env"
    target.parent.mkdir(parents=True, exist_ok=True)
    if as_directory:
        target.mkdir()
    elif dotenv is not None:
        (target.write_bytes if isinstance(dotenv, bytes) else target.write_text)(dotenv)
        if mode is not None:
            target.chmod(mode)
    env = {k: v for k, v in os.environ.items()
           if k not in _WRITER_VARS and k != "PYTHONPATH"}
    env.update({"HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(exported or {})
    try:
        proc = subprocess.run([sys.executable, "-c", _PROBE], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=120)
    finally:
        if mode is not None and target.exists():
            target.chmod(0o600)
    assert proc.returncode == 0, f"the entry point raised:\n{proc.stderr[-2000:]}"
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, f"the probe printed no result:\n{proc.stdout[-500:]}\n{proc.stderr[-500:]}"
    return json.loads(line[len("RESULT "):]), proc.stderr


class TestTheFileIsRead:
    def test_a_key_in_the_file_reaches_the_consumer(self, tmp_path):
        """The operator's case: a key in `~/.config/wisp/.env`, nothing exported."""
        result, _ = _run(tmp_path, "OPENROUTER_API_KEY=sk-or-from-file\n")
        assert result["openrouter"] == "sk-or-from-file", (
            "a key written to ~/.config/wisp/.env did not reach resolve_key — the file is "
            "still write-only (F57)")
        assert result["added"] == ["OPENROUTER_API_KEY"]

    def test_the_writers_own_output_is_read_back(self, tmp_path):
        """Round-trip through the production writer, so the reader reads what the writer writes."""
        from wisp.provider_select import _upsert_env_file

        home = tmp_path / "home"
        _upsert_env_file(home / ".config" / "wisp" / ".env",
                         {"OPENAI_API_KEY": "sk-openai-written", "WISP_MODEL": "m1"})
        result, _ = _run(tmp_path)
        assert result["openai"] == "sk-openai-written"
        assert set(result["added"]) == {"OPENAI_API_KEY", "WISP_MODEL"}


class TestTheEnvironmentWins:
    def test_an_exported_key_is_not_overwritten(self, tmp_path):
        result, _ = _run(tmp_path, "OPENROUTER_API_KEY=sk-or-from-file\n",
                         exported={"OPENROUTER_API_KEY": "sk-or-exported"})
        assert result["openrouter"] == "sk-or-exported", (
            "the file overrode an exported key — it must be a fallback, not an override")
        assert result["changed"] == [] and result["added"] == []

    def test_an_exported_empty_value_is_still_the_operators_choice(self, tmp_path):
        result, _ = _run(tmp_path, "OPENROUTER_API_KEY=sk-or-from-file\n",
                         exported={"OPENROUTER_API_KEY": ""})
        assert result["changed"] == [] and result["added"] == []


class TestAbsenceIsAConfiguration:
    def test_no_file_changes_nothing(self, tmp_path):
        """The differential: with no file, the environment after `main()` is the environment
        before it — every existing caller sees today's behaviour."""
        result, stderr = _run(tmp_path)
        assert (result["added"], result["changed"], result["removed"]) == ([], [], [])
        assert ".env" not in stderr, "an absent file is the default state and must be silent"


class TestInvalidityDoesNotBreakTheProcess:
    def test_an_unreadable_file_warns_and_does_not_raise(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root reads a 0o000 file")
        result, stderr = _run(tmp_path, "OPENROUTER_API_KEY=sk-or-from-file\n", mode=0o000)
        assert result["added"] == []
        assert "could not read" in stderr and ".env" in stderr, (
            f"an unreadable file must warn:\n{stderr[-800:]}")

    def test_a_directory_at_the_path_warns_and_does_not_raise(self, tmp_path):
        result, stderr = _run(tmp_path, as_directory=True)
        assert result["added"] == [] and "could not read" in stderr

    def test_a_malformed_line_is_skipped_and_named_and_the_rest_loads(self, tmp_path):
        dotenv = ("# a comment\n"
                  "OPENROUTER_API_KEY=sk-or-good\n"
                  "this line has no equals sign\n"
                  "=value-without-a-key\n"
                  "export WISP_MODEL=exported-syntax\n"
                  "\n"
                  "OPENAI_API_KEY=sk-openai-good\n")
        result, stderr = _run(tmp_path, dotenv)
        assert result["openrouter"] == "sk-or-good" and result["openai"] == "sk-openai-good", (
            "one bad line discarded the rest of the file")
        for n in (3, 4, 5):
            assert f"line {n}" in stderr, f"malformed line {n} was not named:\n{stderr[-800:]}"
        assert "line 2" not in stderr and "line 7" not in stderr

    def test_a_non_utf8_file_warns_and_does_not_raise(self, tmp_path):
        result, stderr = _run(tmp_path, b"OPENROUTER_API_KEY=\xff\xfe\n")
        assert result["added"] == [] and "could not read" in stderr


class TestSecretsStayOutOfLogs:
    def test_no_value_reaches_stderr(self, tmp_path):
        dotenv = "OPENROUTER_API_KEY=sk-or-SECRET-VALUE\nnot a line\nOPENAI_API_KEY=sk-SECRET-2\n"
        _result, stderr = _run(tmp_path, dotenv, exported={"OPENAI_API_KEY": "sk-exported"})
        assert "SECRET" not in stderr, "a value from the .env file reached a log line"


class TestThereIsOneLoadSite:
    def test_the_loader_is_called_once_first_in_main(self):
        """AST, not text (F73): exactly one call in `wisp/`, as `main()`'s first statement — so
        no `WispConfig` and no provider has read the environment before it."""
        files = list((REPO / "wisp").rglob("*.py"))
        assert len(files) >= 300, f"floor: only {len(files)} files under wisp/"
        sites = []
        for path in files:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if name == "load_user_env":
                        sites.append(f"{path.relative_to(REPO)}:{node.lineno}")
        assert len(sites) == 1, (
            f"load_user_env is called at {sites or 'no site'} — one read site, or two sites "
            "can disagree about the environment")
        main_py = REPO / "wisp" / "__main__.py"
        main = next(n for n in ast.parse(main_py.read_text(encoding="utf-8")).body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        body = [s for s in main.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        first = body[0]
        called = getattr(getattr(first, "value", None), "func", None)
        assert (getattr(called, "id", None) or getattr(called, "attr", None)) == "load_user_env", (
            "load_user_env is not main()'s first statement — something may read the "
            "environment before the file is loaded")
