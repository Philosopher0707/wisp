"""The harness gate: one function from a tool call to a decision. Pure and deterministic.

    check_tool_call(name, args, ctx, mutating=…)  ->  Decision(allowed, violations)

INVARIANTS
  G1  Deterministic: the decision is a function of (name, args, ctx) and the filesystem's symlinks. No clock, randomness, network
      or environment reads happen here (tests/gates/test_purity.py enforces the imports).
  G2  Fail closed: if any layer raises, the decision is a violation (`GATE_ERROR`), never an allow.
  G3  One enforcement point: the engine calls this once per tool call before dispatch (wisp/core/stateless.py), and every layer's
      refusal is expressed as a `Violation`, so there is one place to read, log and test the policy.
  G4  A typo never weakens the policy: an unknown mode is `enforce`, an unknown lock value is `locked`.
  G5  The operator, not the model, sets the policy: `GateContext` is built from configuration, never from tool arguments.
"""

from __future__ import annotations

import posixpath
import os
from dataclasses import dataclass
from enum import Enum

from wisp.core.gates import secrets as secret_gate
from wisp.core.gates.commands import NETWORK_FETCH, Violation, invocation_violations, problem_violations
from wisp.core.gates.deps import check_install, is_manifest_path
from wisp.core.gates.invocations import Invocation, extract
from wisp.core.gates.paths import PathContext, check_path, check_tool_path, write_targets
from wisp.core.gates.shellparse import parse

COMMAND_TOOLS = frozenset({"run_bash", "exec_sandbox"})
PATH_ARGS = ("path", "new_path", "dest", "target")
_NETWORK_COPY = frozenset({"scp", "rsync", "sftp", "ftp", "ssh", "ssh-copy-id"})
_SENSITIVE_BASENAMES = frozenset({".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", "credentials", ".dockercfg", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "secrets.json", "secrets.yaml", "secrets.yml", "service-account.json"})
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".kdbx", ".ppk")


class GateMode(str, Enum):
    ENFORCE = "enforce"
    OBSERVE = "observe"
    OFF = "off"


def parse_mode(value: object) -> GateMode:
    """Unknown or malformed values mean ENFORCE: a typo must never weaken the policy (G4)."""
    text = str(value or "").strip().lower()
    for mode in GateMode:
        if text == mode.value:
            return mode
    return GateMode.ENFORCE


def parse_lock(value: object) -> bool:
    """True (locked) unless the operator wrote exactly `unlocked` (G4)."""
    return str(value or "").strip().lower() != "unlocked"


@dataclass(frozen=True)
class GateContext:
    workspace: str
    home: str = ""
    mode: GateMode = GateMode.ENFORCE
    deps_locked: bool = True
    extra_write_roots: tuple[str, ...] = ()

    @property
    def paths(self) -> PathContext:
        return PathContext(self.workspace, self.home, self.extra_write_roots)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    violations: tuple[Violation, ...] = ()
    mode: GateMode = GateMode.ENFORCE

    @property
    def observed_only(self) -> bool:
        """Violations were found but the mode is `observe`, so the call proceeds."""
        return bool(self.violations) and self.allowed

    def render(self) -> str:
        lines = "; ".join(v.render() for v in self.violations)
        return f"[Blocked by the harness gate: {lines}. Nothing was run. Choose a narrower, reversible way to do this.]"


def is_sensitive_file(path: str) -> bool:
    base = posixpath.basename(path.rstrip("/")).lower()
    return base in _SENSITIVE_BASENAMES or base.startswith(".env.") or base.endswith(_SENSITIVE_SUFFIXES) or base.startswith(("id_rsa", "id_ed25519", "id_ecdsa"))


