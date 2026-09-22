# WISP — CONTEXT ARCHITECTURE

**Phase 0 design document. Conceptual only — nothing here is implemented.**

> **Principle 7:** *Context engineering is a first-class subsystem.*
> **Principle 8:** *Repository intelligence is derived knowledge supporting execution and planning.*

---

## 0. The Current Position

Wisp's context assembly is **better than the target brief would predict** — it is budgeted, cached,
priority-ordered and deterministic. Three things are missing, and one of them is serious.

| Property | Today | Verdict |
|---|---|---|
| Budgeted | 6000-token prompt, 1200-token repo map, 200 KB payloads | **good** |
| Priority-ordered | `ContextAssembler.build` sorts sections by priority | **good** |
| Deterministic | given a fixed workspace, the static part is a pure function of a frozen `PromptContext` | **good** |
| Cached | 16-entry LRU + module-level system-prompt cache keyed on mtimes | **good** |
| Pruned | three live pruners, all reachable | **good** |
| **Trust boundary** | **absent** | **SERIOUS** |
| **Graph context** | **absent** | gap |
| **Token-based compaction** | compacts by **message count**, not tokens | gap |
| **Plan context** | assembled but never populated | gap |
| **Repo map depth** | the live path is forced to **skeleton-only** | gap |

---

## 1. Context Construction — the Real Path

```
core/runtime.py:401        append user message to session["messages"]
core/stateless.py:239      messages = list(session.get("messages", []))     # shallow copy
core/stateless.py:247-259  boot seed inserted at index 0 if no history
core/stateless.py:261-266  user prompt appended if not already last
core/stateless.py:269      system_prompt = self._build_system_prompt(session, query=prompt)
core/stateless.py:273-294  tools = self._get_tool_schemas(), role-filtered
core/stateless.py:1109     _build_system_prompt
   └─ context_assembler.py:329/374  ContextAssembler.build
        └─ priority sort (:389-450, sort at :499)
             -1  context_files
              0  default_system, workspace
              1  mandatory_skill, active_plan, plan_mode, plan_context
              2  role_extra, skills_block, memory_block
              3  project_context, code_index, recent_summaries, git_context, repo_map
   └─ per-turn appends:
        :1264  query-relevant files
        :1269  compaction notice
        :1275  operating context
        :1281  environment
```

Then, inside the turn, `messages` is **mutated in place**: assistant message (`:859`), tool messages
(`:861-883`), nudges (`:766`, `:909`), steering (`:896`).

**Purity assessment:** `ContextAssembler.build` is pure over a frozen `PromptContext` but carries a
mutable 16-entry LRU (`context_assembler.py:321-324,473-477`). `_build_system_prompt` is **not pure** —
it reads a module global (`_SYSTEM_PROMPT_CACHE`, `stateless.py:72,1191`), disk mtimes, git, memory
and environment on every turn. The message list is mutable and mutated mid-turn.

**Target:** context construction becomes a function of an explicit `ContextRequest`, with all
non-determinism (mtimes, git state, clock) captured *into* the request before assembly. Same output,
but reproducible and testable.

---

## 2. Context Selection

**No embedding/retrieval step exists in the prompt path.** Selection is three mechanisms stacked:

| Mechanism | Nature | Where |
|---|---|---|
| File discovery by extension + `_SKIP_DIRS` | deterministic | `repo_map.py:32-65,620-724` |
| Query-relevant files by keyword substring scoring, `top_k=5` | heuristic | `repo_map.py:415-466`, called `stateless.py:1264` |
| File bodies via model tool calls | **model-controlled** | `read_file` etc., persisted as `role:"tool"` |

**Target:** selection becomes deterministic given a `ContextRequest`. The keyword heuristic is
retained as the *default selector* for repository context, but it is named and swappable, and its
output is recorded so a context decision can be explained.

---

## 3. Context Sources and Caps

