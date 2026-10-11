"""INVARIANT G1, enforced by reading the source: the gates may not reach for the clock, randomness, the network, subprocesses, or
the environment. (They read the filesystem only to resolve symlinks and check `node_modules/.bin`.)"""

from __future__ import annotations

import ast
import pathlib

import pytest

GATES = sorted(pathlib.Path("wisp/core/gates").glob("*.py"))
BANNED_MODULES = {"time", "datetime", "random", "secrets", "uuid", "socket", "subprocess", "urllib", "requests", "http", "asyncio", "threading", "multiprocessing", "logging", "sqlite3", "shutil", "tempfile", "signal", "ctypes"}
BANNED_ATTRS = {("os", "environ"), ("os", "getenv"), ("os", "putenv"), ("os", "system"), ("os", "popen"), ("os", "getcwd"), ("os", "remove"), ("os", "unlink"), ("os", "rename"), ("os", "makedirs"), ("os", "mkdir"), ("os", "rmdir"), ("os", "chdir"), ("os", "walk"), ("os", "scandir"), ("os", "listdir")}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "print"}


@pytest.mark.parametrize("path", GATES, ids=lambda p: p.name)
def test_no_gate_module_uses_impure_facilities(path):
    tree = ast.parse(path.read_text())
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in BANNED_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in BANNED_MODULES:
            bad.append(node.module)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (node.value.id, node.attr) in BANNED_ATTRS:
            bad.append(f"{node.value.id}.{node.attr}")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in BANNED_NAMES:
            bad.append(node.id)
    assert not bad, f"{path.name} reaches for impure facilities: {sorted(set(bad))}"


def test_the_gates_import_nothing_from_the_engine():
    """The gates are a leaf: the engine depends on them, never the reverse, so they stay testable without a core."""
    for path in GATES:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for n in names:
                assert not n.startswith(("wisp.core.stateless", "wisp.core.runtime", "wisp.core.verification", "wisp.transport", "wisp.tools", "wisp.infra")), f"{path.name} imports {n}"


def test_the_set_of_gate_modules_is_the_set_this_file_audits():
    assert {p.name for p in GATES} == {"__init__.py", "commands.py", "deps.py", "gate.py", "invocations.py", "paths.py", "secrets.py", "shellparse.py", "verify.py"}
