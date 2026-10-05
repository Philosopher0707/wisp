"""The affected-test lookup that runs after every write must not scale with the size of the machine.

Observed live: a REPL started from $HOME, `write_file` of a 5 KB script took minutes. After each write/edit the
executor builds an import graph of the whole workspace (to find affected tests), and the walk entered `site-packages`,
`node_modules`, virtualenvs and `~/Library`: tens of thousands of files, parsed one by one, synchronously, before the
tool result was returned. Nothing bounded it (the 60 s timeout covers only the pytest run that comes after).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wisp import import_graph
from wisp.import_graph import ImportGraphTooLarge, build_import_graph


def _py(path: Path, text: str = "x = 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize("junk", [
    "venv/lib/python3.12/site-packages/pkg/mod.py",
    ".venv/lib/site-packages/pkg/mod.py",
    "env/lib/python3.12/site-packages/pkg/mod.py",
    "node_modules/pkg/tool.py",
    "site-packages/pkg/mod.py",
    "build/lib/pkg/mod.py",
    "dist/pkg/mod.py",
    ".tox/py312/lib/mod.py",
    "Library/Application Support/thing/mod.py",
])
def test_dependency_and_system_directories_are_not_walked(tmp_path, junk):
    mine = _py(tmp_path / "src" / "app.py")
    _py(tmp_path / junk)
    assert set(build_import_graph(tmp_path)) == {mine.resolve()}


def test_a_workspace_with_too_many_python_files_is_refused_not_walked_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(import_graph, "MAX_FILES", 5)
    for i in range(6):
        _py(tmp_path / "pkg" / f"m{i}.py")
    with pytest.raises(ImportGraphTooLarge):
        build_import_graph(tmp_path)


def test_a_small_workspace_is_unaffected_by_the_budget(tmp_path):
    for i in range(5):
        _py(tmp_path / f"m{i}.py")
    assert len(build_import_graph(tmp_path)) == 5


def test_the_walk_has_a_wall_clock_budget(tmp_path, monkeypatch):
    for i in range(3):
        _py(tmp_path / f"m{i}.py")
    monkeypatch.setattr(import_graph, "BUDGET_SECONDS", -1.0)  # already spent
    with pytest.raises(ImportGraphTooLarge):
        build_import_graph(tmp_path)


def test_run_affected_tests_skips_instead_of_running_pytest_when_the_graph_is_too_large(tmp_path, monkeypatch):
    from wisp import test_runner

    monkeypatch.setattr(import_graph, "MAX_FILES", 2)
    for i in range(3):
        _py(tmp_path / f"m{i}.py")

    def no_pytest(*a, **k):
        raise AssertionError("pytest must not run when the affected-test lookup was skipped")

    monkeypatch.setattr(test_runner, "run_tests", no_pytest)
    started = time.monotonic()
    summary = test_runner.run_affected_tests(["m0.py"], tmp_path, timeout=5)
    assert summary.total == 0 and time.monotonic() - started < 2
    assert "skipped" in summary.stdout.lower() or summary.stderr, "the skip is reported, not silent"


def test_the_real_home_style_workspace_is_cheap(tmp_path):
    """A 'home' with a project, a virtualenv full of packages, and a Library tree: only the project is parsed."""
    _py(tmp_path / "proj" / "tool.py")
    for i in range(300):
        _py(tmp_path / "proj" / ".venv" / "lib" / "site-packages" / f"p{i}" / "m.py")
        _py(tmp_path / "Library" / "Application Support" / f"a{i}" / "m.py")
    started = time.monotonic()
    graph = build_import_graph(tmp_path)
    assert len(graph) == 1 and time.monotonic() - started < 2


def test_the_home_directory_is_never_walked(tmp_path, monkeypatch):
    """$HOME is not a project: even a bounded walk of it costs seconds on every write."""
    from wisp import test_runner

    monkeypatch.setenv("HOME", str(tmp_path))
    _py(tmp_path / "script.py")

    def no_walk(*a, **k):
        raise AssertionError("the home directory must not be walked")

    monkeypatch.setattr(test_runner, "build_import_graph", no_walk)
    started = time.monotonic()
    summary = test_runner.run_affected_tests(["script.py"], tmp_path, timeout=5)
    assert summary.total == 0 and time.monotonic() - started < 1
    assert "skipped" in summary.stdout.lower()


def test_a_too_large_verdict_is_remembered_so_only_the_first_write_pays(tmp_path, monkeypatch):
    from wisp import test_runner

    calls: list[int] = []

    def too_large(root):
        calls.append(1)
        raise ImportGraphTooLarge("big")

    test_runner._TOO_LARGE.clear()
    monkeypatch.setattr(test_runner, "build_import_graph", too_large)
    for _ in range(3):
        test_runner.run_affected_tests(["a.py"], tmp_path, timeout=5)
    assert len(calls) == 1, "a workspace that was too large a moment ago is still too large"


def test_the_remembered_verdict_expires(tmp_path, monkeypatch):
    from wisp import test_runner

    calls: list[int] = []

    def too_large(root):
        calls.append(1)
        raise ImportGraphTooLarge("big")

    test_runner._TOO_LARGE.clear()
    monkeypatch.setattr(test_runner, "build_import_graph", too_large)
    test_runner.run_affected_tests(["a.py"], tmp_path)
    monkeypatch.setattr(test_runner, "_TOO_LARGE_TTL_SECONDS", -1.0)  # everything remembered is now stale
    test_runner.run_affected_tests(["a.py"], tmp_path)
    assert len(calls) == 2