| Source | Mechanism | Deterministic? | Cap | Enters via | File:LINE |
|---|---|---|---|---|---|
| Base system rules | constant | yes | 6000 tok total | system prompt | `context_assembler.py:51-110` |
| Workspace path | constant | yes | — | system prompt | `context_assembler.py:396` |
| `.wisp/rules.md` | file read | yes | (budget) | role_extra | `stateless.py:1208-1214` |
| Skills | dir scan of SKILL.md | yes | 200 chars each | skills_block | `stateless.py:1395-1411` |
| Project context | marker scan | yes | (budget) | project_context | `project_context.py:36-59` |
| Memory facts + summaries | file read | yes | 15 facts, 3 summaries | memory_block | `stateless.py:1424-1441,2301-2334` |
| Git context | git CLI | yes | (budget) | git_context | `git_context.py:64+`; `stateless.py:1446-1460` |
| RepoMap | tree-sitter/regex + PageRank | yes | 200 entries / 1200 tok | repo_map | `repo_map.py:197-411`; `stateless.py:1462-1481` |
| Boot seed | AGENTS.md / CLAUDE.md / `.wisp` | yes | 8000 chars | user msg index 0 | `core/context/boot.py:100-273` |
| Query-relevant files | keyword scoring | heuristic | top 5 | system prompt | `repo_map.py:415-466` |
| Tool output | model tool calls | model | 8 KB hist / 50 KB recent / 200 KB total | `role:"tool"` | `context_pruner.py:78-90` |
| Semantic search | embeddings | retrieval | `top_k` | tool result only | `semantic_index.py:512-619` |

**Explicit budgets (retain):** assembled system prompt 6000 tokens (`context_assembler.py:49`); repo
map 1200 tokens (`stateless.py:1472-1475`); boot seed 8000 chars (`core/context/boot.py:37`); tool
payload 200 KB total / 8 KB historical / 50 KB recent (`context_pruner.py:78-90`;
`context_manager.py:37-42`); repo map entries 200 (`repo_map.py:175`).

---

## 4. Trust Boundary — the Serious Gap

### 4.1 Finding

**No code distinguishes untrusted repository content from trusted system instructions.** Repo text
enters as plain system-prompt sections (`## Codebase Map`, `## Project guidelines`,
`## Cross-Session Memory`) or as `role:"tool"` messages — with **no delimiters, no escaping, no
provenance tags**.

The only defenses are **prose and authorization**:

| Defense | Location | Nature |
|---|---|---|
| Grounding prose | `context_assembler.py:67-74` | advisory text |
| Skill guardrail footer | `context_assembler.py:459-467` | advisory text |
| Agent-role "UNTRUSTED WEB DATA" note | `multi_agent/roles.py:128,215` | advisory text |
| Eval scenario | `eval/scenarios.py:30-37` | a test, not a control |
| Boot guidelines | `core/context/boot.py:100-118,229-241` | injected with **truncation only, no sanitization** |

**Why this matters for the Persistent Graph Loop specifically:** a graph that reads repository
content to decide *what to do next* (via the planner) is directly exposed. Prompt injection in a
README becomes a plan proposal. Today the blast radius is bounded by the 19-gate tool pipeline — but
the target architecture gives the model authority to propose `NodeCreate`, `GraphExpand` and
`DelegationRequest` (`WISP_PROPOSAL_PROTOCOL.md` §5), so the boundary must exist *before* that
authority is granted.

### 4.2 Target: trust tags

Every context item carries a trust tag and a provenance reference:

| Tag | Source | May influence |
|---|---|---|
| `SYSTEM` | built-in rules, architecture invariants | anything |
| `OPERATOR` | `.wisp/rules.md`, operator-authored criteria, approvals | anything |
| `REPOSITORY` | file contents, repo map, code index, git context | *planning and action selection only* |
| `TOOL_OUTPUT` | tool results | *planning and action selection only* |
| `EXTERNAL` | web fetch, MCP results, third-party artifacts | *planning and action selection only*, never policy |

