"""Layer 5: which commands count as verification. Pure.

INVARIANT V1: a command is verification evidence only if (a) it runs a recognised test, lint, type-check or build tool, and (b) that
tool's exit status decides the whole command's exit status. `true`, `echo pytest`, `pytest || true`, `pytest | tail` (no pipefail),
`pytest; echo done`, `pytest &`, `! pytest` and `pytest --collect-only` are all NOT evidence, because each can exit 0 on broken code.

`VerificationFloorGuard` consults `classify` so a turn that changed code cannot finish on the strength of a command that proves
nothing. Read from the parse tree, never the raw string.
"""

from __future__ import annotations

from dataclasses import dataclass

from wisp.core.gates.invocations import Invocation, extract
from wisp.core.gates.shellparse import AndOr, Group, Node, Pipeline, Seq, Simple, Stmt, parse

MAX_DEPTH = 4


@dataclass(frozen=True)
class VerifyClass:
    ok: bool
    runner: str = ""
    kind: str = ""
    reason: str = ""


_NOT_EVIDENCE_FLAGS = frozenset({
    "--collect-only", "--co", "--help", "-h", "--version", "-V", "--setup-plan", "--fixtures", "--markers", "--dry-run",
    "--list", "--list-tests", "--init", "--print-config", "--show-config", "--version-info",
})
_TEST_TOOLS = frozenset({
    "pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha", "ava", "karma", "phpunit", "rspec", "ctest", "pytest-xdist", "trial", "nosetests", "behave", "robot",
})
_LINT_TOOLS = frozenset({"mypy", "pyright", "pylint", "flake8", "pycodestyle", "pyflakes", "eslint", "tsc", "stylelint", "shellcheck", "hadolint", "rubocop", "golangci-lint", "swiftlint", "ktlint", "yamllint", "markdownlint", "bandit", "vulture", "pyre", "pytype"})
_NEEDS_CHECK_FLAG = frozenset({"black", "isort", "prettier", "gofmt", "rustfmt"})
_PY_MODULES = frozenset({"pytest", "unittest", "mypy", "pylint", "flake8", "pyright", "tox", "nox", "pyflakes", "pycodestyle", "ruff", "black", "isort", "compileall", "doctest", "trace"})
_SCRIPT_KINDS = {"test": "test", "t": "test", "lint": "lint", "typecheck": "typecheck", "type-check": "typecheck", "check": "lint", "ci": "test", "verify": "test", "build": "build", "compile": "build"}
_MAKE_TARGETS = {"test": "test", "tests": "test", "check": "lint", "lint": "lint", "ci": "test", "verify": "test", "typecheck": "typecheck", "build": "build", "all": "build"}
_WRAPPER_RUNNERS = frozenset({"run", "exec", "x"})


