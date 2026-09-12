# PHASE 13-I IMPLEMENTATION REPORT (I0 + I-1; I-2..I-6 deferred)

## 13-I-0 — Malformed disable-model-invocation contract: RESOLVED
Finding: opt-out flag dead metadata (13-H0). Implementation:
`resolve_invocation_visibility()` + `is_model_advertisable()` in
`wisp/skills.py` (strict `type(v) is bool`; ABSENT/ENABLED/DISABLED/
INVALID); `Skill.model_invocable`; `tools()` skips non-invocable;
INVALID logs warning (name/path/field/value). Explicit/human
invocation and authorization untouched. Files: `wisp/skills.py`,
`wisp/extensions/skills.py`. Tests: 27 gating (16 malformed incl.
mandatory `"false"` regression) + H0-1 inverted. Before: flagged
skill advertised. After: hidden + diagnostic. Invariant: invalid
opt-out never advertises. Perf: one dict lookup per skill at parse.
Security: advertisement-only withholding; no authority change.
Limitation: YAML-coerced `yes/no` follow parser bools (documented).

## 13-I-1 — Search integrity: PASS
Finding: empty/unbuilt index → authoritative-looking negative (P1).
Decision: Option B (explicit state + model-chosen fallback), NOT
on-demand indexing (unbounded Ollama latency per query).
Implementation: `SemanticIndex.index_state()` (MISSING/EMPTY/
EMPTY_NO_FILES/CORRUPT/STALE/READY via existence/counts/coverage,
no embeddings; ~3ms/300 files) + `last_search_degraded` flag for
zero-vector (backend-down) queries + `tool_search_codebase` gate:
bare "nothing found" ONLY on READY-searched or no-indexable-files;
all other states get explicit unavailable/stale text naming
search_symbols/direct reads. Before: every cold search lied. After:
12/12 state tests green. Invariant: invalid index != valid negative
(enforced in tests, incl. stale+missing+corrupt+empty+degraded).
Perf: +1 walk + few DB reads per search (~ms). Security: detail
truncated to 3 filenames; no contents/payloads stored. Limitation:
no score threshold exists (top-k always returns) — quality-side
negatives are out of scope; staleness is mtime-heuristic (1s).

## 13-I-2..I-6: DEFERRED (not blocked, sequenced)
Each (ledger, retry controls, telemetry, fanout) needs its own
implement→test→verify cycle per §16; batching them behind I-1 risks
hiding failures. No architectural blocker found.

## Regression
G1A–G1E green; skill suites green (153); stream contract green;
full suite 5650 passed / 23 baseline + 1 own inversion (H5, fixed,
re-green). Ruff clean on all touched files. One behavioral
verification: HEAD-version protocol battery passes against new code.

## Files changed (prod)
`wisp/skills.py`, `wisp/extensions/skills.py`,
`wisp/semantic_index.py`, `wisp/tools/search.py`.
## Tests
`tests/test_skill_invocation_gating.py` (new, 27),
`tests/test_search_integrity.py` (new, 12),
`tests/test_13h0_forensics.py` (H0-1 inverted),
`tests/test_13h_forensics.py` (H5 inverted).
## Global invariants
All §9 hold (verified: authority ⊆, retry bound unchanged,
incomplete≠complete, journal intact, graph terminal, steering keeps
state, invalid≠negative, deadline caps nesting).