def _network_violations(inv: Invocation) -> list[Violation]:
    name = inv.name
    if name not in NETWORK_FETCH and name not in _NETWORK_COPY:
        return []
    out: list[Violation] = []
    words = [a.text for a in inv.args]
    for r in inv.redirects:
        if r.heredoc:
            words.append(r.heredoc)
        if r.op == "<<<" and r.target is not None:
            words.append(r.target.text)
        if r.op == "<" and r.target is not None and is_sensitive_file(r.target.text):
            out.append(Violation("secret", "SENSITIVE_FILE_EXFILTRATION", f"`{name}` is given the contents of {posixpath.basename(r.target.text)} on stdin"))
    for text in words:
        kinds = secret_gate.high_confidence_kinds(text)
        if kinds:
            out.append(Violation("secret", "SECRET_IN_NETWORK_COMMAND", f"a {kinds[0]} appears in the arguments of `{name}`"))
            break
    for i, text in enumerate(words):
        candidate = text[1:] if text.startswith("@") else (words[i + 1] if text in ("-T", "--upload-file") and i + 1 < len(words) else "")
        if candidate and is_sensitive_file(candidate.split("=", 1)[-1] if "=@" in candidate else candidate):
            out.append(Violation("secret", "SENSITIVE_FILE_EXFILTRATION", f"`{name}` would upload {posixpath.basename(candidate)}"))
        if "=@" in text and is_sensitive_file(text.split("=@", 1)[1]):
            out.append(Violation("secret", "SENSITIVE_FILE_EXFILTRATION", f"`{name}` would upload {posixpath.basename(text.split('=@', 1)[1])}"))
    if name in _NETWORK_COPY and any(is_sensitive_file(w) for w in words if not w.startswith("-")):
        out.append(Violation("secret", "SENSITIVE_FILE_EXFILTRATION", f"`{name}` would copy a credentials file off the machine"))
    for prior, prior_args in zip(inv.earlier, inv.earlier_args):
        if prior in ("cat", "head", "tail", "less", "more", "base64", "xxd", "tar", "zip", "gzip", "openssl", "cp") and any(is_sensitive_file(a) for a in prior_args if not a.startswith("-")):
            out.append(Violation("secret", "SENSITIVE_FILE_EXFILTRATION", f"a credentials file is piped into `{name}`"))
            break
    return out


def check_command(command: str, ctx: GateContext) -> tuple[Violation, ...]:
    parsed = parse(command)
    if not parsed.ok or parsed.tree is None:
        return (Violation("command", "UNPARSABLE", "; ".join(parsed.reasons) + " (the command cannot be analysed, so it is refused)"),)
    cwd = os.path.realpath(ctx.workspace) if ctx.workspace else None
    invocations, problems = extract(parsed.tree, cwd, ctx.home)
    found: list[Violation] = list(problem_violations(problems))
    for inv in invocations:
        found += invocation_violations(inv)
        if ctx.deps_locked:
            reason = check_install(inv, ctx.workspace)
            if reason:
                found.append(Violation("dependency", "DEPENDENCY_LOCKED", reason))
        for target in write_targets(inv):
            problem = check_path(target.word, inv.cwd, ctx.paths)
            if problem:
                found.append(Violation("path", "OUTSIDE_WORKSPACE", f"{target.via}: {problem}"))
            elif ctx.deps_locked and target.via not in ("source", ".", "git") and is_manifest_path(target.word.text):
                found.append(Violation("dependency", "MANIFEST_LOCKED", f"{target.via} would change {posixpath.basename(target.word.text)}, a dependency manifest"))
        found += _network_violations(inv)
    return _unique(found)


def check_tool_args(name: str, args: dict[str, object], ctx: GateContext) -> tuple[Violation, ...]:
    found: list[Violation] = []
    for key in PATH_ARGS:
        value = args.get(key)
        if not isinstance(value, str) or not value:
            continue
        problem = check_tool_path(value, ctx.paths)
        if problem:
            found.append(Violation("path", "OUTSIDE_WORKSPACE", f"{name} {key}: {problem}"))
        elif ctx.deps_locked and is_manifest_path(value):
            found.append(Violation("dependency", "MANIFEST_LOCKED", f"{name} would change {posixpath.basename(value)}, a dependency manifest"))
    return _unique(found)


def _unique(violations: list[Violation]) -> tuple[Violation, ...]:
    return tuple(sorted(set(violations), key=lambda v: (v.layer, v.rule, v.reason)))


def check_tool_call(name: str, args: object, ctx: GateContext, *, mutating: bool) -> Decision:
    """Decide one tool call. Never raises (G2)."""
    if ctx.mode is GateMode.OFF:
        return Decision(True, (), ctx.mode)
    try:
        found: tuple[Violation, ...] = ()
        arg_map = args if isinstance(args, dict) else {}
        if name in COMMAND_TOOLS:
            command = arg_map.get("command")
            if not isinstance(command, str):
                found = (Violation("command", "NO_COMMAND", "the command argument is missing or not text"),)
            else:
                found = check_command(command, ctx)
        elif mutating:
            found = check_tool_args(name, arg_map, ctx)
    except Exception as exc:  # noqa: BLE001 — fail closed: a gate that errors must refuse, never allow
        found = (Violation("gate", "GATE_ERROR", f"the gate could not decide ({type(exc).__name__}), so the call is refused"),)
    allowed = not found or ctx.mode is GateMode.OBSERVE
    return Decision(allowed, found, ctx.mode)


def scrub_result_text(text: str) -> secret_gate.ScrubResult:
    """Layer 3 on the way back: what a tool printed, before the model sees it."""
    return secret_gate.scrub(text)
