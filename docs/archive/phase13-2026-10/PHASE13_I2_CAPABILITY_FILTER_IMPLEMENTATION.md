# PHASE 13-I2 — HOST-OWNED CAPABILITY PARTITION IMPLEMENTATION

Implementation + controlled A/B. Baseline HEAD `de130fe`, tracked tree held to 2 modified files + 2 new files (below). No authorization, approval, policy, executor, provider, graph, retry, terminality, or skill code touched.

## 1. Executive Summary

READ_ONLY provider-schema filtering is implemented behind `capability_filtering` (default OFF): with the flag ON in READ_ONLY, the model receives 14 safe schemas instead of 42; menu, provider payload, and cache key follow the same partition; authorization paths are byte-identical. 27 new tests green; adjacent suites green modulo pre-existing failures. Live capable-model A/B BLOCKED (provider 402, credit exhaustion) → behavioral delta UNKNOWN; deterministic mechanism fully proven. No fallback was exercised; fixture intact; no production-workspace live runs.

## 2. Scope

`wisp/capability_filter.py` (new), `wisp/config.py` (flag: schema entry + field + parse + fingerprint), `wisp/core/stateless.py` (two 8-line wiring blocks), `tests/reliability/test_13i2_capability_filter.py` (new). Nothing else.

## 3. Architecture Before

USER TASK → MODEL SEES ALL (42/43 + extensions) → MODEL CHOOSES → HOST AUTHORIZES → EXECUTE. Proven in I1 via provider-capture rounds across full/auto_edit/read_only/ask_all: 42 schemas every mode.

## 4. Architecture After

USER TASK → HOST permission_mode → FILTER PROVIDER-BOUND SCHEMAS (flag-gated) → MODEL SEES PARTITION → MODEL CHOOSES → HOST AUTHORIZES (unchanged) → EXECUTE. With flag OFF the middle step is identity: byte-identical legacy behavior (asserted).

## 5. Filter Location

`WispAgentCore.turn()` (`stateless.py`, provider-schema assembly, after role filtering) and `_build_system_prompt` (tools-menu construction, same helper). Single shared pure function; the ONLY production consumer of `filter_schemas_for_mode` is `stateless.py` (asserted). Verified single-funnel: no entry path (REPL/single-shot/headless/agent/composition/direct runtime) constructs provider schemas — all reach `core.turn()`.

## 6. READ_ONLY Capability Definition

`capability_filter.READ_ONLY_TOOLS`: literal auditable 14-name frozenset, pinned equal to enforcement set `infra.security._SAFE_READ_TOOLS` by test (drift in either file fails loudly). Verified against code, not copied from the report. Contents: read_file, list_files, search_codebase, search_symbols, git_status, git_diff, lsp_diagnostics, lsp_definition, lsp_references, lsp_hover, lsp_symbols, web_fetch, web_search, recall.

## 7. Mode Matrix

READ_ONLY → 14 (implemented). FULL / AUTO_EDIT / ASK_ALL → passthrough, behavior preserved (asserted content+order equality). Non-existent modes (analysis, repair, offline-secure, read-only-review, enterprise-managed) NOT invented. Child roles untouched (`_effective_child_tools` path unchanged; researcher set byte-identical with flag OFF asserted; with flag ON + read_only the composition is narrowing intersection, documented).

## 8. Feature Flag

`capability_filtering`, existing `SETTINGS_SCHEMA`/`get_setting`/`_parse_bool` mechanism reused (no new framework). Host-controlled (config/env `WISP_CAPABILITY_FILTERING`); no model write path (model cannot set config; CLI/env only).

## 9. Default Behavior

OFF. No project convention requires safety-visibility ON (contrast `verification_loop`, which defaults True but governs enforcement, not visibility). OFF → exact legacy surface (asserted: 42 schemas, identical order). Rollback = flag off; needs no code change (asserted). Documented here and in the schema description.

## 10. Provider Schema Snapshots

| mode | flag | count | bytes | tok |
|---|---|---|---|---|
| read_only | ON | 14 | 7,317 | 1,829 |
| read_only | OFF | 42 | 27,542 | 6,885 |
| full | OFF | 42 | 27,542 | 6,885 |
| auto_edit | OFF | 42 | 27,542 | 6,885 |

Test asserts 14 names == READ_ONLY_TOOLS and byte band 6,500–8,200.

## 11. Schema Economics

FULL 27,542 B / 6,885 tok → READ_ONLY 7,317 B / 1,829 tok: −73%, −5,056 tok/round. Projected (schemas resend per round): 10 rounds ≈ 50k, 25 ≈ 126k, 50 ≈ 253k tok avoided. No billing claims (no provider accounting).

## 12. Deterministic Test Results

27/27 green: F1 determinism, F2 mutation hidden, F3 run_bash hidden, F4 all-12 delegation hidden, F5 14 retained (+`diagnose` nuance pinned: READ-risk but not safe-set, hidden), F6 unknown names fail closed, F7 input list untouched, F8 dicts shared-not-copied, F9 FULL identical, F10 AUTO_EDIT/ASK_ALL identical; menu/provider/cache consistency; child-role parity; thin+read_only → empty without crash.

