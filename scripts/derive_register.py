#!/usr/bin/env python3
"""Derive `register.md` — every exception the runtime can raise, its phase, and its sites.

**Why committed.** Same reason as its siblings (`CONTEXT.md` §0.0.9's finding **F75**):
*"an instrument that cannot be committed is not a re-runnable measurement."*

**What is derived and what is data.** The **definition site**, the **raise count**, the **first
raise site**, the **catch sites** and the **tripwire** are all read from the tree by an AST walk on
every run — never transcribed. Only the four judgements a walk cannot make are held here as data:
`base` (what the class inherits), `role` (what kind of signal it is), `phase` (where in the
lifecycle it fires) and `adr` (the decision that introduced it, cited only where the class's own
docstring names one).

**The totality property.** Every exception class defined under `wisp/`, `wisp_net/` or `agent/`
outside `tests/` gets a row, and every row names a real class. That is mechanical, so the guard
asserts it rather than trusting the table.

**Stdlib only.** Unlike its three siblings this generator imports no `wisp.*` module, so it runs
under any `python3` and needs no venv. Stated because the sibling commands all begin
`env -u PYTHONPATH .venv/bin/python` and this one must not.

Run: `python3 scripts/derive_register.py`
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "register.md"

#: The roots the totality property covers, and the directory names that are not the runtime.
ROOTS = ("wisp", "wisp_net", "agent")
SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "tests"}

#: The role vocabulary. Closed: every row's role is one of these, and the generator refuses
#: a row that invents a sixth.
ROLES = ("verdict", "recoverable", "guard", "fault", "unwired")

#: The phase vocabulary. Closed for the same reason. `—` is not a phase: an `unwired` class
#: has no firing point, and its row says so by carrying `unwired` in `role`.
PHASES = ("startup", "context", "turn", "provider", "tool", "approval", "plan",
          "subagent", "persist", "cli", "net", "observe", "bench")

#: `(name, defined_at, base, role, phase, adr)`.
#:
#: `defined_at` is the row's **identity** — not the name, because the tree defines
#: `SchemaValidationError` twice (§Findings). Two rows sharing a name are two rows.
#:
#: `adr` cites a decision **only where the class's own docstring names one**. An exception with
#: no stated provenance carries `—` rather than a plausible guess; guessing a decision is the
#: defect this corpus exists to prevent.
ROWS: list[tuple[str, str, str, str, str, str]] = [
    # ── Verdicts: a decision, not a fault. Raised by design in a healthy run. ──────────
    ("ExitREPL", "wisp/exceptions.py:13", "Exception", "verdict", "cli", "—"),
    ("ApprovalCancelled", "wisp/exceptions.py:17", "Exception", "verdict", "approval", "—"),
    ("ApprovalTimeout", "wisp/exceptions.py:33", "Exception", "verdict", "approval", "—"),

    # ── Recoverable: expected transient faults the runtime retries or degrades around. ──
    ("_TransientOpenError", "wisp/core/provider_stream.py:40", "Exception", "recoverable",
     "provider", "—"),
    ("FirstTokenTimeout", "wisp/multi_agent/_runner.py:151", "asyncio.TimeoutError",
     "recoverable", "subagent", "—"),
    ("CircuitOpenError", "wisp/infra/circuit_breaker.py:175", "Exception", "recoverable",
     "provider", "—"),
    ("CircuitBreakerOpenError", "wisp/multi_agent/_circuit_breaker.py:102", "Exception",
     "recoverable", "subagent", "—"),
    ("OllamaError", "wisp/ollama_client.py:46", "Exception", "recoverable", "provider", "—"),
    ("OllamaConfigurationError", "wisp/ollama_client.py:72", "OllamaError", "recoverable",
     "provider", "ADR-0038"),

    # ── Guards: a ceiling or a contract the host enforces on itself. ────────────────────
    ("BoundsError", "wisp/runtime/bounds.py:59", "RuntimeError", "guard", "startup", "—"),
    ("CostError", "wisp/runtime/cost.py:48", "RuntimeError", "guard", "turn", "—"),
    ("UnknownModel", "wisp/runtime/cost.py:54", "CostError", "guard", "turn", "—"),
    ("FleetManifestError", "wisp/fleet.py:28", "ValueError", "guard", "cli", "—"),
    ("IdempotencyError", "wisp/runtime/idempotency.py:57", "RuntimeError", "guard", "tool", "—"),
    ("KeyReuse", "wisp/runtime/idempotency.py:63", "IdempotencyError", "guard", "tool", "—"),
    ("UnstableKey", "wisp/runtime/idempotency.py:78", "IdempotencyError", "guard", "tool", "—"),
    ("StoreUnavailable", "wisp/runtime/idempotency.py:99", "IdempotencyError", "guard",
     "tool", "—"),
    ("RedactionPointError", "wisp/runtime/redaction.py:69", "RuntimeError", "guard",
     "observe", "—"),
    ("GrowthBudgetExceeded", "wisp/core/task_graph.py:600", "RuntimeError", "guard",
     "plan", "—"),
    ("TrustViolation", "wisp/core/context_trust.py:95", "RuntimeError", "guard", "context", "—"),
    ("ContextOverflow", "wisp/core/context_trust.py:104", "RuntimeError", "guard",
     "context", "—"),
    ("CriteriaDeclarationRejected", "wisp/core/convergence.py:673", "Exception", "guard",
     "turn", "ADR-0050"),
    ("ReplayDivergence", "wisp/core/replay_digest.py:64", "RuntimeError", "guard",
     "persist", "—"),

    # ── Faults: an error the caller must handle. The bulk. ─────────────────────────────
    ("ToolError", "wisp/tools/errors.py:8", "Exception", "fault", "tool", "—"),
    ("PlanError", "wisp/graph/planner.py:72", "Exception", "fault", "plan", "—"),
    ("LSPServerError", "wisp/lsp/client.py:24", "Exception", "fault", "tool", "—"),
    ("SearchReplaceError", "wisp/core/mutator/search_replace.py:38", "ValueError", "fault",
     "tool", "—"),
    ("JsonExtractionError", "wisp/structured_output.py:28", "RuntimeError", "fault",
     "provider", "—"),
    ("DockerUnavailable", "wisp/benchmark/adapters/docker_backend.py:35", "RuntimeError",
     "fault", "bench", "—"),
    ("ExportRefused", "wisp/trace/otlp.py:24", "Exception", "fault", "observe", "—"),

    # ── wisp_net: the network subsystem's own surface. ─────────────────────────────────
    ("AclError", "wisp_net/acl.py:30", "ValueError", "fault", "net", "—"),
    ("ApprovalError", "wisp_net/governance/control.py:37", "ValueError", "fault", "net", "—"),
    ("LedgerCorrupt", "wisp_net/governance/ledger.py:45", "ValueError", "fault", "net", "—"),
    ("PathError", "wisp_net/paths.py:13", "ValueError", "fault", "net", "—"),
    ("IntentError", "wisp_net/reasoning/intents.py:32", "ValueError", "fault", "net", "—"),
    ("ChangeError", "wisp_net/safety/change.py:21", "ValueError", "fault", "net", "—"),
    ("SetError", "wisp_net/sim/config.py:25", "ValueError", "fault", "net", "—"),
    ("DeviceUnreachable", "wisp_net/sim/network.py:48", "RuntimeError", "fault", "net", "—"),
    ("SyslogParseError", "wisp_net/telemetry/syslog.py:14", "ValueError", "fault", "net", "—"),

    # ── Unwired: declared, and nothing in the tree raises it. ──────────────────────────
    # The five below are the Phase-1 typed taxonomy in `core/contracts.py`. Its docstring says
    # it *replaces* the stringly path in `core/transport.py`; the stringly path is still there.
    ("WispError", "wisp/core/contracts.py:69", "Exception", "unwired", "—", "—"),
    ("TransientTransportError", "wisp/core/contracts.py:94", "WispError", "unwired", "—", "—"),
    ("FatalProviderError", "wisp/core/contracts.py:102", "WispError", "unwired", "—", "—"),
    ("ToolDeniedError", "wisp/core/contracts.py:110", "WispError", "unwired", "—", "—"),
    ("CancelledTurnError", "wisp/core/contracts.py:118", "WispError", "unwired", "—", "—"),
    ("LadderExhausted", "wisp/core/recovery.py:589", "RuntimeError", "unwired", "—", "—"),
    ("EventStreamError", "wisp/stream_parser.py:17", "Exception", "unwired", "—", "—"),
    ("SchemaValidationError", "wisp/structured_output.py:37", "RuntimeError", "unwired",
     "—", "—"),
    ("SchemaValidationError", "wisp/multi_agent/schema_validator.py:16", "Exception",
     "unwired", "—", "—"),
]

#: `(phase, [exception, ...])` — the order the exceptions can fire in one turn. Hand-ordered
#: because a walk cannot see sequence; the *membership* of each phase is checked against the
#: rows, so a row whose phase is not listed here is a derivation failure.
SEQUENCE: list[tuple[str, str]] = [
    ("startup", "`BoundsError` — the run's declared ceilings are read and validated before the "
                "first turn. The only exception on this page that fires before anything else can."),
    ("context", "`TrustViolation` and `ContextOverflow` — the assembler's two refusals, in that "
                "order: T1 (an untrusted item in instruction position) is checked before the "
                "budget (a protected item that does not fit). **Neither is reachable in the tree "
                "today** — see §Findings."),
    ("turn", "`CostError` / `UnknownModel` when a model's price is unknown and no policy covers "
             "it; `CriteriaDeclarationRejected` when an objective's declaration cannot be used."),
    ("plan", "`GrowthBudgetExceeded` bounds an expansion; `PlanError` covers compilation. "
             "Both precede dispatch."),
    ("approval", "`ApprovalTimeout` then `ApprovalCancelled` — a lapse and a verdict are "
                 "different facts and carry different denial statuses. Order between them is "
                 "not fixed; they are alternatives, not a sequence."),
    ("provider", "`_TransientOpenError` (an internal sentinel, caught in the same function that "
                 "raises it), then `CircuitOpenError` once the breaker trips, then "
                 "`OllamaError` / `OllamaConfigurationError`, then `JsonExtractionError` if the "
                 "stream's JSON cannot be extracted."),
    ("tool", "`ToolError` dominates — 78 raise sites. `IdempotencyError` and its three subclasses "
             "guard a repeat; `SearchReplaceError` and `LSPServerError` are the edit and "
             "language-server paths."),
    ("subagent", "`FirstTokenTimeout` (accepted, streamed nothing) then "
                 "`CircuitBreakerOpenError` — the runner's own breaker, distinct from the "
                 "provider's."),
    ("persist", "`ReplayDivergence` — a resumed session's journal does not replay consistently."),
    ("cli", "`ExitREPL` — `/exit`. Terminates the REPL rather than the turn."),
    ("net", "The `wisp_net` surface: parse-time (`PathError`, `AclError`, `IntentError`, "
            "`SetError`, `SyslogParseError`) before runtime (`DeviceUnreachable`, "
            "`LedgerCorrupt`, `ApprovalError`, `ChangeError`)."),
    ("observe", "`RedactionPointError` (a redaction at a point the project decided against) and "
                "`ExportRefused` (the tier forbids export)."),
    ("bench", "`DockerUnavailable` — the benchmark harness's setup path."),
]


def _tracked() -> set[str] | None:
    """The set of **tracked** paths, or `None` when git cannot answer.

    **The derivation must read the committed tree.** Until this existed, `_files()` and
    `_tripwires()` walked the working *directory*, so the page depended on files that are not in
    the repository: the `tripwire` column, the "named by no test file" figure and its list all
    moved when six untracked test files were present or absent. That made **R8's reproducibility
    promise false for this register** — a page derived from a developer's uncommitted work cannot
    be reproduced from a clone, and its guard failed on a page that was correct for the tree it
    was generated from.

    Found by running the guard with the untracked files temporarily set aside. `None` (git
    missing, or not a repository) falls back to the old walk — the page is then
    working-tree-dependent again, which is why `_check` reports it as a finding.
    """
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return {p for p in out.stdout.split("\0") if p}


def _files() -> list[tuple[str, ast.Module]]:
    out: list[tuple[str, ast.Module]] = []
    tracked = _tracked()
    for root in ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if any(p in SKIP_DIRS for p in path.parts):
                continue
            rel = path.relative_to(REPO).as_posix()
            if tracked is not None and rel not in tracked:
                continue          # untracked: not part of the committed tree
            try:
                out.append((rel, ast.parse(path.read_text(encoding="utf-8"))))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
    return out


def _scan() -> tuple[dict[tuple[str, int], str], dict[str, list[tuple[str, int]]],
                     dict[str, list[tuple[str, int]]]]:
    """`((path, line) -> name)`, `name -> raise sites`, `name -> catch sites`."""
    files = _files()
    defined: dict[tuple[str, int], str] = {}
    for rel, tree in files:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(b) for b in node.bases]
                if any("Error" in b or "Exception" in b for b in bases):
                    defined[(rel, node.lineno)] = node.name

    names = set(defined.values())
    raises: dict[str, list[tuple[str, int]]] = {n: [] for n in names}
    catches: dict[str, list[tuple[str, int]]] = {n: [] for n in names}
    for rel, tree in files:
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                nm = None
                if isinstance(node.exc, ast.Call):
                    nm = ast.unparse(node.exc.func).split(".")[-1]
                elif isinstance(node.exc, ast.Name):
                    nm = node.exc.id
                if nm in raises:
                    raises[nm].append((rel, node.lineno))
            elif isinstance(node, ast.ExceptHandler) and node.type is not None:
                items = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
                for t in items:
                    nm = ast.unparse(t).split(".")[-1]
                    if nm in catches:
                        catches[nm].append((rel, node.lineno))
    for d in (raises, catches):
        for v in d.values():
            v.sort()
    return defined, raises, catches


def _line_names(rel: str, line: int, name: str, *, kind: str) -> str | None:
    """`None` when the pin is sound, else why it is not."""
    path = REPO / rel
    if not path.exists():
        return f"{rel} does not exist"
    lines = path.read_text(encoding="utf-8").splitlines()
    if not 1 <= line <= len(lines):
        return f"{rel}:{line} is out of range ({len(lines)} lines)"
    if kind == "def":
        if f"class {name}" not in lines[line - 1]:
            return f"{rel}:{line} does not define class {name}"
    elif kind == "raise" and name not in lines[line - 1]:
        return f"{rel}:{line} does not name {name}"
    elif kind == "catch" and name not in lines[line - 1]:
        return f"{rel}:{line} does not name {name}"
    return None


def _tripwires(names: list[str]) -> dict[str, str]:
    """The first **tracked** test file naming each exception. Derived, not declared.

    A row with no tripwire is an exception nothing tests — a fact worth stating, so the
    column carries `—` rather than being omitted.

    **Tracked only.** This used to walk `tests/` on disk, so an untracked test file could
    supply a tripwire — and the page then disagreed with itself across checkouts. See `_tracked`.
    """
    tracked = _tracked()
    tests = [p for p in sorted((REPO / "tests").rglob("*.py"))
             if tracked is None or p.relative_to(REPO).as_posix() in tracked]
    text = {p: p.read_text(encoding="utf-8", errors="replace") for p in tests}
    out: dict[str, str] = {}
    for name in names:
        for p in tests:
            if name in text[p]:
                out[name] = p.relative_to(REPO).as_posix()
                break
    return out


def _check(defined, raises, catches) -> list[str]:
    broken: list[str] = []
    row_ids = {(r[1].rsplit(":", 1)[0], int(r[1].rsplit(":", 1)[1])): r[0] for r in ROWS}

    if len(row_ids) != len(ROWS):
        broken.append("two rows share a defined_at pin — a row's identity is its definition site")
    missing = sorted(f"{p}:{ln} ({n})" for (p, ln), n in defined.items()
                     if (p, ln) not in row_ids)
    if missing:
        broken.append(f"exception class(es) in the tree with no row: {missing}")
    for (p, ln), name in row_ids.items():
        actual = defined.get((p, ln))
        if actual is None:
            broken.append(f"{p}:{ln} is a row but defines no exception class")
        elif actual != name:
            broken.append(f"{p}:{ln} defines {actual}, the row says {name}")

    for name, pin, base, role, phase, adr in ROWS:
        if role not in ROLES:
            broken.append(f"{name}: role {role!r} is not in the vocabulary {ROLES}")
        if role == "unwired":
            # An unwired class has no firing point, so `—` is its phase and the phase
            # vocabulary does not apply. Stated as its own branch so the two rules cannot
            # be satisfied by accident.
            if phase != "—":
                broken.append(f"{name}: an unwired class has no firing point; phase must be `—`")
        elif phase not in PHASES:
            broken.append(f"{name}: phase {phase!r} is not in the vocabulary {PHASES}")
        for cell in (name, base, role, phase, adr, pin):
            if "|" in cell:
                broken.append(f"{name}: a cell contains `|`, which splits the markdown table")
        rel, _, num = pin.rpartition(":")
        problem = _line_names(rel, int(num), name, kind="def")
        if problem:
            broken.append(f"{name}: {problem}")
            continue
        # The declared base must be the tree's base. Either spelling is accepted: the row
        # may write `asyncio.TimeoutError` (what the source says) or `TimeoutError` (the
        # simple name), and the check normalises rather than forcing one form.
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.lineno == int(num):
                full = [ast.unparse(b) for b in node.bases]
                simple = [f.split(".")[-1] for f in full]
                if base not in full and base not in simple:
                    broken.append(f"{name}: the row says base {base}; the tree says "
                                  f"{full}")
                break
        # Every raise and catch site must name the class.
        for kind, sites in (("raise", raises[name]), ("catch", catches[name])):
            for srel, sline in sites:
                problem = _line_names(srel, sline, name, kind=kind)
                if problem:
                    broken.append(f"{name}: {kind} site {problem}")

    # A phase in SEQUENCE that no row carries, and a row whose phase SEQUENCE omits.
    listed = {p for p, _ in SEQUENCE}
    used = {r[4] for r in ROWS if r[4] != "—"}
    if used - listed:
        broken.append(f"row phase(s) missing from the sequence section: {sorted(used - listed)}")
    if listed - used:
        broken.append(f"sequence phase(s) with no row: {sorted(listed - used)}")
    if _tracked() is None:
        broken.append(
            "git could not list the tracked files, so this page was derived from the working "
            "DIRECTORY and is not reproducible from a clone (R8). Run it inside the repository.")
    return broken


def _head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.strip()


def render() -> str:
    defined, raises, catches = _scan()
    trip = _tripwires([r[0] for r in ROWS])
    by_name: dict[str, int] = {}
    for r in ROWS:
        by_name[r[0]] = by_name.get(r[0], 0) + 1
    dupes = sorted(n for n, c in by_name.items() if c > 1)
    untested = sorted({r[0] for r in ROWS if r[0] not in trip})

    L: list[str] = []
    A = L.append
    A("# register.md — every exception the runtime can raise, its phase, and its sites")
    A("")
    A("> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**")
    A("> Regenerate with `python3 scripts/derive_register.py` — **stdlib only**, so unlike its")
    A("> three siblings it needs no venv and no `env -u PYTHONPATH`.")
    A(">")
    A("> Every `defined_at`, every `raise_sites` count, every `caught_at` and every `tripwire`")
    A("> below is **read from the tree by an AST walk** at generation time, not from prose — a")
    A("> disagreement between this page and the tree fails the derivation. The four judgements a")
    A("> walk cannot make (`base`, `role`, `phase`, `adr`) are held in the generator as data.")
    A(">")
    A("> This page states the exception surface's **current** state. It **introduces no")
    A("> decision**: it raises nothing, catches nothing, and removes nothing. A claim that cannot")
    A("> be pinned is a §Findings entry, not a row.")
    A(">")
    A("> **Sibling registers:** `CURRENT_AUTHORITIES.md`, `CURRENT_FINDINGS.md`,")
    A("> `CURRENT_FLAGS.md`, `CURRENT_OPEN_ITEMS.md`. All five are derived; none may decide.")
    A("> This one is named `register.md` rather than `CURRENT_EXCEPTIONS.md` because that is the")
    A("> name the mission gave it; the convention it follows is theirs.")
    A(">")
    A("> **Scope.** `wisp/`, `wisp_net/` and `agent/`, with `tests/` excluded. A `caught_at` of")
    A("> `—` therefore means *no catch inside these roots*, which is **not** the same claim as")
    A("> *unhandled* — see §(c).")
    A(">")
    A(f"> Generated 2026-09-30 at `{_head_sha()}` · **{len(ROWS)} classes** · "
      f"**{sum(len(raises[r[0]]) for r in ROWS)} raise sites** · "
      f"**{sum(len(catches[r[0]]) for r in ROWS)} catch sites** · "
      f"**{len(untested)} with no test naming them**.")
    A("")
    A("---")
    A("")
    A("## The register")
    A("")
    A("| exception | base | role | phase | defined_at | raise_sites | first_raise | caught_at | adr |")
    A("|---|---|---|---|---|---|---|---|---|")
    for name, pin, base, role, phase, adr in ROWS:
        rs = raises[name]
        cs = catches[name]
        first = f"`{rs[0][0]}:{rs[0][1]}`" if rs else "—"
        caught = ", ".join(f"`{f}:{n}`" for f, n in cs) if cs else "—"
        A(f"| `{name}` | `{base}` | {role} | {phase} | `{pin}` | {len(rs)} | {first} | "
          f"{caught} | {adr} |")
    A("")
    A("`first_raise` is the **lowest-sorted** site, not the most important one — it is a")
    A("deterministic anchor, and the full list is one AST walk away in the generator.")
    A("")
    A("---")
    A("")
    A("## (a) The role vocabulary")
    A("")
    A("Five roles, closed. A row that invents a sixth fails the derivation.")
    A("")
    A("| role | what it means | why it is not the others |")
    A("|---|---|---|")
    A("| `verdict` | A **decision**, raised by design in a healthy run. `/exit`, a user's "
      "cancel, a lapsed prompt. | Not a fault: nothing is wrong. Not `recoverable`: there is "
      "nothing to retry — `ApprovalCancelled`'s docstring is explicit that it is *deliberately "
      "not* a `CancelledError`, and that genuine SIGINT must still propagate untouched. |")
    A("| `recoverable` | An **expected transient** fault the runtime retries or degrades around "
      "— a transport reset, a breaker, a provider that accepted the request and streamed "
      "nothing. | Not a `fault`: the caller does not have to handle it, the runtime already "
      "does. Not a `guard`: it is imposed by the world, not declared by the host. |")
    A("| `guard` | A **ceiling or contract the host enforces on itself** — a run bound, a cost "
      "ceiling, an idempotency row, a graph-growth budget, a replay digest. | Not a `fault`: "
      "it is the host refusing, not the world failing. `ContextOverflow` is the sharpest case: "
      "the assembler raises *because* the alternative — a silently shortened prompt — is worse. |")
    A("| `fault` | An error the caller must handle. The bulk. | |")
    A("| `unwired` | **Declared, and nothing in the tree raises it.** | Not a judgement about "
      "value: several are correct designs that were never adopted. The role exists so the page "
      "states the fact without deciding what to do about it. |")
    A("")
    A("---")
    A("")
    A("## (b) The firing sequence — one turn, in order")
    A("")
    A("A walk cannot see sequence, so this section is hand-ordered. Its **membership** is not:")
    A("the derivation refuses if a row's phase is absent here, or a phase here has no row.")
    A("")
    for phase, text in SEQUENCE:
        A(f"- **`{phase}`** — {text}")
    A("")
    A("**What the order is for.** Two exceptions that can both fire on one turn are not "
      "interchangeable if their *order* carries meaning. The clearest instance on this page is "
      "the `context` pair: `TrustViolation` is checked before `ContextOverflow`, and the "
      "source says the order *is* the point — a priority-0 item that did not fit used to be "
      "silently truncated, and the protected-item branch was placed above that truncation so "
      "the truncation can no longer reach a protected item.")
    A("")
    A("---")
    A("")
    A("## (c) The raise/catch asymmetry")
    A("")
    total_r = sum(len(raises[r[0]]) for r in ROWS)
    total_c = sum(len(catches[r[0]]) for r in ROWS)
    A(f"**{total_r} raise sites, {total_c} catch sites.** The asymmetry is large and it is "
      f"mostly not a defect: a library-style module raises and lets its caller decide, and the "
      f"caller is often outside the three roots this page covers.")
    A("")
    A("What the asymmetry **does** let this page state precisely is the zero:")
    A("")
    A("| | count |")
    A("|---|---|")
    A(f"| classes with at least one raise site | {sum(1 for r in ROWS if raises[r[0]])} |")
    A(f"| classes with **no** raise site | {sum(1 for r in ROWS if not raises[r[0]])} |")
    A(f"| classes with at least one catch site | {sum(1 for r in ROWS if catches[r[0]])} |")
    A(f"| classes with **no** catch site | {sum(1 for r in ROWS if not catches[r[0]])} |")
    A("")
    A("**A zero-raise class is the load-bearing number.** `caught_at: —` is weak evidence — it")
    A("may mean the caller is out of scope. `raise_sites: 0` is strong: an AST walk over the")
    A("whole runtime found no `raise` of that name anywhere, so the class cannot fire in this")
    A("tree at all.")
    A("")
    untested_rows = sum(1 for r in ROWS if r[0] not in trip)
    A(f"**{len(untested)} distinct names — {untested_rows} of {len(ROWS)} rows — are named by "
      f"no test file.** Derived by")
    A("searching `tests/` for each name, so it is a floor and not a proof: a test can exercise a")
    A("path without ever naming the exception. The list is a place to look, not a verdict.")
    A("")
    A("The name and row counts differ because `SchemaValidationError` is defined twice "
      "(§Findings); a name-keyed count would say 8 and a row-keyed count 9, and only the pair "
      "is honest.")
    A("")
    if untested:
        A(", ".join(f"`{n}`" for n in untested))
        A("")
    A("---")
    A("")
    A("## §Findings — what this page could not pin")
    A("")
    for text in _findings(len(untested)):
        A(text)
        A("")
    A("### What this page did not do")
    A("")
    A("- **No exception added, removed, or re-based.** The register's totality is asserted, not")
    A("  extended.")
    A("- **No finding repaired.** Every §Findings entry below is recorded. Each repair is a")
    A("  behaviour change, and several touch a published vocabulary (ADR-0052's blast radius),")
    A("  which makes them decisions rather than mechanical edits.")
    A("- **No ADR.** Nothing here surfaced a conflict that requires one; each finding names the")
    A("  decision that would.")
    A("")
    return "\n".join(L)


def _findings(untested: int) -> list[str]:
    """The §Findings entries. `untested` is passed in so the count is computed once.

    It was a module-level list with its own `_tripwires(...)` call, and that copy counted
    `SchemaValidationError` **twice** — the two definitions share a name — so §(c) said 8 and
    §Findings said 9. One number, one computation.
    """
    return [
    "- **The typed error taxonomy is declared and unwired — the largest single finding on this "
    "page.** `wisp/core/contracts.py` defines `ErrorKind` (ten members) and `WispError` with four "
    "subclasses. Its own module docstring says it *\"Replaces: stringly `error_event(code, hint)` "
    "+ substring matching in `wisp/core/transport.py:is_transient_error` + bare `except "
    "BaseException`\"* (debt IDs **D2**, **D8**). Measured: **all five classes have zero raise "
    "sites and zero catch sites**, and a search for the four subclass names across `wisp/` "
    "excluding `contracts.py` returns **no matches at all** — nothing imports them either. The "
    "stringly path the docstring says it replaced is still present. This is the Phase-10 "
    "unwired-control class: a Phase-1 contract frozen as an interface and never adopted. **The "
    "claim is true of the target model and false of the tree**, which is why it is a finding "
    "rather than a comment. *A repair is a migration, not an edit — it needs the decision that "
    "owns D2/D8.*",

    "- **`LadderExhausted` appears exactly once in the repository — at its own definition.** Not "
    "raised, not caught, not imported, not named in any test. Its docstring says *\"Raised when a "
    "rung is requested and none is available\"*, and the exhaustion it describes is real — but "
    "`RecoveryLadder` signals it a different way: `decide()` returns `self.escalate(...)` when no "
    "candidate rung remains, and `escalate()` returns a `RecoveryDecision`, sets `self.escalated`, "
    "and the `ladder_state` property returns `\"ESCALATED_TO_HUMAN\"`. The module contradicts "
    "itself: `escalate`'s own docstring reads *\"Terminal honesty: exhaustion produces a STATE, "
    "not a hang.\"* **The state design is the better one** — a returned decision cannot be "
    "accidentally swallowed the way a raise can — so this is a superseded class that was never "
    "removed, and a docstring that still describes the mechanism it replaced.",

    "- **`TrustViolation` and `ContextOverflow` are unreachable in production, and their "
    "docstrings promise a caller that does not exist.** Both raises sit inside "
    "`wisp/core/context_trust.py::assemble`. Measured: **`assemble` has no production importer.** "
    "The module's only production importer is `wisp/context_assembler.py`, and it takes "
    "`TrustTag` and `TRUSTED_TAGS` — the tag vocabulary — not the assembler. Three test files "
    "import the module; no runtime module calls `assemble`. `ContextOverflow`'s docstring is "
    "unusually explicit about the contract — *\"the assembler **raises**, and the caller decides: "
    "shrink the context, raise the budget, or fail the turn\"* — and the measured state is that "
    "the raise is correct and **there is no caller to decide**. The two refusals are well-argued "
    "and currently unenforced; whether the assembler should be wired in or the module retired is "
    "a decision, and this page takes neither.",

    "- **A crashed approval handler is published as `DENIAL_USER_DENIED`.** In "
    "`wisp/core/approval_gate.py` the handler call is wrapped by four handlers: the cancellation "
    "quad re-raises untouched, `ApprovalCancelled` and `ApprovalTimeout` each return their own "
    "denial — and the generic `except Exception` **logs and falls through** to the block's final "
    "`return`, which stamps `denial=DENIAL_USER_DENIED`. So *\"the handler raised\"* and *\"the "
    "human said no\"* are published under one code. That is the same collapse "
    "`tool_executor.py:883-913` was fixed for: that site now emits `DENIAL_NO_APPROVER` and its "
    "comment states the rule — *\"Nobody could be asked\" and \"the human said no\" are different "
    "facts.* ADR-0061 R4 states it for the WebSocket path. **This site was not covered by that "
    "repair**, and it is live: `ApprovalGate` is imported by `wisp/core/stateless.py`. The fix is "
    "the same shape as the one already applied — a distinct code, not a distinct message. *Note "
    "the mis-attribution is the F8 defect class from the other side: a system failure reported as "
    "a human verdict.*",

    "- **`SchemaValidationError` is defined twice, and the registry cannot see it.** "
    "`wisp/multi_agent/schema_validator.py:16` subclasses `Exception`; "
    "`wisp/structured_output.py:37` subclasses `RuntimeError`. Two classes, one name, two bases, "
    "no relation. The second has zero raise sites and zero catch sites. **This is also a finding "
    "about the instrument**: the first draft of the scanner keyed its registry by class *name* "
    "and reported **46** classes where the tree has **47** — a name-keyed scan silently merges "
    "the second definition into the first. This page keys rows by *definition site* so the "
    "totality check cannot repeat that error, and the derivation now refuses two rows sharing a "
    "pin. *A scanner that reports 46 has not shown the tree holds 46.*",

    "- **`EventStreamError` is caught twice and raised never.** Two `except EventStreamError` "
    "clauses in `wisp/stream_parser.py::parse_stream` (at `:191` and `:197`), zero raise sites, "
    "and no importer outside the module. A handler for a signal the tree cannot produce — either "
    "a removed raise or one that was always intended to come from a dependency. Recorded, not "
    "resolved: which of the two it is cannot be settled from the tree alone.",

    "- **Two denial statuses are emitted but never classified.** `wisp/core/events.py`'s "
    "`_DENIAL_STATUSES` has **seven** members; `OUTCOME_BY_STATUS` has only the **original "
    "five** plus `ok` and `error`. `DENIAL_BUDGET_EXCEEDED` and `DENIAL_NO_APPROVER` were added "
    "to the first and never to the second, so `classify_status(\"BUDGET_EXCEEDED\")` returns "
    "`OutcomeClass.UNKNOWN` and `is_terminal_outcome` returns **`False`** — a verdict that by "
    "construction must not be retried reads as retryable — while `is_denial_text` on the same "
    "value returns `True`. One status, two answers: the \"second classifier\" failure "
    "`events.py:330-333` warns about in its own comment. Both are emitted live "
    "(`tool_executor.py:726-732` and `:900-905`). **The guards cannot catch it because they pin "
    "the same five**: `tests/test_outcome_classification_authority.py:53` and "
    "`wisp/core/recovery.py:152` each hard-code the original list, so a guard whose subject is a "
    "*copy* of the vocabulary cannot see the vocabulary grow. *The repair is two lines and the "
    "decision is not — `OUTCOME_BY_STATUS` is a published vocabulary, ADR-0052's blast radius.*",

    "- **The circuit-breaker authority is duplicated.** Two `CircuitBreakerConfig` classes and "
    "two `CircuitBreaker` classes exist — `wisp/infra/circuit_breaker.py` and "
    "`wisp/multi_agent/_circuit_breaker.py` — with two exception types for one concept, "
    "`CircuitOpenError` and `CircuitBreakerOpenError`. The provider path imports the first "
    "(`wisp/core/stateless.py:53`); the subagent runner uses the second. Two implementations of "
    "one concern is the shape this corpus has a standing name for, and the two exception names "
    "make it visible from the outside: **a caller cannot write one handler that covers both.** "
    "*Recorded; unifying them is a behaviour change with its own decision.*",

    "- **`ReplayDivergence`'s docstring states an absolute the tree does not keep.** The class "
    "docstring says a divergence *\"must **escape** the loop, not be caught and reported as one "
    "more way a run can end\"*. It **is** caught — at `wisp/core/runtime.py:1841`, inside "
    "`_recover_unfinished_turn`. The catch is deliberate and its own comment documents it as a "
    "repair: the divergence used to be swallowed by a bare `except Exception: pass`, so the log "
    "claimed a replay that never happened. **The code is right and the docstring is stale**: what "
    "must escape is the *turn loop*, not every handler. The catch does not reconcile silently — "
    "it discards the journal and says why. *A docstring absolute that the tree deliberately "
    "narrows is a trap for the next reader; the sentence should name the loop.*",

    "- **`adr` is `—` for 45 of 47 rows, and that is a limit, not a claim.** Only two rows "
    "carry a decision, and each is cited because the class's **own docstring** names it — "
    "`ADR-0038` for `OllamaConfigurationError`, `ADR-0050` for `CriteriaDeclarationRejected`. "
    "Most of these exceptions arrived in a phase whose ADR exists but does not name the class. "
    "**The provenance was not traced**, and a plausible ADR is worse than a blank — guessing a "
    "decision is the defect this corpus exists to prevent. `—` states *not pinned*, not *none "
    "exists*.",

    "- **`tripwire` is derived, and a derived tripwire is weaker than a declared one.** The "
    "column is the first `tests/` file that names the exception, found by search. It shows what "
    "is *referenced*, not what is *asserted*: a test that imports a name and never exercises the "
    "raise still counts. It is included because the zero is informative — "
    f"{untested} of {len(ROWS)} classes are named by no test file at all — and it is labelled "
    "derived so nobody reads it as a guard. **The number is a floor.** `LSPServerError` has 16 "
    "raise sites and no test names it; that is a live path with no assertion on its failure "
    "shape, and it is the kind of zero this column exists to surface.",
]


def main() -> int:
    defined, raises, catches = _scan()
    problems = _check(defined, raises, catches)
    if problems:
        print("DERIVATION REFUSED — the data table is not sound:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(ROWS)} classes, "
          f"{sum(len(raises[r[0]]) for r in ROWS)} raise sites, "
          f"{sum(len(catches[r[0]]) for r in ROWS)} catch sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
