"""Layer 1: path and file boundaries. Pure apart from reading the filesystem to resolve symlinks (`realpath`).

INVARIANT: no write is allowed unless its target, after `realpath`, is the workspace root, inside it, or inside an explicit
whitelist root. Containment is the single primitive `wisp.pathsec.resolve_contained`; this module only decides *which words are
write targets* and refuses what cannot be proven.

A target that cannot be resolved statically (`$VAR`, `$(…)`, `~user`, an unknown working directory) is refused, not guessed.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from wisp.core.gates.invocations import Invocation
from wisp.core.gates.shellparse import Redirect, Word
from wisp.pathsec import is_protected_path, resolve_contained

# Devices that are always safe to write: output sinks, never storage.
SAFE_DEVICES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty", "/dev/zero"})

_WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})

# Commands for which every non-option operand is a write target.
_ALL_OPERANDS = frozenset({"rm", "rmdir", "unlink", "shred", "mkdir", "touch", "tee", "mkfifo", "mknod"})
# Option values that are not operands, per command.
_VALUE_OPTS: dict[str, frozenset[str]] = {
    "touch": frozenset({"-r", "-d", "-t", "--reference", "--date"}),
    "mkdir": frozenset({"-m", "--mode"}),
    "truncate": frozenset({"-s", "-r", "--size", "--reference"}),
    "mv": frozenset({"-t", "--target-directory", "-S", "--suffix"}),
    "cp": frozenset({"-t", "--target-directory", "-S", "--suffix"}),
    "install": frozenset({"-m", "-o", "-g", "-t", "--mode", "--owner", "--group", "--target-directory", "-S", "--suffix"}),
    "ln": frozenset({"-t", "--target-directory", "-S", "--suffix"}),
    "rsync": frozenset({"-e", "--rsh", "--exclude", "--include", "--exclude-from", "--include-from", "--filter", "-f", "--port", "--bwlimit", "--timeout", "--rsync-path", "--backup-dir"}),
    "tee": frozenset(),
}
_FIRST_OPERAND_IS_NOT_A_PATH = frozenset({"chmod", "chown", "chgrp"})


@dataclass(frozen=True)
class PathContext:
    workspace: str
    home: str = ""
    extra_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriteTarget:
    word: Word
    via: str


def _operands(args: tuple[Word, ...], value_opts: frozenset[str]) -> list[Word]:
    out: list[Word] = []
    i = 0
    only_operands = False
    while i < len(args):
        w = args[i]
        t = w.text
        if only_operands:
            out.append(w)
        elif t == "--":
            only_operands = True
        elif t.startswith("-") and t != "-" and not w.dynamic:
            if t in value_opts:
                i += 1
        else:
            out.append(w)
        i += 1
    return out


def _option_value(args: tuple[Word, ...], names: frozenset[str]) -> Word | None:
    for i, w in enumerate(args):
        if w.text in names and i + 1 < len(args):
            return args[i + 1]
        for n in names:
            if n.startswith("--") and w.text.startswith(n + "="):
                return Word(w.text[len(n) + 1:], dynamic=w.dynamic, glob=w.glob, quoted=w.quoted, subs=w.subs)
    return None


def _is_remote(w: Word) -> bool:
    head = w.text.split("/", 1)[0]
    return ":" in head and not w.text.startswith(("/", "."))


def redirect_targets(redirects: tuple[Redirect, ...]) -> tuple[WriteTarget, ...]:
    out: list[WriteTarget] = []
    for r in redirects:
        if r.target is None:
            continue
        if r.op in _WRITE_REDIRECTS:
            out.append(WriteTarget(r.target, f"redirect {r.op}"))
        elif r.op in (">&", "<&") and r.target.text not in ("-",) and not r.target.text.isdigit():
            out.append(WriteTarget(r.target, f"redirect {r.op}"))
    return tuple(out)


def write_targets(inv: Invocation) -> tuple[WriteTarget, ...]:
    """The words of `inv` that name something it would create, change or delete. Unknown commands name none: the sandbox, not
    this layer, is what bounds an interpreter running arbitrary code."""
    out = list(redirect_targets(inv.redirects))
    if not inv.argv:
        return tuple(out)
    name, args = inv.name, inv.args
    if name in _ALL_OPERANDS:
        out += [WriteTarget(w, name) for w in _operands(args, _VALUE_OPTS.get(name, frozenset()))]
    elif name in _FIRST_OPERAND_IS_NOT_A_PATH:
        ops = _operands(args, frozenset())
        has_ref = any(a.text.startswith("--reference") for a in args)
        out += [WriteTarget(w, name) for w in (ops if has_ref else ops[1:])]
    elif name == "truncate":
        out += [WriteTarget(w, name) for w in _operands(args, _VALUE_OPTS["truncate"])]
    elif name == "mv":
        t = _option_value(args, frozenset({"-t", "--target-directory"}))
        ops = _operands(args, _VALUE_OPTS["mv"])
        out += [WriteTarget(w, name) for w in ops] + ([WriteTarget(t, name)] if t else [])
    elif name in ("cp", "install", "ln"):
        t = _option_value(args, frozenset({"-t", "--target-directory"}))
        ops = _operands(args, _VALUE_OPTS[name])
        dest = [t] if t else (ops[-1:] if ops else [])
        out += [WriteTarget(w, name) for w in dest]
    elif name == "rsync":
        ops = [w for w in _operands(args, _VALUE_OPTS["rsync"]) if not _is_remote(w)]
        out += [WriteTarget(w, name) for w in ops[-1:]]
    elif name in ("sed", "perl", "ruby") and any(_is_in_place(a) for a in args):
        ops = _operands(args, frozenset({"-e", "-f", "--expression", "--file", "-E", "-l"}))
        has_script_opt = any(a.text in ("-e", "-f", "--expression", "--file") or a.text.startswith(("--expression=", "--file=")) for a in args)
        out += [WriteTarget(w, f"{name} -i") for w in (ops if has_script_opt else ops[1:])]
    elif name == "dd":
        for a in args:
            if a.text.startswith("of="):
                out.append(WriteTarget(Word(a.text[3:], dynamic=a.dynamic, glob=a.glob, quoted=a.quoted, subs=a.subs), "dd of="))
    elif name == "unzip":
        d = _option_value(args, frozenset({"-d"}))
        if d:
            out.append(WriteTarget(d, "unzip -d"))
    elif name == "git":
        out += [WriteTarget(w, "git") for w in git_directories(args)]
    elif name in ("source", "."):
        out += [WriteTarget(w, name) for w in args[:1]]  # not a write, but the same "must be a provable workspace path" rule applies
    return tuple(out)


def _is_in_place(w: Word) -> bool:
    t = w.text
    return t == "--in-place" or t.startswith("--in-place=") or (t.startswith("-") and not t.startswith("--") and "i" in t[1:] and t[1:].replace("i", "").replace("E", "").replace("n", "") == "")


def git_directories(args: tuple[Word, ...]) -> list[Word]:
    """Directories a git invocation is pointed at through its global options (`-C`, `--git-dir`, `--work-tree`)."""
    out: list[Word] = []
    i = 0
    while i < len(args):
        t = args[i].text
        if t in ("-C", "--git-dir", "--work-tree") and i + 1 < len(args):
            out.append(args[i + 1])
            i += 2
            continue
        for opt in ("--git-dir=", "--work-tree="):
            if t.startswith(opt):
                w = args[i]
                out.append(Word(t[len(opt):], dynamic=w.dynamic, glob=w.glob, quoted=w.quoted, subs=w.subs))
        if t in ("-c", "--namespace", "--exec-path") and i + 1 < len(args) and t != "--exec-path":
            i += 2
            continue
        if not t.startswith("-"):
            break
        i += 1
    return out


def check_path(word: Word, cwd: str | None, ctx: PathContext) -> str | None:
    """None when `word` provably names something inside the workspace (or a whitelisted root); otherwise the reason it is refused."""
    text = word.text
    if word.dynamic:
        return f"{text!r} is only known at run time, so it cannot be shown to stay inside the workspace"
    if not text:
        return "empty path"
    if text in SAFE_DEVICES:
        return None
    if text == "~" or text.startswith("~/"):
        if not ctx.home:
            return "the home directory is not known"
        text = ctx.home if text == "~" else posixpath.join(ctx.home, text[2:])
    elif text.startswith("~"):
        return f"{word.text!r}: another user's home directory is outside the workspace"
    if word.glob:
        cut = min((i for i in (text.find(c) for c in "*?[") if i >= 0), default=len(text))
        text = text[:cut].rsplit("/", 1)[0] if "/" in text[:cut] else "."
        text = text or "/"
    if not text.startswith("/"):
        if cwd is None:
            return f"{word.text!r} is relative to a working directory that is not known"
        text = posixpath.join(cwd, text)
    candidate = posixpath.normpath(text) if not word.glob else text
    roots = (ctx.workspace, *ctx.extra_roots)
    last: str | None = None
    for root in roots:
        try:
            resolved = resolve_contained(root, candidate)
        except ValueError as exc:
            last = str(exc)
            continue
        if is_protected_path(resolved):
            return f"{word.text!r} is inside a protected directory (hooks, git config)"
        return None
    return f"{word.text!r} is outside the workspace ({last})" if last else f"{word.text!r} is outside the workspace"


def check_tool_path(value: str, ctx: PathContext) -> str | None:
    """The same rule for a path given as a tool argument (relative paths resolve against the workspace)."""
    return check_path(Word(value), ctx.workspace, ctx)
