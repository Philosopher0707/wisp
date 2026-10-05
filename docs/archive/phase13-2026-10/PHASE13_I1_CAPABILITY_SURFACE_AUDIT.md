# PHASE 13-I1 — MODE-AWARE TOOL SURFACE & DELEGATION TRIGGER FORENSIC AUDIT

AUDIT ONLY. No production file modified. Baseline: HEAD `de130fe`, Python 3.12 venv, default AUTO_EDIT. Harness: `tests/reliability/test_13i1_capability_surface.py` (22 deterministic green + 2 live, local Ollama only).

## 1. Executive Summary

The primary model receives the same 42-tool surface in every mode, including READ_ONLY: `allowed_tools` is populated only for child subagents (`multi_agent/_runner.py:440,619`); no main entry path (`entry/composition/__main__/agent/headless`) sets it, so provider-bound schemas are never mode-filtered. Enforcement is execution-side only (`policy_hard_deny`, role-block, approval gate) — EXECUTION DENIAL exists, CAPABILITY HIDING does not. The model demonstrably cannot distinguish "Analyze this repo" from "Implement the changes": no host-owned task mode exists (TASK_MODE = NONE), permission mode is invisible in the prompt, and delegation pressure (12 EXEC-risk delegation tools, 41% of schema bytes + subagent-protocol block) is unconditional. Live A/B (local llama3.2:3b, hermetic fixture): zero delegation tendency in ALL conditions — causality of visibility on this model class: UNKNOWN. I0's incident-model cells remain the only positive fanout evidence. Top finding P2 (mechanism proven, causal weight unproven); one P3 taxonomy drift; H0's P1 skill opt-out verified FIXED.

## 2. Scope

§§2–9 from code + recording-mock provider rounds through production `turn()`; §10 by byte measurement; §§11–12/14 live on fixture with explicit `--provider ollama` equivalent (direct `OllamaProvider`, localhost:11434, model pinned from `/api/tags`); §§13/16/17 by authority trace; §19 targeted suites. No prod behavior changed (`git status` clean for tracked files).

## 3. Current Tool Surface

