"""Layer 2: command interception. Pure.

INVARIANT C1: a command line is refused when it contains, anywhere a shell would run it (inside `&&`/`||`/`;`/`|` chains, groups,
`$(…)`, backticks, `sh -c`, `xargs`, `find -exec`), a command whose effect cannot be undone; and it is refused when the parser or
walker cannot say what runs (fail closed). The rules read the parse tree, never the raw string, so quoting and chaining cannot move
a command out of position.

Irreversible means: recursive or bulk deletion, discarding uncommitted or committed work (`git reset --hard`, `clean -f`, forced or
deleting pushes, `checkout --`, `restore`), destroying a filesystem or device, privilege escalation, and executing code fetched at
run time. An interpreter given inline code (`python -c`) is not parsed: bounding what it does is the sandbox's job, and the path
layer still checks every shell-visible write.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from wisp.core.gates.invocations import PRIVILEGE, SHELLS, Invocation, Problem, extract
from wisp.core.gates.paths import SAFE_DEVICES, write_targets
from wisp.core.gates.shellparse import Seq, Word

NETWORK_FETCH = frozenset({"curl", "wget", "fetch", "nc", "ncat", "netcat", "telnet", "socat", "http", "https", "xh", "aria2c"})
INTERPRETERS = frozenset({"python", "python3", "python2", "perl", "ruby", "node", "deno", "bun", "php", "lua", "osascript", "pwsh", "powershell"})
EXEC_CONTENT = SHELLS | INTERPRETERS | frozenset({"source", ".", "xargs"})
_DISK_COMMANDS = frozenset({"mkswap", "mke2fs", "fdisk", "sfdisk", "gdisk", "sgdisk", "cfdisk", "parted", "wipefs", "blkdiscard", "format", "shred", "newfs", "mkfs", "fsck.ext4", "tune2fs", "cryptsetup", "pvcreate", "vgremove", "lvremove", "zpool", "zfs"})
_DISKUTIL_DESTRUCTIVE = frozenset({"erasedisk", "erasevolume", "partitiondisk", "reformat", "secureerase", "zerodisk", "randomdisk", "deletevolume", "eraseall", "resizevolume", "splitpartition", "mergepartitions", "apfs"})
_GIT_GLOBAL_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env"})
_GIT_CODE_EXEC_KEYS = ("core.hookspath", "core.fsmonitor", "core.sshcommand", "core.pager", "core.editor", "alias.", "credential.helper", "diff.external", "gpg.program", "protocol.ext.allow")


@dataclass(frozen=True)
class Violation:
    layer: str
    rule: str
    reason: str

    def render(self) -> str:
        return f"[{self.layer}:{self.rule}] {self.reason}"


def _flags(args: tuple[Word, ...]) -> set[str]:
    out: set[str] = set()
    for a in args:
        if a.text == "--":
            break
        if a.text.startswith("--"):
            out.add(a.text.split("=", 1)[0])
        elif a.text.startswith("-") and a.text != "-":
            out.update("-" + ch for ch in a.text[1:])
    return out


def _git_sub(args: tuple[Word, ...]) -> tuple[str, tuple[Word, ...], list[str]]:
    """(subcommand, its arguments, `-c key=value` settings) after git's global options."""
    i = 0
    configs: list[str] = []
    while i < len(args):
        t = args[i].text
        if t == "-c" and i + 1 < len(args):
            configs.append(args[i + 1].text)
            i += 2
        elif t in _GIT_GLOBAL_VALUE_OPTS and i + 1 < len(args):
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            return t, args[i + 1:], configs
    return "", (), configs


def _git_violations(inv: Invocation) -> list[Violation]:
    sub, args, configs = _git_sub(inv.args)
    out: list[Violation] = []
    for c in configs:
        key = c.split("=", 1)[0].lower()
        if any(key.startswith(k) for k in _GIT_CODE_EXEC_KEYS):
            out.append(Violation("command", "GIT_CODE_EXECUTION", f"`git -c {key}=…` makes git run a command of its choosing"))
    f = _flags(args)
    operands = [a.text for a in args if not a.text.startswith("-")]
    why: str | None = None
    if sub == "reset" and (f & {"--hard", "--merge", "--keep"}):
        why = "`git reset --hard` discards uncommitted work for good"
    elif sub == "clean" and ("-f" in f or "--force" in f):
        why = "`git clean -f` deletes untracked files for good"
    elif sub == "checkout" and ("--" in [a.text for a in args] or f & {"-f", "--force", "-B"} or operands == ["."]):
        why = "`git checkout -- <paths>` / `-f` overwrites uncommitted changes"
    elif sub == "restore" and not (f & {"--staged", "-S"} and not f & {"--worktree", "-W"}):
        why = "`git restore` overwrites uncommitted changes"
    elif sub == "push" and (f & {"--force", "-f", "--force-with-lease", "--delete", "-d", "--mirror", "--prune"} or any(o.startswith((":", "+")) for o in operands)):
        why = "a forced or deleting `git push` rewrites or removes published history"
    elif sub == "branch" and (f & {"-D", "-f", "--force"} or (f & {"--delete"} and f & {"--force"})):
        why = "`git branch -D/-f` discards a branch's commits"
    elif sub == "stash" and operands[:1] in (["drop"], ["clear"]):
        why = "`git stash drop/clear` discards stashed work for good"
    elif sub == "reflog" and operands[:1] in (["expire"], ["delete"]):
        why = "`git reflog expire/delete` removes the history that recovers lost commits"
    elif sub in ("gc", "prune") and (sub == "prune" or any(a.text.startswith("--prune=") and a.text[8:] in ("now", "all") for a in args)):
        why = "pruning unreachable objects removes the last copy of lost work"
    elif sub in ("filter-branch", "filter-repo", "replace"):
        why = f"`git {sub}` rewrites history"
    elif sub == "update-ref" and f & {"-d"}:
        why = "`git update-ref -d` deletes a ref"
    elif sub == "worktree" and operands[:1] == ["remove"] and f & {"-f", "--force"}:
        why = "`git worktree remove --force` discards a worktree's changes"
    if why:
        out.append(Violation("command", "IRREVERSIBLE_GIT", why))
    return out