**Structural rules:**
- T1 — Only `SYSTEM` and `OPERATOR` items may appear in instruction position.
- T2 — `REPOSITORY` / `TOOL_OUTPUT` / `EXTERNAL` items are always **delimited and labelled**, never
  concatenated into instruction prose.
- T3 — An untrusted item may **never** alter policy, authorization, criteria, or the tool set.
- T4 — Provenance is recorded: which file, which hash, which observation.

**Why tags rather than sanitization:** sanitization of arbitrary repository text is not solvable
(there is no reliable injection detector). Labelling *is* solvable, and it makes the boundary
auditable: any policy-relevant decision that cites a `REPOSITORY` item is a defect that can be
detected mechanically.

---

## 5. Compaction

### 5.1 Four implementations, three live

| Implementation | Trigger | Preserves | Status |
|---|---|---|---|
| `prune_live_session` | pre-turn, byte budget | recent 3 payloads verbatim; user/assistant untouched | **LIVE** (`context_manager.py:108`; `runtime.py:386-390`) |
| `prune_messages` | before **every** provider dispatch | `read_file`/`list_files` → status headers; others → head/tail 8 KB | **LIVE** (`context_pruner.py`; `stateless.py:371,921,967`) |
| `Compactor.compact` (LLM) | `len(messages) > max_messages` | system messages + summary + kept window | **LIVE** (`core/compaction.py`; `runtime.py:857-911`) |
| 3-tier policy (micro / RollingSummary@70% / full 9-section) | — | — | **UNWIRED** (`core/context/compactor.py`) |
| `SemanticCompressor` | — | dedup / truncate / LLM | reachable only via a deferred import in `infra/session_dto.py:67` (session export), **not the turn path** |

### 5.2 Target

- **One policy, three mechanisms.** `prune_live_session` (budget), `prune_messages` (per-dispatch),
  `Compactor` (semantic) stay; the unwired 3-tier policy becomes the *policy* that decides which
  mechanism fires, rather than a fourth implementation.
- **Token-based, not count-based.** The audit found compaction triggers on message count
  (`runtime.py:857-868`) while every other budget is in tokens. Unify on tokens.
- **Compaction is a recorded transition,** not a silent rewrite. Today `maybe_compact` mutates
  `session["messages"]` in place (`runtime.py:914-916`) with no durable record of what was dropped —
  which is precisely what makes replay impossible (audit §20).

---

## 6. Graph Context — Currently Absent

**Verified:** no graph/task-state serialization into the prompt exists. Legacy
`session["graph_state"]` hydration was removed (`runtime.py:394-395`). `PlanState` exists in the
assembler (`context_assembler.py:199-208`) but `stateless.py` calls `from_legacy` **without**
`plan_*` (`:1238-1247`), so plan sections are inert in production. The plan tools (`plan_task`,
`mark_step_done`, `update_plan`) write to a `PlanStore` that never reaches the model.

### 6.1 Target: an explicit, budgeted graph-context section

```
## Current Work
Node:        <task_id>  (<type>)   attempt 2/3
Goal:        <goal statement>
Criteria:    [x] build passes        (evidence: exit 0 @ 12:04)
             [ ] integration test    (not yet evaluated)
Depends on:  <task_id> SUCCEEDED (evidence: artifact ab12...)
Blocked by:  —
Prior failures (this node): TOOL — "module not found" (2 attempts)
Budget:      4/10 turns · 62% of turn time · replans 1/2
```

**Rules:**
- G1 — Only the **current node's** dependency outputs are included, never the whole graph.
- G2 — Evidence is referenced by **hash/pointer**, not inlined.
- G3 — Failure history is bounded to the current node's recent attempts.
- G4 — Budget state is shown explicitly (the audit found **no explicit token-budget accounting is
  shown to the model** today).
