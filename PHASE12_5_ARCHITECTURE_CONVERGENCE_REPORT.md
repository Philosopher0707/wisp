# PHASE 12.5 — ARCHITECTURE CONVERGENCE REPORT

## 1. Baseline

112,173 core Python code LOC; two graph runtimes (legacy `core/agentic_graph`
+ canonical `wisp/graph/`); 8-module arena/server SCC; 3 containment impls;
30-branch `__main__` elif chain + Dispatcher + legacy registry; key-mask-only
JSONL redaction beside pattern-based SQLite/tool redaction.

## 2–4. Two-graph analysis + decision + compatibility

Discovery proved: **nothing outside tests instantiates GraphRunner**;
`runtime.py` autonomous mode wrote a dead `session["graph_state"]` nobody
read; `doctor` source-inspects legacy; config doc stale. Decision:
**QUARANTINE** (not delete, not migrate-execution): deprecation headers
with no-new-consumers/no-new-features/removal-condition terms on all three
files; `wisp/core` re-export removed (zero users); dead write removed;
doc fixed. Semantic diff legacy-vs-canonical documented in §2 table of the
report history: level-barrier scheduler vs ready-queue, whole-pattern retry
vs smallest-unit retry, prompt-injected deps vs typed edges — divergence is
exactly why quarantine (not compat-shim) was chosen.

## 5. Arena/server cycle

All back-edges were deferred; the single common edge E3 obscured four
upward violations (`arena/background_agent/routes.diff/routes.review →
wisp.entry::run_headless`). Fix: extracted `run_headless` verbatim to
`wisp/headless.py` (same CompositionRoot/HeadlessTransport/cache
semantics); `entry` re-exports; 7 production call sites + 3 test patch
targets migrated. Tarjan: 5 SCCs → 4, the 8-module one gone. Direction now
`entry → server.main → routes → {arena, background_agent} → headless →
composition/core`. 72 arena/server/headless/entry tests green; startup,
server boot, arena, bg agent, CLI verified by suite.

## 6–7. Containment convergence

Compared: identical realpath+prefix semantics; tools variant additionally
allows in-workspace absolutes, workspace variant rejects absolutes.
Canonical `wisp/pathsec.resolve_contained(root, candidate,
allow_absolute=True)`: NUL + C0-control rejection (new, fail-closed),
absolute policy flag, sibling-prefix-anchored check, root itself allowed.
All three callers delegate (tools keeps `Path`+`ToolError` signature).
Strengthened, never weakened: absolute paths now rejected in artifact/
workspace paths; control chars rejected everywhere.

## 8–9. CLI convergence

Discovery: 28 thin delegations + 5 inline sub-parsers; behavior lives in
`cmd_*`, the elif chain was pure routing. Converted mechanically to
`_SUBCOMMAND_TABLE` (def-per-command, bodies verbatim, zero reindent):
one routing point, names single-sourced against `_SUBCOMMAND_NAMES`
(test-enforced), help coverage test-enforced. REPL Dispatcher + legacy
registry remain as the conversational-dispatch layer with distinct context
(argv/exit-codes vs session/transport) — documented, not duplicated work.
186 CLI tests green; `--help`, unknown-command, and per-command behavior
verified byte-identical (including pre-existing unknown-input traceback).

## 10. Audit/redaction convergence

`tools/audit` already funneled through `auth.secrets`; graph scrub is a
documented thin extra. Only `AuditTrail._redact_value` diverged (key-mask,
no recursion, raw metadata, unserializable crash). Now: mask preserved,
then canonical pattern scan (recursive), metadata scrubbed, JSON-safety
coercion, plus two genuinely missing canonical patterns
(provider-key-assignment, sk-token — the known OPENAI_API_KEY gap is now
closed everywhere at once). Stores unchanged (JSONL vs SQLite roles kept).

## 11. Before/after graphs

Before: two runtimes; P1 SCC (8 modules); 3 containments; 30 elif branches;
split redaction. After: quarantined legacy (AST-gated); 4 residual SCCs
(doctor↔runtime, catalog↔select, tui↔screens.workspace,
dispatcher↔graph.cli — all deferred-import, classified, out of scope);
1 containment; 1 dispatch table; 1 redaction semantic.

## 12. Metrics

wisp/ 58,952→59,022 code (+70), files 369→371 (`headless`, `pathsec`).
Public API −3 (`wisp.core` re-export). Cycles 5→4. Containment impls 3→1.
Dispatch architectures 3→2 (one-shot table + conversational dispatcher;
legacy registry = compat data, not a router). Redaction funnels 3→1
semantic. Tests +84 new (5 files), full selection **1516 green**.

## 13–15. Regression/security/property

§33 matrices green (legacy, cycle, containment corpus+fuzz, CLI table,
redaction invariant incl. new patterns). AST quarantine + taxonomy tests
hold. Perf: import 379 ms, containment 22–24 µs/call (no regression),
audit 40 µs/write, chain verifies.

## 16–18. Legacy, compat, remaining

Quarantined: 3 legacy graph files (headers + removal condition + tests).
Compat: `entry.run_headless` re-export; `wisp/commands.py` untouched;
CLI behavior byte-identical. Remaining cycles/duplication explicitly
classified (above + TUI/speculative/embeddings untouched per §32).

## 19. Limitations

Legacy code still ships (importable); `server/headless.py` shim still
points at `entry` (works, noted follow-up); REPL Dispatcher ↔ legacy
registry fallback retained by design; redaction patterns still miss
high-entropy unlabeled secrets (documented, unchanged scope).

## 20–21. Invariants + freeze note

One runtime (quarantine-gated), correct dependency direction (Tarjan-
proven), one containment, one one-shot dispatcher, one redaction semantic,
unchanged authority (diff-verified: tool_executor comment-only,
secrets additive-only, optimizer untouched). Phase 10 frozen intact.

PHASE 12.5 — COMPLETE