def _classify_tool(name: str, args: tuple[str, ...]) -> VerifyClass | None:
    if any(a in _NOT_EVIDENCE_FLAGS for a in args):
        return None
    if name in _TEST_TOOLS:
        return VerifyClass(True, name, "test")
    if name in _LINT_TOOLS:
        if name == "tsc" and "--init" in args:
            return None
        return VerifyClass(True, name, "typecheck" if name in ("mypy", "pyright", "tsc", "pyre", "pytype") else "lint")
    if name in _NEEDS_CHECK_FLAG:
        return VerifyClass(True, name, "lint") if any(a in ("--check", "-c", "--diff", "-l", "--list-different", "-d") for a in args) else None
    if name == "ruff":
        sub = args[0] if args else ""
        if sub == "check":
            return VerifyClass(True, "ruff", "lint")
        if sub == "format" and any(a in ("--check", "--diff") for a in args):
            return VerifyClass(True, "ruff", "lint")
        return None
    if name == "cargo":
        sub = args[0] if args else ""
        if sub in ("test", "nextest", "bench"):
            return VerifyClass(True, "cargo", "test")
        if sub in ("check", "clippy", "build", "doc"):
            return VerifyClass(True, "cargo", "build" if sub == "build" else "lint")
        if sub == "fmt" and "--check" in args:
            return VerifyClass(True, "cargo", "lint")
        return None
    if name == "go":
        sub = args[0] if args else ""
        return VerifyClass(True, "go", "test" if sub == "test" else "build" if sub == "build" else "lint") if sub in ("test", "vet", "build") else None
    if name == "swift":
        sub = args[0] if args else ""
        return VerifyClass(True, "swift", "test" if sub == "test" else "build") if sub in ("test", "build") else None
    if name in ("gradle", "gradlew", "mvn", "mvnw", "dotnet", "sbt", "bazel", "deno", "bun", "zig", "rake", "bundle", "ant"):
        ops = [a for a in args if not a.startswith("-")]
        sub = ops[0] if ops else ""
        if name in ("gradle", "gradlew"):
            return VerifyClass(True, name, "test") if any(o in ("test", "check", "build", "assemble", "connectedCheck", "lint") for o in ops) else None
        if name in ("mvn", "mvnw"):
            return VerifyClass(True, name, "test") if any(o in ("test", "verify", "package", "install", "compile") for o in ops) else None
        if name == "dotnet":
            return VerifyClass(True, name, "test" if sub == "test" else "build") if sub in ("test", "build") else None
        if name == "sbt":
            return VerifyClass(True, name, "test") if any(o in ("test", "compile", "check") for o in ops) else None
        if name == "bazel":
            return VerifyClass(True, name, "test") if sub in ("test", "build") else None
        if name == "deno":
            return VerifyClass(True, name, "test" if sub == "test" else "lint") if sub in ("test", "lint", "check") else None
        if name == "bun":
            return VerifyClass(True, name, "test") if sub == "test" else None
        if name == "zig":
            return VerifyClass(True, name, "test" if sub == "test" else "build") if sub in ("test", "build") else None
        if name == "rake":
            return VerifyClass(True, name, "test") if any(o in ("test", "spec", "check") for o in ops) else None
        if name == "bundle":
            return _classify_tool(ops[1], tuple(args[args.index(ops[1]) + 1:])) if sub == "exec" and len(ops) > 1 else None
        return None
    if name == "make" or name == "gmake":
        ops = [a for a in args if not a.startswith("-")]
        if any(a in ("-n", "--dry-run", "-q", "--question", "-t", "--touch", "-p", "--print-data-base") for a in args):
            return None
        if not ops:
            return VerifyClass(True, name, "build")
        for o in ops:
            if o in _MAKE_TARGETS:
                return VerifyClass(True, name, _MAKE_TARGETS[o])
            if o.startswith(("test", "lint", "check")):
                return VerifyClass(True, name, "test" if o.startswith("test") else "lint")
        return None
    return None


def _classify_invocation(inv: Invocation) -> VerifyClass | None:
    if inv.unbounded_args or not inv.argv:
        return None
    name = inv.name
    args = tuple(a.text for a in inv.args)
    if name.startswith("python") or name in ("python", "py"):
        if len(args) >= 2 and args[0] == "-m" and args[1] in _PY_MODULES:
            module, rest = args[1], args[2:]
            if module in ("pytest", "tox", "nox"):
                return None if any(a in _NOT_EVIDENCE_FLAGS for a in rest) else VerifyClass(True, module, "test")
            if module == "unittest":
                return VerifyClass(True, "unittest", "test")
            return _classify_tool(module, rest) or (VerifyClass(True, module, "build") if module == "compileall" else None)
        return None
    if name in ("uv", "poetry", "pipenv", "pdm", "hatch", "rye"):
        if args and args[0] in ("run", "exec") and len(args) > 1:
            return _classify_tool(args[1].split("/")[-1], tuple(args[2:]))
        return None
    if name in ("npx", "bunx", "pnpx") and args:
        return _classify_tool(args[0], tuple(args[1:]))
    if name in ("npm", "yarn", "pnpm", "bun"):
        ops = [a for a in args if not a.startswith("-")]
        if not ops:
            return None
        sub = ops[0]
        if sub in ("test", "t", "tst", "lint", "typecheck", "check", "build", "ci-test", "verify"):
            if sub == "ci":
                return None
            return VerifyClass(True, name, _SCRIPT_KINDS.get(sub, "test"))
        if sub in ("run", "run-script") and len(ops) > 1:
            script = ops[1].split(":", 1)[0]
            return VerifyClass(True, name, _SCRIPT_KINDS[script]) if script in _SCRIPT_KINDS else None
        if sub in _WRAPPER_RUNNERS and len(ops) > 1:
            return _classify_tool(ops[1], tuple(args[args.index(ops[1]) + 1:]))
        if name == "yarn" and sub in _SCRIPT_KINDS:
            return VerifyClass(True, name, _SCRIPT_KINDS[sub])
        return None
    return _classify_tool(name, args)


