# FANOUT TRIGGER FORENSIC — read-only overview session, fanout denial

Trace: `list_files` → `fanout(max_concurrent=3, mode='background', tasks=<3 items>)` → approval prompt → user denied (`Blocked: not approved`). Forensic only; no production code touched.

## 1. Exact fanout tool definition (`wisp/tools/registry.py:249-279`)

Description verbatim:

> "Launch multiple specialist subagents in parallel, NON-BLOCKING: returns agent ids immediately and you keep working while they run. Settle lines appear as each finishes; call subagent_wait with the returned agent_ids when you need the results (synthesis point). Use when a task splits into independent work units. Pass mode:'blocking' only if you must have all results before doing anything else."

Params: `tasks[]` (`task` required; per-task `role/timeout_seconds/max_iterations/worktree_isolated/model`), `max_concurrent` (default 4), `mode: background|blocking` (default background). Required: `["tasks"]`.

## 2. Exact relevant system-prompt instructions (`wisp/context_assembler.py:51-83`)

`DEFAULT_BASE_SYSTEM` "## Subagent protocol" block, shipped unconditionally:

- "fanout/spawn_background return IMMEDIATELY with agent ids; you stay free to work."
- "After launching: do useful independent work (read files, answer questions, prepare synthesis). Do NOT idle-poll subagent_list in a loop."
- "Call subagent_wait ONCE at your synthesis point when you actually need the results…"

Tools menu (`_build_tools_block`, `stateless.py:1574`): `- fanout: Launch multiple specialist subagents in parallel, NON-BLOCKING…` (first sentence of the schema description). No "go use fanout", "delegate", or "parallelize this task" imperative exists anywhere in the prompt. `rg -i "parallel|concurrent"` hits only the two lines above.

## 3. Relevant skill instructions

Discovered project skills (`.agents/skills/`): chain-of-verification, matt-pocock, issue-remediation-loop. Only CoVe carries parallelism text — "Spawn independent subagent calls for each verification" (line 103) and "run verifications in parallel" (line 75). **Not model-visible**: `_build_skills_block` (`stateless.py:1283`) emits name + description + first 200 chars of instructions only; the CoVe parallelism lines sit far beyond that window. Skill contribution: ~none (description-level CoVe relevance to a "technical documentation" request at most).

## 4. Model-visible tool registry

Provider receives full `TOOL_SCHEMAS` — 42 builtins — for the main agent in every mode. `stateless.py:263-277`: schema filtering by `allowed_tools` exists, but the main REPL session carries `allowed_tools=all`/unset; the `_allowed_set` role-block (`stateless.py:407-426`, "switch to full mode" hint) fires only for role-restricted *subagents*. No mode-based schema hiding for the primary agent. Extensions/MCP append on top.

## 5. Immediate pre-fanout context

Previous tool: `list_files` (ok, 333µs), result listed `docs/architecture/VERIFICATION_ENGINE_DESIGN.md` + `scripts/fix_doc_comments.py` (neither exists in repo now — created/removed since, or ephemeral). User request: read-only 4-section technical overview. Steering: none (H0). Session: workspace `/Users/philosopher/Documents/wisp`, model `nex-agi/nex-n2.5-pro:free` (sessions table), auto_edit posture (audit log). System prompt carried: base system + verification rules + subagent protocol + 42-tool menu + repo map/memory/git blocks.

## 6. Exact fanout arguments

From trace render: `max_concurrent=3, mode='background', tasks=<3 items>`. Full `tasks[]` texts are **unrecoverable from host state**: the call was denied pre-execution (no telemetry/results) and `audit.jsonl` records `args_keys: []`, `arg_summary: {}` (arg scrubbing). No `session_events` payload preserves the proposal (`msg_count 0` REPL rows; denials leave no argument record).

## 7. Origin of the 3-task decomposition

Model reasoning. Eliminated: host code performs no bullet→task mapping (no decomposition routine found; capability_matcher routes executors, never authors tasks; graph optimizer only *caps* fanout width); no prompt transformation rewrites user bullets (only additive "Files Relevant to Query"/compaction notices); skill text with task-splitting semantics was outside the visible window; the 4→3 shape matches no host constant (`max_subagent_branching=3` bounds *spawned* agents, it does not author 3 tasks from 4 sections — and the call never executed, so no cap engaged).

## 8. Causal decision chain

1. Full 42-tool schemas to provider → fanout visible with "Use when a task splits into independent work units."
2. System prompt normalizes background delegation (protocol block) + lists fanout in the menu.
3. User's 4-section overview request pattern-matches "splits into independent work units."
4. Model authors 3 background tasks, `max_concurrent=3` (mirrors task count, not the default 4).
5. Gate: fanout ∈ `write_tools` (`config.py:724-741`) + `ToolRisk.EXEC` (`core/contracts.py:297`) → approval required in auto_edit → prompt rendered → user denied. Repeated 5× Sep 11–12 (`audit.jsonl`: `USER_DENIED … mode auto_edit`).

## 9–11. Encouraged vs merely available; mode variance

Explicitly encouraged? No imperative points fanout at *this* task. Merely available? No — stronger: the description's *use-condition* ("splits into independent work units") is a standing invitation the overview request satisfies. Availability is mode-invariant for the main agent; only the *consequence* differs by mode (auto_edit→approval prompt, full→runs). The "blocked in auto_edit" role-hint never fires for the primary agent.

## 12–13. Host recommendation; capability-first selection

No host component recommends fanout (no "consider fanout/spawn" prose; no planner injection). The model selected it from described capabilities after exactly one ground-truth tool (`list_files`) — capability-first, evidence-thin selection.

## 14. Severity: P3

Defense held (approval caught an EXEC-risk call on a read-only task). Cost is approval fatigue: 5 denied fanouts in 2 days, each interrupting a read-only flow. No execution, no side effects, no bypass.

## 15. Smallest remediation (recommended, not applied)

One-line description guard on the fanout schema: append "Prefer direct reads for analysis-only tasks; do not fan out to summarize files you can read yourself." Rationale: edits the exact sentence doing the inviting, zero code paths touched. Mode-aware schema hiding is the larger fix; deferred.
