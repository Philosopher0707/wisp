"""The invariant table in docs/harness/invariant-gates.md is executable: every witness it names must exist."""

from __future__ import annotations

import ast
import pathlib
import re

DOC = pathlib.Path("docs/harness/invariant-gates.md")
ROW = re.compile(r"^\| ([A-Z]\d) \|")
WITNESS = re.compile(r"`(tests/gates/[\w/]+\.py)(?:::([\w]+))?(?:::([\w]+))?`")


def _rows():
    return [line for line in DOC.read_text().splitlines() if ROW.match(line)]


def _names(path: pathlib.Path) -> tuple[set[str], dict[str, set[str]]]:
    tree = ast.parse(path.read_text())
    top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    members = {n.name: {m.name for m in n.body if isinstance(m, ast.FunctionDef)} for n in tree.body if isinstance(n, ast.ClassDef)}
    return top, members


def test_every_invariant_row_names_an_existing_witness():
    rows = _rows()
    assert len(rows) >= 25, "the table is missing rows"
    missing: list[str] = []
    for row in rows:
        ident = ROW.match(row).group(1)  # type: ignore[union-attr]
        m = WITNESS.search(row)
        if not m:
            missing.append(f"{ident}: no witness")
            continue
        file, a, b = m.groups()
        path = pathlib.Path(file)
        if not path.exists():
            missing.append(f"{ident}: {file} does not exist")
            continue
        top, members = _names(path)
        if b:
            if b not in members.get(a, set()):
                missing.append(f"{ident}: {file}::{a}::{b} not found")
        elif a and a not in top:
            missing.append(f"{ident}: {file}::{a} not found")
    assert not missing, missing


def test_invariant_ids_are_unique():
    ids = [ROW.match(r).group(1) for r in _rows()]  # type: ignore[union-attr]
    assert len(ids) == len(set(ids))


def test_every_layer_has_at_least_one_invariant():
    ids = {ROW.match(r).group(1)[0] for r in _rows()}  # type: ignore[union-attr]
    assert ids >= {"P", "C", "S", "D", "V", "G"}