def _rm_violations(inv: Invocation) -> list[Violation]:
    f = _flags(inv.args)
    if f & {"-r", "-R", "--recursive", "--no-preserve-root"}:
        return [Violation("command", "IRREVERSIBLE_DELETE", "recursive deletion (`rm -r`) cannot be undone; remove named files instead")]
    if inv.unbounded_args:
        return [Violation("command", "UNBOUNDED_DELETE", f"`{' '.join(inv.via) or 'a wrapper'}` feeds `rm` operands that are only known at run time, so what it deletes cannot be bounded")]
    return []


def _find_violations(inv: Invocation) -> list[Violation]:
    if any(a.text == "-delete" for a in inv.args):
        return [Violation("command", "IRREVERSIBLE_DELETE", "`find -delete` deletes everything it matches")]
    return []


def _device_violations(inv: Invocation) -> list[Violation]:
    name = inv.name
    out: list[Violation] = []
    if name in _DISK_COMMANDS or name.startswith(("mkfs", "newfs")):
        out.append(Violation("command", "DESTROYS_STORAGE", f"`{name}` creates or erases filesystems and partitions"))
    elif name == "diskutil" and inv.args and (inv.args[0].text.lower() in _DISKUTIL_DESTRUCTIVE or inv.args[0].text.lower().startswith(("erase", "partition", "secure", "zero", "random"))):
        out.append(Violation("command", "DESTROYS_STORAGE", f"`diskutil {inv.args[0].text}` erases or repartitions a disk"))
    elif name == "dd":
        for a in inv.args:
            if a.text.startswith("of=/dev/") and a.text[3:] not in SAFE_DEVICES:
                out.append(Violation("command", "DESTROYS_STORAGE", "`dd of=/dev/…` overwrites a device"))
    elif name in ("mv", "cp", "tee", "install", "ln"):
        for t in write_targets(inv):
            if t.word.text.startswith("/dev/") and t.word.text not in SAFE_DEVICES:
                out.append(Violation("command", "DESTROYS_STORAGE", f"`{name}` writes to a device ({t.word.text})"))
    for t in write_targets(inv):
        if t.via.startswith("redirect") and t.word.text.startswith("/dev/") and t.word.text not in SAFE_DEVICES:
            out.append(Violation("command", "DESTROYS_STORAGE", f"redirecting output to a device ({t.word.text}) overwrites it"))
    if name == "mv" and any(a.text == "/dev/null" for a in inv.args[-1:]):
        out.append(Violation("command", "IRREVERSIBLE_DELETE", "`mv … /dev/null` deletes the source"))
    return out


def _contains_network(seq: Seq) -> bool:
    invs, _problems = extract(seq, None)
    return any(i.name in NETWORK_FETCH for i in invs)


def _remote_exec_violations(inv: Invocation) -> list[Violation]:
    name = inv.name
    if name not in EXEC_CONTENT or name == "xargs":
        return []
    out: list[Violation] = []
    reads_stdin = False
    if name in SHELLS:
        reads_stdin = inv.inline_shell is None and not any(not a.text.startswith("-") for a in inv.args) and not any(r.op in ("<<", "<<-", "<<<") for r in inv.redirects)
    elif name in INTERPRETERS:
        reads_stdin = not inv.args or inv.args[0].text == "-"
    if reads_stdin and any(e in NETWORK_FETCH for e in inv.earlier):
        out.append(Violation("command", "REMOTE_CODE_EXECUTION", f"piping a download into `{name}` runs code that was never reviewed"))
    elif reads_stdin and inv.earlier and not any(r.op in ("<<", "<<-", "<<<") for r in inv.redirects):
        out.append(Violation("command", "STDIN_SCRIPT", f"`{name}` is fed a program through a pipe, which hides it from review (decode-and-run, `echo … | sh`)"))
    for a in inv.args:
        if any(_contains_network(s) for s in a.subs):
            out.append(Violation("command", "REMOTE_CODE_EXECUTION", f"`{name}` is given the output of a download as its program"))
            break
    return out


def invocation_violations(inv: Invocation) -> list[Violation]:
    if not inv.argv:
        return [v for v in _device_violations(inv)]
    name = inv.name
    out: list[Violation] = []
    escalated = [w for w in inv.via if w in PRIVILEGE]
    if name in PRIVILEGE or escalated:
        who = name if name in PRIVILEGE else escalated[0]
        out.append(Violation("command", "PRIVILEGE_ESCALATION", f"`{who}` runs a command with elevated rights"))
    if name == "rm":
        out += _rm_violations(inv)
    elif name == "find":
        out += _find_violations(inv)
    elif name == "git":
        out += _git_violations(inv)
    elif name == ":" and inv.stages >= 2 and inv.earlier and all(e == ":" for e in inv.earlier):
        out.append(Violation("command", "FORK_BOMB", "a pipeline of `:` calling itself is a fork bomb"))
    out += _device_violations(inv)
    out += _remote_exec_violations(inv)
    return out


def problem_violations(problems: tuple[Problem, ...]) -> list[Violation]:
    return [Violation("command", p.rule, p.reason) for p in problems]


def posix_name(inv: Invocation) -> str:
    return posixpath.basename(inv.argv[0].text) if inv.argv else ""
