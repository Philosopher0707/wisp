# REPL ↔ Graph Coding Report (Phase 11)

## 1. Before / after

Before: conversational agent loop and graph runtime were separate tools
(`wisp graph run` manual). After: `ReplRunner` consults a deterministic
strategy gate per prompt — simple turns unchanged, complex tasks run a
coding graph with progress lines, durable refs, and conversational summary.

## 2–3. TaskContext + strategy gate

`wisp/coding.py::TaskContext` (frozen): objective ≤8KB, `constraint:`/
`must not:` lines, workspace, profile, existing-file facts ≤20, repo refs,
informational capabilities (never authority), mode, metadata. No transcript.

`decide_strategy`: explicit mode wins; interrogatives forced single;
score = extra-files×2 + hints(≤3) + length + multi-part; GRAPH at ≥2;
policy `max_nodes<3` vetoes graph. Model may recommend via metadata —
host decides (capabilities/recommendations never gate).

## 4. REPL ↔ Graph integration

12-line hook in `ReplRunner.run()` before the turn: `coding.handle_prompt`
returns True (graph path, blocking run with progress) or False (agent
loop). Control via Graph API only (`run/status/trace/cancel/resume`);
`coding.py` never imports sqlite3 or touches `.wisp/wisp.db`. Continuation
uses run/node/artifact refs, never transcripts.

## 5. RepoMap integration

`repo_context()` reuses `wisp.repo_map.RepoMap`: `build()` +
`get_relevant_files(query,12)` + `get_dependencies` (8 seeds) +
`format_for_llm` within token budget. Bounded, deterministic given FS,
degrades to empty on failure. Nodes receive file lists, not the repo.

## 6–7. Templates + contracts

`wisp/graph/coding_graphs.py`: simple (ANALYZE→IMPLEMENT→VERIFY),
parallel-analysis (split→4 branches→synthesize),
repair (IMPLEMENT⇄VERIFY bounded ×3), complex (analyze→fan→plan→
implement→test→review⇄repair). All compile+optimize+validate clean.
Analysis nodes read-only tools; implement nodes write-capable (enforced
downstream by `authorize()`); verifiers independent with evidence;
repair reuses bounded CycleSpec; `pick_template` routes by keywords.

## 8–11. Parallelism, implementation, repair, results

Fanout only for independent analysis with isolated contexts; joins consume
mapped findings. Mutations flow runner→orchestrator→ToolExecutor (children
inherit parent permission mode — same as existing graph runs). Repair is
verifier-evidence-driven, max 3 iterations, budgets unchanged.
`ExecutionResult` (strategy/success/summary/files/tests/verification/
warnings/artifacts/evidence/execution_id/followup) keeps the REPL
substrate-agnostic.

## 12–13. Streaming UX + progress

`render_progress` maps 12 structural event types to glyph+label lines;
model text never becomes status; unknown events silent. `/graph`
status/trace/inspect/metrics/cancel/resume + new honest `pause`
(cancel-now/resume-later, terminal-safe).

## 14–16. Security, injection, artifacts

Repo content is data end to end (85 adversarial tests: poisoned
source/README/comments, fake/truthy approvals, tool/provider/workspace
escape, artifact-ref smuggling, strategy escalation, repair escalation).
Approval stays `decision is True` on the resume channel; coding.py decides
nothing. Artifacts runtime-only (no `.wisp/artifacts` at plan time,
tested). Audit unchanged (executor emits; CLI redaction kept).

## 17–18. Compatibility + perf

Agent loop, graph CLI/SDK/resume/audit/artifacts untouched (1037 green).
Baselines: gate 200× in 1 ms; templates 1–3 ms; RepoMap cold 1.4 s on a
930-file repo / 7 ms cached; parallel-vs-sequential untouched (runtime).

## 19–20. Test matrix + limitations

122 coding tests (strategy/context/templates/repo/control/result/
progress/hook/security/fuzz). Limitations: no model-based strategy
recommendation call (heuristics only); `default_executor` doesn't forward
REPL auto_approve (subagents inherit permission mode); no cross-run
learning; templates fixed set of four.

## 21–22. Deferred + invariants

Deferred per spec (§27) all untouched. Invariants: conversation→intent→
TaskContext→gate→loop-or-graph→RepoMap→governed execution→verification→
bounded repair→unified result; no subsystem absorbs another's role.

PHASE 11 — COMPLETE