42 base schemas (`TOOL_SCHEMAS`), 27,542 B ≈ 6,885 tok (chars//4 repo convention). Plus runtime additions: composition appends `read_files_batch` to the GLOBAL list at startup (43rd tool live); extensions/MCP append further. Categories (test-pinned map, 5+2+6+2+9+1+12+5=42): READ 5 (read_file, list_files, git_status, git_diff, recall), SEARCH 2, ANALYSIS 6 (5 lsp + diagnose), NETWORK 2 (web_fetch/search — both in the "safe" set), MUTATION 9 (write/edit×2, git branch/commit/push, gh_pr_create, run_tests, rewind), EXECUTION 1 (run_bash), DELEGATION 12 (spawn, fanout, spawn_background, 5 subagent_*, 4 orchestrate_*), STATE 5 (remember, plan_task, mark_step_done, update_plan, capture_skill).

| Tool | Category | Read-only safe? | Mutating? | Delegating? | Network? | Requires approval? | Visible in each mode? |
|---|---|---|---|---|---|---|---|
| read_file, list_files, git_status, git_diff, recall | READ | yes | no | no | no | no | all modes + all roles |
| search_codebase, search_symbols | SEARCH | yes | no | no | no | no | all modes; absent from CODER/TESTER/DEBUGGER/PLANNER role sets |
| lsp_* (5), diagnose | ANALYSIS | yes | no | no | no | no | all modes; role-subset varies |
| web_fetch, web_search | NETWORK | nominally | no | no | yes | no | all modes (incl. READ_ONLY "safe" set) |
| write/edit×2, git branch/commit/push, gh_pr_create, run_tests, rewind | MUTATION | no | yes | no | no | auto_edit: writes auto-approved, run_bash/git-writes DENY (13F.1); ask_all/full: approval per policy | all modes (schema); execution-gated |
| run_bash | EXECUTION | no | yes | no | n/a | auto_edit: hard-DENY; else approval | all modes (schema) |
| spawn, fanout (+10 subagent/orchestrate companions) | DELEGATION | no | via children | yes | via children | auto_edit: REQUIRE_APPROVAL; full: auto_approve governs | all modes (schema); role sets exclude except GENERALIST(all) |
| remember, plan_task, mark_step_done, update_plan, capture_skill | STATE | partial | plan/scratch | no | no | ask_all: plan_* approval | all modes (schema) |

Approval column = execution gate, never visibility (proven §4).

## 4. `allowed_tools` Trace

`configuration → permission mode → (no task mode) → allowed_tools → schema assembly → provider request`: the chain BREAKS between permission mode and allowed_tools. Population sites: ONLY `_runner.py:440,619` (`_effective_child_tools`, subagents). Empty/unset means "all" (`stateless.py:267,308`: `"all" in {…} → no filter`). Enforcement points: (a) provider schemas filtered when set (`stateless.py:277` — the ONLY hiding mechanism, subagents only); (b) role-block refusal (`stateless.py:407-426`); (c) approval/policy gates at execution. The model sees prohibited capabilities in every main-agent mode even when execution will reject them (proven: read_only policy session still receives 42 schemas, harness `test_read_only_mode_still_advertises_fanout`).

## 5. Fanout Visibility Matrix

| Mode | fanout | spawn_background | subagent_wait | other delegation |
|---|---|---|---|---|
| read-only | YES | YES | YES | YES (all 12) |
| analysis (no such mode; closest: researcher role) | NO (researcher set lacks all 12) | NO | NO | NO |
| AUTO_EDIT (main) | YES | YES | YES | YES |
| repair (no such mode) | n/a (main surface) | n/a | n/a | n/a |
| restricted subagent | NO except GENERALIST(all) | NO | NO | NO |
| CI/headless | YES (full+auto-approve, 42/43) | YES | YES | YES |
| offline-secure (no such mode) | n/a | n/a | n/a | n/a |
| read-only-review (no such mode) | n/a | n/a | n/a | n/a |
| enterprise-managed (no such surface) | n/a | n/a | n/a | n/a |

5 of 9 rows are n/a — the mode vocabulary in the question does not exist in code. Verified by provider-capture rounds, not docs.

## 6. Task Mode Analysis

TASK_MODE = NONE. No `task_mode` field on `WispConfig` (asserted), no session key consumed (a `task_mode="READ_ONLY"` session key passes all 42 schemas through — asserted), nothing persisted, nothing passed to schema construction or authorization. Consequence: the ONLY host-owned mode signal is `permission_mode` (full/ask_all/auto_edit/read_only), which drives execution gates but zero schema filtering and zero prompt surfacing (no mode section in system prompt — H0 §9 stands).

## 7. Read-Only Intent Analysis

"Analyze…" vs "Implement…" are indistinguishable to the host. REPL entry, single-shot, headless, coding strategy (`TaskContext`), permission mode, graph integration, skill injection, ToolExecutor: none classifies intent; intent exists ONLY as model-side interpretation of user prose. Permission mode is user/host-selected posture, not intent inference (fresh sessions inherit AUTO_EDIT regardless of the words "analyze" vs "implement").

## 8. Capability-First vs Intent-First Architecture

Proven capability-first: USER TASK → MODEL SEES ALL CAPABILITIES → MODEL CHOOSES → HOST AUTHORIZES. The intent-first pipeline does not exist (no mode determination, no capability subsetting). Authorization-after-selection is load-bearing and intact; the missing stage is pre-generation capability scoping, which no component performs.

## 9. Delegation Prompt Pressure

Model-visible, always present, mode-invariant: (1) `DEFAULT_BASE_SYSTEM` "## Subagent protocol" (5 lines: launch→work→wait-once→fetch→report-honestly); (2) 12 delegation schema descriptions, several with use-conditions (`fanout`: "splits into independent work units"; `orchestrate_map_reduce`: "Fan a task out…"; `orchestrate_vote`: "Ask N independent subagents…"); (3) tools-menu one-liners for all 12. No "launch background agents NOW" directive; pressure is affordance + normalization, not instruction. Present during read-only analysis (asserted on captured prompt).

## 10. Schema Economics

Bytes (exact) / tok≈bytes//4: A all-42: 27,542 / 6,885. B read-only-14: 7,317 / 1,829 (−73%). C +analysis-16: 8,574 / 2,143. D +delegation-28: 19,925 / 4,981. E researcher-10: 5,698 / 1,424. Delegation subset alone: 11,351 B / 2,837 tok (41.2% of the full surface); mutation 5,390 B; network 1,091 B; read 6,226 B. Test-only snapshots constructed in-harness from live `TOOL_SCHEMAS`; production construction untouched.

## 11. Fanout Causality Experiment

Harness: same model (llama3.2:3b, explicit local Ollama — `/api/tags` pinned, no fallback path exercised), same prompt, same fixture, deny-all approval, ALL (42) vs NO_DELEGATION (30). Result: 0 tool calls both cells, direct answers, fanout 0/0, wall 13.4s vs 11.9s. The 3b model exhibits no delegation tendency under any condition (consistent with I0 local leg) → behavioral causality on this model class: UNKNOWN. Mechanism half PROVEN deterministically (provider-bound schema sets differ exactly as constructed: 42 vs 30). No speculation substituted.

## 12. Mutation-Surface Experiment

READ_ONLY (14) vs READ_EXEC (20) vs FULL (42), same harness: 3/3/0 calls (list/search/lsp mixes), fanout 0 throughout, answers absent in ladder cells (no done event — recorded, not interpreted). Planning deltas within noise for this model class → UNKNOWN. Deterministic half stands (schema sets differ by construction).

## 13. Delegation Authority Boundary

fanout → `_route_fanout` → child sessions with role-filtered tools (`_effective_child_tools`) → approval (REQUIRE in auto_edit; auto-approve governs in full) → execution → results → `subagent_wait` synthesis. Exposing fanout expands the model's planning options (12 EXEC-risk tools, children with network-capable researcher sets). Approval happens strictly AFTER selection — no host pre-generation veto exists. Read-only mode CANNOT prevent fanout at schema level today (matrix §5). fanout itself is EXEC-risk (delegation, not direct mutation); children are independently mode-filtered, so harm requires a second gate failure.

## 14. Approval vs Capability

Case A (forensics/I0 logs): visible → chosen → approval → 10 denials + approval UX interruptions on read-only tasks. Case B (live NO_DELEGATION cell): hidden → 0 proposals, 0 interruptions, direct answer, 11.9s. Approval is functioning as SECURITY CONTROL (correct denials, no bypass) while simultaneously absorbing PLANNING-CONTROL load it was never designed for (interruption cost on lawful-but-mismatched proposals). Hiding would remove the interruption class without weakening the security boundary (§17).

## 15. Skill Interaction

Mechanism: `SkillExtension.tools()` advertises every discovered skill as `skill__*` (description only); invocation returns the FULL SKILL.md body (≤50KB) with no capability check (read-like delivery). A skill body CAN therefore reintroduce delegation concepts post-filtering (instances: wisp-agent SKILL documents subagents/swarm; CoVe documents spawning subagents — both beyond any schema filter's reach). H0's dead-flag instance RESOLVED: `disable-model-invocation` is now honored fail-closed (verified by harness test; setup-matt-pocock-style mutation-directing skills can now be suppressed at advertisement). Residual: skill SELECTION remains model-side from descriptions; read-only tasks can still receive delegation-flavored instructions via invoked bodies. No fix applied (out of scope).

## 16. Mode-Aware Filter Design Space

Smallest host-owned partitions (permission_mode is the only host-owned signal — use it, not inferred intent): READ_ONLY → 14 safe schemas (delegation/execution/mutation hidden; approval load → ~0 for this class); ANALYSIS → 16 (+diagnose, capture_skill; delegation hidden — tradeoff: kills legitimate research-swarm use, documented); IMPLEMENT → read+mutation, delegation hidden unless task declares parallel structure; REPAIR → implement + run_tests/run_bash approved-set; FULL → 42/43 as today. Tradeoff core: hiding reduces proposal-error surface but also removes capabilities the model currently (mis)uses as planning fallback; delegation visibility for IMPLEMENT/REPAIR is the open calibration (I0: researchers are the legitimate fanout users — researcher role already excludes delegation, an inconsistency to resolve deliberately). No implementation (out of scope).

## 17. Security Invariants

Hiding is defense-in-depth ONLY. Proven: `policy_hard_deny` blocks fanout in READ_ONLY independent of advertisement (asserted); TOOL_RISK_TABLE marks fanout EXEC; `write_tools` membership forces the approval path. Required preserved order: MODEL SELECTION → HOST AUTHORIZATION → TOOL EXECUTION. Audit confirms no path treats visibility as authority (all three enforcement layers key off names/modes, never off "was it advertised").

## 18. Context/Performance Impact

Per-round schema tax: full 6,885 tok vs read-only 1,829 (−5,056 tok/round, −73%). Schemas resend EVERY provider round (system prompt is per-request): ×10 rounds ≈ 50k tok; ×50 ≈ 250k tok of capability advertisement alone. Delegation subset (2,837 tok/round) is the single largest removable block. No billing extrapolation (provider accounting unavailable).

## 19. Regression Results

New harness: 22 deterministic green + 2 live green. Adjacent suites: 214 passed; 15 failed — all pre-existing and HEAD-confirmed (14× missing `cryptography` in venv, 1× timing-sensitive FirstTokenDeadline). Ruff clean on harness. No prod files touched (`git status` clean for tracked).

## 20. Findings

- F-1 (P2): always-visible 42-tool surface across all main-agent modes; enforcement execution-side only. Mechanism proven; causal weight on incident fanout UNKNOWN (I0 positive cells predate filtering; local A/B cannot discriminate).
- F-2 (P3): `TOOL_RISK_TABLE` drift — `rewind` unclassified — AND the table is write-only taxonomy (zero production readers; enforcement lives in `security.py`/`policy_engine.py` frozensets). No enforcement impact.
- F-3 (P3): runtime global mutation of `TOOL_SCHEMAS` (`register_with_wisp_registry` append, no lock/audit log) — live surface is 42+X by startup path; ordering-dependent visibility.
- F-4 (P2): skill bodies reintroduce delegation concepts post-filter (mechanism + H0 instance); advertisement flag now honored (H0-P1 CLOSED).
- F-5 (P2): approval absorbs planning-control load (10 read-only denials); security-control function intact.

## 21. Severity

Highest P2 (F-1/F-4/F-5). No P0 (no bypass, no loss). No P1: the I0 incident class (expensive uncontrolled completion) is real but visibility's causal share is unproven — rating the mechanism P1 would inflate. H0-P1 (skill opt-out) verified CLOSED.

## 22. Recommended Next Gate

NEXT PHASE: 13-I2 — implement the smallest host-owned partition behind a flag: filter provider-bound schemas by `permission_mode` (READ_ONLY→14 safe; AUTO_EDIT→hide 12 delegation tools OR keep-with-approval, decided by I2 A/B using this phase's live harness cells), reusing `test_13i1` ALL/NODELEG cells as the acceptance A/B on a capable model with explicit credential opt-in. Do NOT start from intent inference (no such signal exists); do NOT touch approval semantics.

## 23. Unknowns

Live behavioral causality on capable models; ladder no-done anomaly; exact subagent-side tool visibility under `_effective_child_tools` per role (role sets recorded, child-round capture not run); enterprise-managed/offline-secure surfaces (no such code paths — matrix n/a); per-round payload bytes (H1 standing null).

```text
VERDICT: PASS WITH KNOWN UNKNOWNs
NEXT PHASE: 13-I2
```
