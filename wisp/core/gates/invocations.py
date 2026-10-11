"""From a parse tree to the list of commands that would actually execute. Pure.

`extract(tree, cwd)` walks the tree and returns every `Invocation` (a command with its arguments, the working directory it runs
in, its redirections and where it sits in a pipeline) plus a list of `Problem`s: things that cannot be decided statically and
therefore make the caller fail closed (a command whose name is only known at run time, `eval`).

Wrappers are looked through, so the rules see the command that really runs: `env X=1 timeout 5 xargs -n1 rm -rf` yields an `rm`
invocation (marked `unbounded_args` because xargs supplies more operands at run time), and `bash -c '…'` is parsed recursively.
The working directory is tracked lexically through `cd`; when it cannot be known (`cd $X`, `cd -`, a `cd` on a `||` branch)
it becomes `None` and path rules treat every relative path as unprovable.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from wisp.core.gates.shellparse import AndOr, Group, Node, Parse, Pipeline, Redirect, Seq, Simple, Word, parse

MAX_INVOCATIONS = 500
MAX_WRAPPER_DEPTH = 6

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "fish"})
PRIVILEGE = frozenset({"sudo", "doas", "su", "pkexec", "runas"})


@dataclass(frozen=True)
class Invocation:
    argv: tuple[Word, ...]  # argv[0] is the command that runs, after wrappers are stripped
    cwd: str | None  # lexical absolute working directory, None when unknown
    redirects: tuple[Redirect, ...] = ()
    stage: int = 0
    stages: int = 1
    earlier: tuple[str, ...] = ()  # names of the commands feeding this one through a pipe
    earlier_args: tuple[tuple[str, ...], ...] = ()  # and their arguments, one tuple per earlier stage
    via: tuple[str, ...] = ()  # wrappers that were looked through, outermost first
    unbounded_args: bool = False  # more operands arrive at run time (xargs, find -exec … +)
    inline_shell: str | None = None  # the string given to `sh -c`, when this invocation came from one

    @property
    def name(self) -> str:
        return posixpath.basename(self.argv[0].text) if self.argv else ""

    @property
    def args(self) -> tuple[Word, ...]:
        return self.argv[1:]


@dataclass(frozen=True)
class Problem:
    rule: str
    reason: str


# ── wrapper stripping ─────────────────────────────────────────────────────

# For each wrapper: options that take a value (so the value is not mistaken for the wrapped command).
_ENV_VALUE_OPTS = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})
_TIMEOUT_VALUE_OPTS = frozenset({"-s", "--signal", "-k", "--kill-after"})
_NICE_VALUE_OPTS = frozenset({"-n", "--adjustment"})
_IONICE_VALUE_OPTS = frozenset({"-c", "-n", "-p", "-P", "-u", "-t"})
_STDBUF_VALUE_OPTS = frozenset({"-i", "-o", "-e"})
_XARGS_VALUE_OPTS = frozenset({"-I", "-i", "-n", "-P", "-L", "-l", "-d", "-E", "-e", "-s", "-a", "--max-args", "--max-procs", "--delimiter", "--arg-file", "--replace"})
_SUDO_VALUE_OPTS = frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group", "--chdir", "--host", "--prompt", "--role", "--type", "--other-user", "--command-timeout"})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


def _is_option(w: Word) -> bool:
    return w.text.startswith("-") and w.text != "-" and not w.dynamic


def _skip_options(words: tuple[Word, ...], value_opts: frozenset[str]) -> int:
    """Index of the first word that is not an option (or an option's value)."""
    i = 0
    while i < len(words):
        t = words[i].text
        if t == "--":
            return i + 1
        if not _is_option(words[i]):
            return i
        if t in value_opts:
            i += 2
        else:
            i += 1
    return i


def _unwrap(argv: tuple[Word, ...]) -> tuple[tuple[Word, ...] | None, str, bool]:
    """If argv[0] is a transparent wrapper, return (the wrapped argv, wrapper name, adds_unbounded_args).

    Returns (None, "", False) when argv[0] is not a wrapper or the wrapper wraps nothing.
    """
    name = posixpath.basename(argv[0].text)
    rest = argv[1:]
    if name == "env":
        i = 0
        while i < len(rest):
            t = rest[i].text
            if t == "--":
                i += 1
                break
            if "=" in t and not t.startswith("-") and not rest[i].dynamic:
                i += 1
            elif _is_option(rest[i]):
                i += 2 if t in _ENV_VALUE_OPTS else 1
            else:
                break
        return (rest[i:] or None), "env", False
    if name in ("command", "builtin"):
        i = _skip_options(rest, frozenset())
        if any(w.text in ("-v", "-V") for w in rest[:i]):
            return None, "", False  # only describes the command, runs nothing
        return (rest[i:] or None), name, False
    if name == "exec":
        i = _skip_options(rest, frozenset({"-a"}))
        return (rest[i:] or None), "exec", False
    if name in ("sudo", "doas", "pkexec"):
        i = _skip_options(rest, _SUDO_VALUE_OPTS)
        return (rest[i:] or None), name, False
    if name in ("nohup", "setsid", "time", "caffeinate", "unbuffer"):
        i = _skip_options(rest, frozenset())
        return (rest[i:] or None), name, False
    if name == "nice":
        i = _skip_options(rest, _NICE_VALUE_OPTS)
        return (rest[i:] or None), "nice", False
    if name == "ionice":
        i = _skip_options(rest, _IONICE_VALUE_OPTS)
        return (rest[i:] or None), "ionice", False
    if name == "stdbuf":
        i = _skip_options(rest, _STDBUF_VALUE_OPTS)
        return (rest[i:] or None), "stdbuf", False
    if name == "timeout":
        i = _skip_options(rest, _TIMEOUT_VALUE_OPTS)
        i += 1  # the DURATION operand
        return (rest[i:] or None), "timeout", False
    if name == "xargs":
        i = _skip_options(rest, _XARGS_VALUE_OPTS)
        return (rest[i:] or None), "xargs", True
    if name == "find":
        for k, w in enumerate(rest):
            if w.text in _FIND_EXEC:
                inner: list[Word] = []
                for w2 in rest[k + 1:]:
                    if w2.text in (";", "+") and not w2.dynamic:
                        break
                    inner.append(w2)
                if inner:
                    return tuple(inner), "find-exec", True
        return None, "", False
    return None, "", False


@dataclass(frozen=True)
class ShellForm:
    string: str | None  # the `-c` script, when it is known
    is_shell: bool
    dynamic: bool  # `-c` was given a script that is only known at run time
    operand: bool  # a script FILE was named (`sh run.sh`)


def _shell_form(argv: tuple[Word, ...]) -> ShellForm:
    name = posixpath.basename(argv[0].text)
    if name not in SHELLS:
        return ShellForm(None, False, False, False)
    rest = argv[1:]
    i = 0
    while i < len(rest):
        t = rest[i].text
        if t == "--":
            return ShellForm(None, True, False, i + 1 < len(rest))
        if t[:1] in "-+" and not t.startswith("--") and len(t) > 1 and t[-1] in "oO" and "c" not in t:
            i += 2  # a flag cluster ending in -o/-O takes an option name, which is not the script
            continue
        if t.startswith("-") and not t.startswith("--") and "c" in t[1:]:
            if i + 1 < len(rest):
                nxt = rest[i + 1]
                return ShellForm(None, True, True, False) if nxt.dynamic else ShellForm(nxt.text, True, False, False)
            return ShellForm(None, True, False, False)
        if t.startswith("-"):
            i += 1
            continue
        return ShellForm(None, True, False, True)
    return ShellForm(None, True, False, False)


def _redirected_script(redirects: tuple[Redirect, ...]) -> tuple[str | None, bool]:
    """(script text, dynamic) when stdin is a here-document or here-string; (None, False) otherwise."""
    for r in redirects:
        if r.op in ("<<", "<<-"):
            dynamic = bool(r.target and r.target.dynamic)
            return (None, True) if dynamic else (r.heredoc or "", False)
        if r.op == "<<<" and r.target is not None:
            return (None, True) if r.target.dynamic else (r.target.text, False)
    return None, False


# ── walking ───────────────────────────────────────────────────────────────


class _Walker:
    def __init__(self, home: str) -> None:
        self.home = home
        self.out: list[Invocation] = []
        self.problems: list[Problem] = []

    def _add(self, inv: Invocation) -> None:
        if len(self.out) >= MAX_INVOCATIONS:
            self.problems.append(Problem("TOO_MANY_COMMANDS", f"more than {MAX_INVOCATIONS} commands in one request"))
            return
        self.out.append(inv)

    def seq(self, seq: Seq, cwd: str | None) -> str | None:
        for stmt in seq.stmts:
            cwd = self.andor(stmt.andor, cwd)
        return cwd

    def andor(self, ao: AndOr, cwd: str | None) -> str | None:
        cwd = self.pipeline(ao.first, cwd)
        for op, pl in ao.rest:
            after = self.pipeline(pl, cwd)
            # A `cd` on the right of `||` may or may not have run, so the directory afterwards is unknown.
            cwd = after if op == "&&" else (cwd if after == cwd else None)
        return cwd

    def pipeline(self, pl: Pipeline, cwd: str | None) -> str | None:
        n = len(pl.stages)
        earlier: list[str] = []
        earlier_args: list[tuple[str, ...]] = []
        result = cwd
        for idx, node in enumerate(pl.stages):
            after = self.node(node, cwd, idx, n, tuple(earlier), tuple(earlier_args))
            if n == 1:
                result = after  # only a lone command can change the directory for what follows
            earlier.append(self._stage_name(node))
            earlier_args.append(self._stage_args(node))
        return result

    @staticmethod
    def _stage_args(node: Node) -> tuple[str, ...]:
        return tuple(w.text for w in node.words[1:]) if isinstance(node, Simple) else ()

    @staticmethod
    def _stage_name(node: Node) -> str:
        if isinstance(node, Simple) and node.words:
            return posixpath.basename(node.words[0].text)
        return ""

    def node(self, node: Node, cwd: str | None, idx: int, n: int, earlier: tuple[str, ...], earlier_args: tuple[tuple[str, ...], ...]) -> str | None:
        if isinstance(node, Group):
            for r in node.redirects:
                self._visit_redirect_subs(r, cwd)
            if node.redirects:
                self._add(Invocation((), cwd, node.redirects, idx, n, earlier, earlier_args))
            inner_end = self.seq(node.body, cwd)
            return cwd if node.kind == "subshell" else inner_end
        return self.simple(node, cwd, idx, n, earlier, earlier_args)

    def _visit_redirect_subs(self, r: Redirect, cwd: str | None) -> None:
        if r.target is not None:
            for sub in r.target.subs:
                self.seq(sub, cwd)

    def simple(self, s: Simple, cwd: str | None, idx: int, n: int, earlier: tuple[str, ...], earlier_args: tuple[tuple[str, ...], ...]) -> str | None:
        for _name, value in s.assignments:
            for sub in value.subs:
                self.seq(sub, cwd)
        for w in s.words:
            for sub in w.subs:
                self.seq(sub, cwd)
        for r in s.redirects:
            self._visit_redirect_subs(r, cwd)
        if not s.words:
            if s.redirects:
                self._add(Invocation((), cwd, s.redirects, idx, n, earlier, earlier_args))
            return cwd
        head = s.words[0]
        if head.dynamic:
            self.problems.append(Problem("DYNAMIC_COMMAND", f"the command name {head.text!r} is only known at run time"))
            return cwd
        if head.text == "::loop-header::":
            return cwd
        if posixpath.basename(head.text) == "cd":
            return self._cd(s.words[1:], cwd, s.redirects, idx, n, earlier, earlier_args)
        self._resolve(s.words, cwd, s.redirects, idx, n, earlier, earlier_args, (), False, 0)
        return cwd

    def _cd(self, args: tuple[Word, ...], cwd: str | None, redirects: tuple[Redirect, ...], idx: int, n: int, earlier: tuple[str, ...], earlier_args: tuple[tuple[str, ...], ...]) -> str | None:
        operands = [a for a in args if not (_is_option(a))]
        if redirects:
            self._add(Invocation((), cwd, redirects, idx, n, earlier, earlier_args))
        if not operands:
            return self.home or None
        target = operands[0]
        if target.dynamic or target.text == "-" or cwd is None and not target.text.startswith(("/", "~")):
            return None
        text = target.text
        if text == "~" or text.startswith("~/"):
            if not self.home:
                return None
            text = posixpath.join(self.home, text[2:]) if text != "~" else self.home
        elif text.startswith("~"):
            return None
        if not text.startswith("/"):
            assert cwd is not None
            text = posixpath.join(cwd, text)
        return posixpath.normpath(text)

    def _resolve(self, argv: tuple[Word, ...], cwd: str | None, redirects: tuple[Redirect, ...], idx: int, n: int,
                 earlier: tuple[str, ...], earlier_args: tuple[tuple[str, ...], ...], via: tuple[str, ...], unbounded: bool, depth: int) -> None:
        if depth > MAX_WRAPPER_DEPTH:
            self.problems.append(Problem("WRAPPER_DEPTH", "commands wrapped too deeply to analyse"))
            return
        head = argv[0]
        if head.dynamic:
            self.problems.append(Problem("DYNAMIC_COMMAND", f"the command name {head.text!r} is only known at run time"))
            return
        name = posixpath.basename(head.text)
        if name == "eval":
            self.problems.append(Problem("EVAL", "`eval` runs text that is only known at run time"))
            return
        inner, wrapper, adds_unbounded = _unwrap(argv)
        if wrapper:
            if inner is None:
                self._add(Invocation(argv, cwd, redirects, idx, n, earlier, earlier_args, via, unbounded))
                return
            self._resolve(inner, cwd, redirects, idx, n, earlier, earlier_args, via + (wrapper,), unbounded or adds_unbounded, depth + 1)
            return
        form = _shell_form(argv)
        string, is_shell, dynamic_script = form.string, form.is_shell, form.dynamic
        if is_shell and string is None and not dynamic_script and not form.operand:
            # `sh <<< "…"` / `sh <<EOF … EOF`: the script is visible, so analyse it like `-c`.
            string, dynamic_script = _redirected_script(redirects)
        if is_shell and dynamic_script:
            self.problems.append(Problem("DYNAMIC_INLINE_SHELL", f"`{name}` was given a script that is only known at run time"))
            return
        if is_shell and string is not None:
            parsed: Parse = parse(string)
            if not parsed.ok or parsed.tree is None:
                self.problems.append(Problem("UNPARSABLE_INLINE_SHELL", f"`{name}` was given a script that cannot be analysed: {'; '.join(parsed.reasons)}"))
                return
            self._add(Invocation(argv, cwd, redirects, idx, n, earlier, earlier_args, via, unbounded, inline_shell=string))
            self.seq(parsed.tree, cwd)
            return
        self._add(Invocation(argv, cwd, redirects, idx, n, earlier, earlier_args, via, unbounded))


def extract(tree: Seq, cwd: str | None, home: str = "") -> tuple[tuple[Invocation, ...], tuple[Problem, ...]]:
    """Every command that would run, and every reason the answer is incomplete. Deterministic."""
    w = _Walker(home)
    w.seq(tree, cwd)
    return tuple(w.out), tuple(w.problems)
