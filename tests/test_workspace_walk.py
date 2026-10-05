"""One bounded, pruned, deterministic walk of a workspace, in core, for every caller that reads files.

Why it exists: wisp had about thirteen independent `os.walk` / `rglob` loops with at least five private skip lists. When
one of them lacked a rule another had (the affected-test lookup after every write did not skip `site-packages` or
`Library`), a write from `$HOME` took 581 s. The mechanism (prune before descending, never follow symlinks, stable order,
a budget that either truncates or refuses) belongs in one place; each caller still states its own skip rules, so moving
a caller onto it changes nothing until that caller chooses to.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from wisp.core import workspace_walk as ww
from wisp.core.workspace_walk import STANDARD_SKIP_DIRS, WalkBudget, WalkBudgetExceeded, walk_files


def _f(root: Path, rel: str, text: str = "x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _rel(root: Path, paths) -> list[str]:
    return [Path(p).relative_to(root).as_posix() for p in paths]


def test_files_come_in_a_stable_sorted_order(tmp_path):
    for name in ("b/z.py", "a/y.py", "a/x.py", "c.py", "b/a.py"):
        _f(tmp_path, name)
    first = _rel(tmp_path, walk_files(tmp_path))
    # a directory's own files first, then its subdirectories, each sorted: the order os.walk gives with pruned, sorted dirnames
    assert first == ["c.py", "a/x.py", "a/y.py", "b/a.py", "b/z.py"]
    assert first == _rel(tmp_path, walk_files(tmp_path)), "two walks of the same tree agree"


def test_skip_dirs_are_pruned_not_entered(tmp_path, monkeypatch):
    _f(tmp_path, "src/app.py")
    for i in range(20):
        _f(tmp_path, f"node_modules/pkg{i}/m.py")
    scanned: list[str] = []
    real = os.scandir

    def spy(path="."):
        scanned.append(str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", spy)
    out = _rel(tmp_path, walk_files(tmp_path, skip_dirs=frozenset({"node_modules"})))
    assert out == ["src/app.py"]
    assert not any("node_modules" in p for p in scanned), "a skipped directory is never read, not read then discarded"


def test_hidden_directories_are_skipped_only_when_asked(tmp_path):
    _f(tmp_path, ".github/wf.yml")
    _f(tmp_path, "a.py")
    assert _rel(tmp_path, walk_files(tmp_path, skip_hidden=True)) == ["a.py"]
    assert sorted(_rel(tmp_path, walk_files(tmp_path, skip_hidden=False))) == [".github/wf.yml", "a.py"]


def test_a_virtualenv_is_recognised_by_pyvenv_cfg_whatever_it_is_called(tmp_path):
    _f(tmp_path, "proj/app.py")
    _f(tmp_path, "proj/tools-env/pyvenv.cfg", "home = /usr/bin")
    _f(tmp_path, "proj/tools-env/lib/site-x/mod.py")
    assert _rel(tmp_path, walk_files(tmp_path, skip_venvs=True)) == ["proj/app.py"]
    assert "proj/tools-env/lib/site-x/mod.py" in _rel(tmp_path, walk_files(tmp_path, skip_venvs=False))


def test_symlinks_are_never_followed(tmp_path):
    real = _f(tmp_path, "real/a.py")
    (tmp_path / "link.py").symlink_to(real)
    (tmp_path / "linkdir").symlink_to(tmp_path / "real", target_is_directory=True)
    assert _rel(tmp_path, walk_files(tmp_path)) == ["real/a.py"]


def test_suffix_filter(tmp_path):
    _f(tmp_path, "a.py")
    _f(tmp_path, "b.txt")
    assert _rel(tmp_path, walk_files(tmp_path, suffixes=(".py",))) == ["a.py"]


def test_a_stopping_budget_yields_what_it_has_and_says_so(tmp_path):
    for i in range(10):
        _f(tmp_path, f"f{i}.py")
    budget = WalkBudget(max_files=3)
    assert len(list(walk_files(tmp_path, budget=budget))) == 3
    assert budget.exceeded and budget.reason == "files"


def test_a_budget_that_is_exactly_enough_is_not_exceeded(tmp_path):
    for i in range(3):
        _f(tmp_path, f"f{i}.py")
    budget = WalkBudget(max_files=3)
    assert len(list(walk_files(tmp_path, budget=budget, on_budget="raise"))) == 3
    assert not budget.exceeded


def test_a_refusing_budget_raises_instead_of_returning_a_partial_tree(tmp_path):
    for i in range(4):
        _f(tmp_path, f"f{i}.py")
    with pytest.raises(WalkBudgetExceeded) as exc:
        list(walk_files(tmp_path, budget=WalkBudget(max_files=3), on_budget="raise"))
    assert "files" in str(exc.value)


def test_the_time_budget_applies_to_both_policies(tmp_path):
    _f(tmp_path, "a.py")
    spent = WalkBudget(max_seconds=-1.0)
    assert list(walk_files(tmp_path, budget=spent)) == [] and spent.reason == "time"
    with pytest.raises(WalkBudgetExceeded):
        list(walk_files(tmp_path, budget=WalkBudget(max_seconds=-1.0), on_budget="raise"))


def test_no_budget_means_unbounded(tmp_path):
    for i in range(50):
        _f(tmp_path, f"f{i}.py")
    assert len(list(walk_files(tmp_path))) == 50


def test_a_missing_root_yields_nothing(tmp_path):
    assert list(walk_files(tmp_path / "nope")) == []


def test_the_standard_skip_set_covers_what_cost_581_seconds():
    assert {"node_modules", "site-packages", "dist-packages", "venv", "env", "virtualenv", "__pycache__",
            "build", "dist", "Library"} <= STANDARD_SKIP_DIRS


def test_the_module_is_stdlib_only_so_core_stays_importable_anywhere():
    tree = ast.parse(Path(ww.__file__).read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if m.startswith("wisp")}, "core primitives import nothing from wisp"