- G5 — Prior failures become a **structured ledger**, not raw `role:"tool"` text subject to pruning.

**Why G1 matters:** the audit flagged "unnecessary graph serialization" as a risk. Serializing the
whole graph would be the single fastest way to blow the context budget. The section is scoped to the
current node by construction.

---

## 7. Repository Intelligence

### 7.1 What exists

`repo_map.py` produces `RepoMapEntry(path, name, kind, line, signature, importance, dependencies,
summary)` (`:133-153`).

| Aspect | Finding |
|---|---|
| Indexed | files + symbols + dependency graph + PageRank importance (`:593-618,1761+`) |
| Generated | tree-sitter (`_extract_symbols_ts`) with regex fallback |
| Updated | **on demand only** — no watcher |
| Queried | `get_relevant_files` (keyword), `get_dependencies`/`get_dependents` (`:415-490`) |
| Persisted | `.wisp/repo_map.json` with `_meta{timestamp,git_hash,files,skeleton}` (`:761-844`) |
| Authoritative? | **advisory** |

### 7.2 The skeleton problem — verified

The turn path calls `build(use_cache=True, fast_mode=True)` (`core/stateless.py:1469`), and
`fast_mode` short-circuits at `repo_map.py:218-245`. **The turn path therefore always receives the
skeleton** — a file-path list with `importance=0.5` and `kind:"file"` — and the full symbol/PageRank
map is never built in the main loop.

Verified on disk: `.wisp/repo_map.json` has `_meta.skeleton: true`, 200 entries, all `kind:"file"`.

**So the repository intelligence that exists is not the repository intelligence the model receives.**
This is a wiring/serving gap, not a capability gap.

### 7.3 Sibling indexes

| Index | Storage | Wired to the turn path? |
|---|---|---|
| `code_index.py` / `tree_sitter_index.py` | in-memory, 30 s TTL, **not persisted** | no (used by `search_symbols`, `workspace.py:189,382`) |
| `import_graph.py` | — | no |
| `semantic_index.py` | SQLite `.wisp/semantic_index.db`, tables `files`/`chunks`/`embeddings`, Ollama `nomic-embed-text`, numpy cosine | **no** — model-invoked via `search_codebase` (`tools/search.py:50-90`) |
| `core/context/repomap.py` | independent PageRank + token-budget binary search | **UNWIRED** (tests only) |

`semantic_index` handles staleness **correctly** — mtime diff → `STATE_STALE` → refuses to answer
(`:451-508`, `tools/search.py:71-74`). That refusal is the right model for repository knowledge and
should be preserved.

### 7.4 Target role

Repository intelligence remains **derived and advisory**, and gains two jobs:

1. **Context** — the turn path receives the *symbol-level* map within its token budget, not the
   skeleton (remove the forced `fast_mode`).
2. **Proposal input** — the planner and the node proposer consult the map to answer "which files does
   this task touch, and what depends on them?" That is the difference between a graph built from
   guesswork and one built from knowledge.

**It never gains authority.** It may inform; it may not authorize. This is Principle 8 stated as a
constraint.

---

## 8. Context Size Control

| Risk | Mechanism today | Cap | Target change |
|---|---|---|---|
| Full file reads entering history | pre-dispatch pruning (keep last 3 full) | 200 KB live | retain; add token accounting |
| Repo map dump | 200 entries × 1200 tokens | capped | serve symbol-level within the same cap |
| Boot seed (AGENTS.md/CLAUDE.md) | 8000 chars, truncated | capped | retain; add trust tag |
| Unbounded history between compactions | `max_messages` trigger | count-based | **switch to tokens** |
| System prompt growth | `_fit_sections` drops/truncates over-budget sections; priority 0 truncated, not dropped | 6000 tokens | retain |
| Semantic index load | all embeddings into one numpy matrix (`semantic_index.py:550-575`) | memory, not context | unchanged |
| Whole-graph serialization | **does not happen today** | — | **must not be introduced** (rule G1) |

