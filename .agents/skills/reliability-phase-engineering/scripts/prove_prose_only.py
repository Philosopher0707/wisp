#!/usr/bin/env python3
"""Prove that a source edit is prose-only (docstrings and comments).

The claim *"this change is documentation-only"* is a claim about compiled
behaviour, and it is usually made by inspection. Inspection is exactly the
instrument that fails silently: a stray re-indentation, a moved statement or a
deleted blank line inside a function is invisible in a prose diff and is a real
behavioural change.

Two independent instruments, both of which must agree:

1. **Docstring-stripped AST equality.** Parse both revisions, delete every
   module/class/function docstring node, and compare `ast.dump`. Comments never
   reach the AST at all, so this covers them for free.
2. **Recursive `co_code` equality.** Compile both revisions and walk every code
   object, comparing the instruction stream. Docstring *text* lives in
   `co_consts[0]` and never appears in `co_code`, so a text-only change leaves
   every byte of the bytecode identical.

Neither instrument can be satisfied by a behavioural change, and each covers a
blind spot of the other: instrument 1 sees structure but not constants,
instrument 2 sees compiled constants but is harder to read when it fails.

Usage:
    python3 prove_prose_only.py <before.py> <after.py>

Exit code 0 = prose-only, 1 = a behavioural change is present (or a parse error).

A note on baselines: if the file is already modified in the working tree, a
version-control diff cannot isolate your change. Snapshot the file *before*
editing and compare snapshot-vs-now. Do not stash to obtain a baseline — on a
tree that carries unrelated uncommitted work in the same files, stashing reverts
that work to the last commit and discards it.
"""
from __future__ import annotations

import ast
import difflib
import sys
import types

_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if not isinstance(node, _DOC_OWNERS):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:]
    return tree


def _ast_fingerprint(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    return ast.dump(_strip_docstrings(tree), indent=2)


def _code_fingerprint(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    root = compile(source, path, "exec")
    lines: list[str] = []
    stack: list[types.CodeType] = [root]
    while stack:
        code = stack.pop()
        lines.append(f"{code.co_name}:{code.co_code.hex()}")
        stack.extend(
            const for const in code.co_consts
            if isinstance(const, types.CodeType)
        )
    return sorted(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    before_path, after_path = argv[1], argv[2]

    before_ast, after_ast = _ast_fingerprint(before_path), _ast_fingerprint(after_path)
    ast_ok = before_ast == after_ast

    before_code, after_code = _code_fingerprint(before_path), _code_fingerprint(after_path)
    code_ok = before_code == after_code

    print(f"docstring-stripped AST identical : {ast_ok}")
    print(f"recursive co_code identical      : {code_ok}")

    if not ast_ok:
        diff = difflib.unified_diff(
            before_ast.splitlines(), after_ast.splitlines(),
            "before", "after", lineterm="", n=3,
        )
        print("\n--- AST DIFF ---")
        print("\n".join(diff))

    if not code_ok:
        print("\n--- CODE OBJECTS THAT DIFFER ---")
        for name, blob in zip(before_code, after_code):
            if name != blob:
                print(f"  before: {name}")
                print(f"  after : {blob}")

    verdict = ast_ok and code_ok
    print(f"\nVERDICT: {'PROSE-ONLY (no behavioural change)' if verdict else 'NOT PROSE-ONLY'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