def _is_failing_terminal(pl: Pipeline) -> bool:
    if len(pl.stages) != 1 or not isinstance(pl.stages[0], Simple):
        return False
    s = pl.stages[0]
    if not s.words:
        return False
    head = s.words[0].text
    if head == "false":
        return True
    if head in ("exit", "return"):
        if len(s.words) == 1:
            return True
        arg = s.words[1]
        return not arg.dynamic and arg.text.lstrip("+") not in ("0", "00")
    return False


def _sets_pipefail(s: Simple) -> bool | None:
    if not s.words or s.words[0].text != "set":
        return None
    texts = [w.text for w in s.words[1:]]
    if "pipefail" not in texts:
        return None
    return not any(t.startswith("+") and "o" in t for t in texts)


def _shell_pipefail(inv: Invocation) -> bool:
    texts = [a.text for a in inv.args]
    return any(t == "pipefail" for t in texts) and any(t.startswith("-") and "o" in t for t in texts)


def _node_verifies(node: Node, pipefail: bool, depth: int) -> VerifyClass | None:
    if isinstance(node, Group):
        return _seq_verifies(node.body, pipefail, depth)
    if not node.words:
        return None
    one = Seq((Stmt(AndOr(Pipeline((node,)))),))
    invs, problems = extract(one, "/", "")
    if problems or not invs:
        return None
    for inv in invs:
        if inv.inline_shell is not None:
            if depth >= MAX_DEPTH:
                return None
            return _classify_text(inv.inline_shell, pipefail or _shell_pipefail(inv), depth + 1)
    if len(invs) != 1:
        return None
    return _classify_invocation(invs[0])


def _pipeline_verifies(pl: Pipeline, pipefail: bool, depth: int) -> VerifyClass | None:
    if pl.negated:
        return None
    eligible = range(len(pl.stages)) if pipefail else [len(pl.stages) - 1]
    for idx in eligible:
        v = _node_verifies(pl.stages[idx], pipefail, depth)
        if v is not None:
            return v
    return None


def _andor_verifies(ao: AndOr, pipefail: bool, depth: int) -> VerifyClass | None:
    pipelines = [ao.first] + [p for _op, p in ao.rest]
    ops = [op for op, _p in ao.rest]
    limit = len(pipelines)
    if "||" in ops:
        cut = ops.index("||") + 1
        if not all(_is_failing_terminal(p) for p in pipelines[cut:]) or any(o == "&&" for o in ops[cut:]):
            return None
        limit = cut
    for pl in pipelines[:limit]:
        v = _pipeline_verifies(pl, pipefail, depth)
        if v is not None:
            return v
    return None


def _seq_verifies(seq: Seq, pipefail: bool, depth: int) -> VerifyClass | None:
    if not seq.stmts:
        return None
    for stmt in seq.stmts[:-1]:
        for pl in [stmt.andor.first] + [p for _op, p in stmt.andor.rest]:
            for node in pl.stages:
                if isinstance(node, Simple):
                    flag = _sets_pipefail(node)
                    if flag is not None:
                        pipefail = flag
    final = seq.stmts[-1]
    if final.background:
        return None
    for pl in [final.andor.first] + [p for _op, p in final.andor.rest]:
        for node in pl.stages:
            if isinstance(node, Simple):
                flag = _sets_pipefail(node)
                if flag is not None and pl is final.andor.first:
                    pipefail = flag
    return _andor_verifies(final.andor, pipefail, depth)


def _classify_text(command: str, pipefail: bool, depth: int) -> VerifyClass | None:
    parsed = parse(command)
    if not parsed.ok or parsed.tree is None:
        return None
    return _seq_verifies(parsed.tree, pipefail, depth)


def classify(command: str) -> VerifyClass:
    """Is `command` evidence that the code works? `ok` only when it runs a verification tool whose status decides the result."""
    if not isinstance(command, str) or not command.strip():
        return VerifyClass(False, reason="empty command")
    v = _classify_text(command, False, 0)
    if v is not None:
        return v
    return VerifyClass(False, reason="not a test, lint, type-check or build run whose exit status decides the command's result")