**The single largest explosion risk introduced by this migration would be graph context.** Rule G1
exists specifically to prevent it.

---

## 9. Deterministic Context Selection

**Target contract:**

```
ContextRequest
  goal_ref        : the goal
  task_ref        : the current node (or null at planning time)
  purpose         : PLANNING | ACTING | VERIFYING | RECOVERING
  workspace_state : git HEAD, mtimes, hashes   (captured, not read live)
  budget          : token + byte budget
  trust_floor     : the minimum trust level permitted for this purpose
  prior_failures  : the structured failure ledger for this node

Context
  sections        : [(tag, priority, provenance, content)]
  dropped         : [(tag, reason)]           # what was cut, and why
  token_estimate  : int
```

**Properties:**
- D1 — Same `ContextRequest` → same `Context`. All non-determinism is captured into the request.
- D2 — `dropped` is recorded. Today `_fit_sections` truncates silently
  (`context_assembler.py:492-582`); a context decision that cannot be explained is a context decision
  that cannot be debugged.
- D3 — `purpose` selects the section set. Planning does not need tool output; verification does not
  need the full repository map.
- D4 — `trust_floor` is enforced: a `VERIFYING` request may not include `EXTERNAL` content.

---

## 10. Trusted vs Untrusted — Summary

| Information | Tag | Instruction position? | May affect policy? |
|---|---|---|---|
| Built-in system rules | `SYSTEM` | ✅ | ✅ |
| `.wisp/rules.md` | `OPERATOR` | ✅ | ✅ |
| Operator-authored criteria | `OPERATOR` | ✅ | ✅ |
| Skills | `OPERATOR` (if operator-installed) | ✅ | ✅ |
| AGENTS.md / CLAUDE.md | `REPOSITORY` | ❌ — delimited block | ❌ |
| File contents | `REPOSITORY` | ❌ | ❌ |
| Repo map / code index | `REPOSITORY` | ❌ | ❌ |
| Git context | `REPOSITORY` | ❌ | ❌ |
| Tool results | `TOOL_OUTPUT` | ❌ | ❌ |
| Web fetch / MCP results | `EXTERNAL` | ❌ | ❌ |
| Memory facts | `OPERATOR` if operator-authored, else `TOOL_OUTPUT` | per origin | per origin |

**Note on memory:** `memory.py` facts are currently indistinguishable by origin. The target requires
each fact to carry its origin so it can be tagged correctly — a small schema addition with a large
security effect.

---

## 11. What Must Not Change

| Component | Why retain |
|---|---|
| `ContextAssembler.build` priority ordering and `_fit_sections` | correct, budgeted, tested |
| The three live pruners | each solves a distinct problem at a distinct trigger point |
| The 6000/1200/8000/200 KB caps | appropriately conservative |
| `RepoMap` generation (tree-sitter + PageRank) | genuine repository intelligence |
| `semantic_index`'s `STATE_STALE` refusal | the right model for stale derived knowledge |
| Boot seed injection | correctly bounded, correctly placed |
| `semantic_compressor` reachability via `session_dto` | the audit's own inventory pins this; do not "fix" it |

---

## 12. Open Questions

1. **Where do criteria-authored context and operator rules live?** `.wisp/rules.md` exists and is
   already read (`stateless.py:1208-1214`) — it is the natural home for `OPERATOR`-tagged criteria.
2. **How expensive is a symbol-level repo map per turn?** The forced `fast_mode` suggests the full
   build was too slow. The target needs a measurement before removing the shortcut.
3. **Should `EXTERNAL` content be summarised by a separate model before entering context?** This is
   the standard containment technique, but it adds a call per fetch.
4. **How does memory origin get recorded retroactively?** Existing facts have no origin field; a
   migration decision is required.
