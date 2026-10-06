"""RC1/RC10 by reading the source: the reasoning core's pure layer may not touch the clock, randomness, the network, subprocesses, the
environment, the filesystem or the engine. It is a function of its inputs, so replaying a transcript reproduces its verdicts."""

from __future__ import annotations

import ast
import pathlib

import pytest

MODULES = sorted(pathlib.Path("wisp/core/reasoning").glob("*.py"))
BANNED_MODULES = {"time", "datetime", "random", "secrets", "uuid", "socket", "subprocess", "urllib", "requests", "http", "asyncio", "threading", "multiprocessing", "logging", "sqlite3", "shutil", "tempfile", "signal", "ctypes", "os", "pathlib"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "print"}
ENGINE_PREFIXES = ("wisp.core.stateless", "wisp.core.runtime", "wisp.transport", "wisp.tools", "wisp.infra", "wisp.providers")


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_reasoning_module_uses_impure_facilities(path):
    bad: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in BANNED_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in BANNED_MODULES:
            bad.append(node.module)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in BANNED_NAMES:
            bad.append(node.id)
    assert not bad, f"{path.name} reaches for impure facilities: {sorted(set(bad))}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_the_reasoning_core_imports_nothing_from_the_engine(path):
    for node in ast.walk(ast.parse(path.read_text())):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        for n in names:
            assert not n.startswith(ENGINE_PREFIXES), f"{path.name} imports {n}"


def test_the_set_of_modules_is_the_set_this_file_audits():
    assert {p.name for p in MODULES} == {"__init__.py", "claims.py", "ledger.py"}