## 13. Negative/BYPASS Tests

Fake provider emitting `fanout` under READ_ONLY+ON → turn yields Blocked refusal, no execution. Bypass survey: only `stateless.py` consumes the filter; all entry paths funnel through `core.turn()` (asserted by consumer scan). Runtime additions (`read_files_batch`) and `skill__*` names fail closed under READ_ONLY (not in allowlist).

## 14. Live A/B Methodology

Driver `/tmp/i2ab.py` (not repo): direct `OpenRouterProvider(model=nex-agi/nex-n2.5-pro:free)` — explicit, no fallback chain; fixture repo; temp HOME; deny-all approval (proposals observed, zero executions); per-cell fixture checksum; abort-on-402. Cells planned: survey OFF/ON, parallel OFF/ON, bug-hunt, implementation.

## 15. Live A/B Results

BLOCKED at first cell (survey-OFF): provider 402, free-credit balance exhausted → STOP per protocol. Zero live I2 cells completed. No fallback attempted, no model substituted, no retries. Fixture checksums intact (deny-all held; 402 fired pre-tool).

## 16. Model/Provider Details

Requested: OpenRouter / nex-agi/nex-n2.5-pro:free. Temperature unrecorded (run never reached generation). Fallback: none exercised (direct provider object; failure mode was explicit STOP-402, verified in output). I1 local-leg reference: llama3.2:3b, 0 delegation tendency all cells.

## 17. Performance

Filter latency ~10ns/schema-set (timeit, n=1000) — negligible vs provider latency; no cache introduced (stale-surface hazard class avoided by construction). Payload: −20,225 B/round in READ_ONLY+ON. Memory: one ref-list per turn, freed after.

## 18. Security

Authorization byte-identical: `policy_hard_deny`, engine rules, `ToolExecutor`, `ToolRisk`, `write_tools` untouched (diff proves). Executor regression: fanout/write_file/run_bash attempted in READ_ONLY → still POLICY_DENIED/error/blocked (3 parametrized tests). hidden ≠ authorized and visible ≠ authorized both demonstrated. Fingerprint includes the flag (no stale-schema core reuse across flips).

## 19. H0/I0/I1 Regression

H0 suite, I1 surface suite (22+2), registry, approval×3, fanout resilience, auth, subagent orchestrator, skills, task manager, H-forensics, H2 determinism, graph terminality, retry×2, workspace: 366 passed; 2 failed — `test_all_tools_have_schemas` cross-test pollution (MY harness left `TOOL_IMPLS` entry behind; fixed by restoring both sides, re-verified 51 green) and FirstTokenDeadline timing (HEAD-confirmed pre-existing). G0 killpoints not run (destructive, unrelated surface).

## 20. Skill Interaction

Boundary demonstrated by test: `skill__*` schemas hidden under READ_ONLY+ON; `SkillExtension.call_tool` body path untouched. Residual documented: invoked bodies (≤50KB) can still carry delegation concepts — separate gate, not solved here.

## 21. Runtime Registry Interaction

Filter operates over the live collection (`_get_tool_schemas()` output: base + runtime appends + extensions), never assuming 42. Unknown names fail closed. Global `TOOL_SCHEMAS` never mutated by filtering (identity + length asserts). `read_files_batch` verified hidden in READ_ONLY, present in FULL.

## 22. Findings

- F-1 (mechanism, done): host-owned READ_ONLY partition enforced at both provider and menu surfaces, deterministic, reversible.
- F-2 (process): my harness initially polluted the global registry (schema popped, impl left) breaking an unrelated suite — fixed, both-sides restore; lesson: global-mutation tests must restore ALL registries they touch.
- F-3 (blocking): capable-model behavioral A/B requires funded credentials; free-tier 402 stops the protocol. The mechanism evidence is complete; the behavioral delta is not.

## 23. Unknowns

Behavioral proposal delta on a capable model (BLOCKED, needs funded key + explicit opt-in); ladder no-done anomaly (I1 standing); child-round schema capture per role; thin+read_only empty-surface UX (no crash proven, usefulness untested).

## 24. Residual Risks

Flag default OFF means zero production effect until rollout (intended); a future default-ON flip needs its own A/B. Skill-body injection bypasses schema hiding by design. `ask_all` has no partition (preserved behavior). Runtime registry appends remain unaudited at startup.

## 25. Recommendation for I2.1

Funded-credential A/B: survey OFF vs ON + parallel-task OFF vs ON on the incident model class using this phase's driver (add a pre-flight credit-balance check so 402 stops the run BEFORE the first cell, not mid-cell). If ON suppresses delegation proposals without harming direct-task completion, proceed to default-ON rollout plan + AUTO_EDIT delegation calibration (I1-open question). Do NOT build intent inference first.

```text
VERDICT: PASS WITH KNOWN UNKNOWNs
NEXT PHASE: 13-I2.1
```
