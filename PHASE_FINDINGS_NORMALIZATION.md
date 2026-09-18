# Phase 0 — Findings Normalization
# Phase 1 — Dependency-Ordered Remediation Sequence

**Repository:** `/Users/philosopher/Documents/wisp` @ `main` `5ea0ed9`
**Input:** `FINDINGS_ADJUDICATION.md` (27 findings, F1–F27)
**Status:** analysis only — no code modified

---

## Preamble: three corrections to the brief's premises

The brief assumes 27 findings that are defects, and assumes widespread duplication requiring new canonical authorities. The evidence says otherwise on both counts. Stating this up front, because it changes what "done" means.

**1. Six of the 27 are not defects.** Two are **non-issues that were my own Phase 1 errors** (F-invalid-1, F-invalid-2 below) and three were **environment artifacts**. Under the brief's own Rule 1 — *preserve behavior where behavior is already correct* — fixing them would be fabrication.

**2. Most of the canonical authorities already exist.** The brief lists nine concepts to canonicalize. Checking each: **four are already canonical** (provider registry, provider protocol, graph engine, containment primitive), **two are layered by design and defensible** (events, validation), and **three have real duplication** (model listing, execution state, error taxonomy). The remediation is therefore narrower and more surgical than the brief anticipates — and the three real ones are more consequential than anything in the original list of 27.

**3. The most important finding was not in the 27.** Auditing the concepts revealed a genuine execution-state ambiguity inside a single module (F28, below). It is a better candidate for "canonical authority" work than most of F1–F27, so I have added it rather than ignoring it because it wasn't on the original list.

---

## Part A — Invalidated findings (must NOT be "fixed")

| ID | Original claim | Why it is invalid | Evidence |
|---|---|---|---|
| **INV-1** | `_CONTEXT_TTL` grows unbounded | **False.** `_ttl_get` keys the dict by `kind`; the only three call sites pass fixed literals, so it holds ≤3 entries. | `stateless.py:85,1445,1542,1654` |
| **INV-2** | The verification gate is satisfiable by a malformed result | **False.** `_format_bash_output` emits `[exit code: N]` *only* on non-zero exit, so `not startswith(...)` is correct. Verified by driving the real formatter into the real guard. | `tools/bash.py:39-40`, `verification.py:123` |
| **INV-3** | 25 test failures indicate defects | **False.** 22 from a missing declared dependency, 3 from ambient proxy leakage. All pass when corrected. | §C3 of the adjudication |

**These three are removed from the remediation set.** Fixing them would change correct behavior.

---

## Part B — The 27 findings, normalized

Columns: **Root cause** · **Layer** · **Boundary/contract affected** · **Remediation** · **Verification** · **Related**

### Cluster 1 — Authority & gate coverage (5 findings, 1 root cause)

> **Shared root cause:** *enforcement is applied per-route by hand, so coverage depends on the author remembering.* This is one architectural defect with five symptoms.

| ID | Sev | Location | Observed | Arch. implication | Root cause | Boundary affected | Remediation | Verification | Related |
|---|---|---|---|---|---|---|---|---|---|
| **F1** | S2 | `server/routes/hooks.py:75` | `POST /api/hooks` accepts an unvalidated `command` and persists it to `.wisp/hooks/`; the command later runs via `subprocess.run(shell=True)` with no approval | A documented mitigation is bypassable at the HTTP boundary; the agent is denied this exact write by three guards | Enforcement is a per-route decorator, not a route *class* | REST boundary ↔ host execution | Decide + apply: either route hook creation through the same policy check as `files.py`, or document the API key as sufficient | Targeted test asserting the chosen contract; `docs/THREAT-MODEL.md` reconciled | F2, F5 |
| **F2** | S4 | `server/routes/*` | 35 of 41 mutating routes carry no tool-policy gate; only `files.py`(5) + `bash.py`(1) do | README's "ToolExecutor is the only action path" is false for the control plane | Same as F1 — no route classification | REST boundary | Document the real boundary; classify routes (tool-equivalent vs admin) so coverage is checkable | A test enumerating routes and asserting each has a declared gate class | F1 |
| **F3** | S4 | `server/routes/files.py:40`, `sandbox/__init__.py:51` | Two containment re-implementations accept control characters that canonical `pathsec` rejects | Duplicate semantics for a security primitive | Canonicalization was applied to 3 of 5 call sites | Containment contract | Migrate both onto `pathsec.resolve_contained` | Differential test (6 impls × 12 inputs) as a permanent test | F17 |
| **F4** | S4 | `server/routes/agents.py:67` | WS auth is per-frame, so `RATE_LIMITER` never applies | Auth and rate limiting are applied by two different mechanisms | Same as F1 | REST/WS boundary | Apply the limiter to the WS accept path, or document the exemption | Test asserting the WS path is rate-limited or explicitly exempt | F2 |
| **F5** | S4 | `server/routes/mcp.py:80` | Route persists a global MCP config and connects it if `always_load`, consulting no trust manager | Trust gating is scoped to `source=="workspace"`; the route writes global config | Scope mismatch, not a bypass | Extension trust contract | Document the scope; if global configs should be trust-gated, do it in the manager not the route | Test pinning the intended scope | F1 |

### Cluster 2 — Quality gates (3 findings, 1 root cause)

> **Shared root cause:** *gates were declared before the code met them, and nothing detects the drift.*

| ID | Sev | Location | Observed | Arch. implication | Root cause | Boundary affected | Remediation | Verification | Related |
|---|---|---|---|---|---|---|---|---|---|
| **F6** | S3 | `pyproject.toml:65`, `core/runtime.py`, `core/stateless.py` | mypy strict exits 1 with 16 errors on **tracked** files at the locked version 2.3.1 | A hard gate that is known-red is not a gate; it trains people to ignore CI | Ratchet applied to the gate list ahead of the annotations; two comments describe an older gate | Typed-contract boundary | Annotate the two files, or narrow the list to what passes; reconcile both stale comments | `mypy` exit 0 | F27 |
| **F7** | S3 | `tests/test_provider_select.py:210` | Test deletes the env keys, calls `cmd_provider`, never mocks `getpass` → hangs on a TTY | Local feedback loop broken while CI looks green — the asymmetry hides problems | Test relies on absence of a TTY rather than mocking the prompt | Test/production contract | Mock `getpass`; make the test assert the no-key path deterministically | Test passes with a TTY present | — |
| **F8** | S3 | `.venv` | 4 declared deps missing (`aiohttp`, `tiktoken`, `prompt_toolkit`, `cryptography`) | Environment drift, not code | Not a code defect | — | `pip install -e ".[dev]"` | Full suite collects without import errors | INV-3 |

### Cluster 3 — Runtime correctness & state ownership (10 findings, 3 root causes)

> **Root cause 3a:** *per-run state is created but never reclaimed.*
> **Root cause 3b:** *bounds are enforced at one entry point and assumed everywhere else.*
> **Root cause 3c:** *correct behavior depends on an undocumented contract in another module.*

| ID | Sev | Location | Observed | Root cause | Boundary affected | Remediation | Verification | Related |
|---|---|---|---|---|---|---|---|---|
| **F9** | S3 | `multi_agent/background.py:513` | `send()` spawns without the admission check that `launch()` performs at `:255-263` | 3b | Background-agent lifecycle | Route `send()` through the same admission helper | Test: resume N finished agents, assert the bound holds | F11 |
| **F10** | S3 | `graph/executor.py:82-85,91-96` | Unlocked lazy `_stores()` init; `_cancelled`/`_approvals` keyed by run id, never pruned | 3a | Graph execution state | Prune on terminal transition; guard lazy init | Test: N completed runs leave no residue | F11 |
| **F11** | S3 | `multi_agent/background.py:563-573` | `_entries` + telemetry rings grow unless `prune()` is called; `_subscribers` queues unbounded | 3a | Background registry | Auto-prune on settle beyond `MAX_FINISHED_ENTRIES` | Test: settle >50 agents, assert the cap | F9, F10 |
| **F12** | S4 | `multi_agent/telemetry.py:142-145` | `append` recomputes `sum(len(...))` over the deque under the lock → O(n²); byte accounting counts chars not bytes | 3a | Telemetry ring | Track a running byte total incrementally | Benchmark or an assertion on the accounting | — |
| **F13** | S4 | `tui/screens/workspace.py:195,418`, `tui/data/ws_client.py:47` | Three bare `create_task` despite `OwnedTasks` existing to prevent exactly this | 3c | TUI task ownership | Migrate to `OwnedTasks.spawn` | Structural test (the module documents this pin) | — |
| **F14** | S4 | `subagent_orchestrator.py:990-1026` | `_auto_retry_safe` body duplicated verbatim after its `return` | 3c | — | Delete the unreachable duplicate | `ruff` + review | — |
| **F15** | S4 | `verification.py:123` ↔ `tools/bash.py:39-40` | The completion gate depends on bash omitting the `[exit code:` prefix on success | 3c | Verification contract | Document the contract at both ends, or share a constant | Test pinning both sides together | INV-2 |
| **F16** | S4 | `verification.py:123` | A successful command whose stdout begins with the literal prefix is a false negative | 3c | Verification contract | Parse the code instead of prefix-matching | Test with such a command | F15 |
| **F17** | S4 | `tools/bash.py:78` vs `tools/primitives.py:83` | Two sandbox routing mechanisms (`get_sandbox` singleton vs `get_router`) | 3c | Sandbox contract | Converge on one entry point | Test asserting a single router | F3 |
| **F18** | S4 | `sandbox/router.py:214-246` | Failover Docker→Pty→Noop is silent at the decision point | 3c | Sandbox contract | Emit the chosen tier as a decision record (the per-call warning exists) | Test asserting the decision is observable | F17 |

### Cluster 4 — Documentation & hygiene (9 findings, 2 root causes)

> **Root cause 4a:** *docs describe a past revision and nothing detects drift.*
> **Root cause 4b:** *artifacts accumulate without a reclamation step.*

| ID | Sev | Location | Observed | Root cause | Remediation | Verification |
|---|---|---|---|---|---|---|
| **F19** | S3 | `WISP_ARCHITECTURE_HEALTH.md`, `WISP_CODEBASE_CENSUS.md`, `WISP_DEPENDENCY_MAP.md` | Headline findings assert `core/agentic_graph.py` (absent) and an 8-module arena cycle (not reproducible) | 4a | Correct or retire; add a "verified at <sha>" header | Re-run the cited analyses |
| **F20** | S4 | `ARCHITECTURE.md`, `AGENTS.md` | Reference deleted `multi_agent/delegation.py`, `DelegationAnalyzer`, `_MockIO`; stale test counts | 4a | Correct; make test counts generated not asserted | Compare to live inventory |
| **F21** | S4 | `core/speculative/`, `multi_agent/resource_budget.py`, `cli/commands/{doctor,model}.py` | Zero importers | 4b | Delete (verify no dynamic loading first) | AST importer check |
| **F22** | S4 | `setup.py:10` | `python_requires=">=3.10"` contradicts `pyproject.toml:12` (`>=3.11`) | 4a | Delete `setup.py` or align it | Build check |
| **F23** | S4 | repo root | `.coverage`, `graphify-out/*`, `qa-results/report.md`, `.wisp/mcp.json` tracked despite `.gitignore` | 4b | Untrack + fix the ignore rules | `git check-ignore` |
| **F24** | S4 | `wisp-ts/`, `agent/` | Untracked orphan trees; `agent/` has zero imports either direction | 4b | Remove from the working tree | Import check |
| **F25** | S4 | `wisp/test_distill.py`, `wisp/test_runner.py` | Production modules whose names match pytest's collection glob | 4a | Rename, or set `testpaths` in pytest config | Bare `pytest` collects only `tests/` |
| **F26** | S4 | `__main__.py`, `cli/dispatcher.py`, `repl/commands/` | Three CLI dispatch layers | 4a | Documented strangler-fig — **do not force** | — |
| **F27** | S4 | `pyproject.toml:65` | The strict mypy gate covers 8 of 366 files (2%) | 4a | Same remediation as F6 | Coverage count |

### Cluster 5 — Newly identified (1 finding, added by this phase)

| ID | Sev | Location | Observed | Root cause | Boundary affected | Remediation | Verification |
|---|---|---|---|---|---|---|---|
| **F28** | **S3** | `contracts/run.py:12`, `graph/types.py:37`, `runs/record.py:14`, `multi_agent/background.py:35,79,601` | **Three run-state vocabularies that disagree on the terminal-success value.** `background.py` uses `completed`; `graph/` and `runs/` use `succeeded`; `contracts/run.py` uses `completed` while its consumers compare `succeeded`. One explicit adapter exists (`_STATUS_TO_RUN_STATE`, `background.py:79`) — and a second, contradictory inline mapping 500 lines later (`:601`) that renders the same constant as `completed` again. No translation layer exists between the vocabularies. | No canonical execution-state type; each subsystem named its own terminal state | **Execution-state contract** | Establish one canonical execution state; make every cross-vocabulary translation an explicit, single-sourced adapter | Test: assert every producer's terminal state normalizes to one value; assert the two `background.py` mappings are consistent |

**Why F28 matters more than most of F1–F27:** consumers are already split. `tools/subagent_tools.py:41,85,108` compares `"completed"`; `coding.py:239`, `graph/cli.py:160`, `graph/api.py:27` and `runs/record.py` compare `"succeeded"`. Any consumer that reads the wrong field **silently mis-classifies a terminal run as non-terminal** — a class of bug that produces no error, only wrong behavior. That is precisely the "two parts of Wisp disagree about this concept" condition the brief asks to eliminate.

---

## Part C — Correlation: which findings share a root cause

| Root cause | Findings | Count |
|---|---|---|
| **RC-1** Enforcement applied per-route by hand | F1, F2, F4, F5 | 4 |
| **RC-2** Gate declared before code met it; drift undetected | F6, F27 | 2 |
| **RC-3** Per-run state created but never reclaimed | F10, F11, F12 | 3 |
| **RC-4** Bounds enforced at one entry point, assumed elsewhere | F9 | 1 |
| **RC-5** Correct behavior depends on an undocumented cross-module contract | F13, F14, F15, F16, F17, F18 | 6 |
| **RC-6** Docs describe a past revision; drift undetected | F19, F20, F22, F25, F26 | 5 |
| **RC-7** Artifacts accumulate without reclamation | F21, F23, F24 | 3 |
| **RC-8** No canonical execution-state type | **F28** | 1 |
| **RC-9** Duplicate security primitive semantics | F3 | 1 |
| **RC-10** Test depends on environment rather than mocking | F7, F8 | 2 |

**Ten root causes produce 28 findings.** That ratio is the argument for the brief's Rule 2 (fix root causes, not symptoms): six of the clusters are single decisions with multiple symptoms.

---

# Phase 1 — Dependency-Ordered Remediation Sequence

Severity is **not** the ordering criterion. Ordering follows the brief's priority list (contract/authority → boundary → multi-source-of-truth → semantic duplication → dependency direction → correctness → reliability → performance → maintainability → cosmetic), with one addition: **ordering must respect the fact that a broken gate makes every later change unverifiable.**

### The critical insight for ordering

F6/F7/F8 (the gates) are only S3, but they gate the *verification* of everything else. Remediating F28 without a working type checker and a completable test suite means shipping an execution-state change **unverified**. So the gates come first despite being mid-severity.

### Ordered sequence

| Order | Finding(s) | Priority class | Why here |
|---|---|---|---|
| **0** | F8 (restore deps), F6 (mypy green), F7 (unmock-hang) | Reliability → *prerequisite* | Nothing after this can be verified while the suite cannot complete and the type gate is red. This is the only place where severity is deliberately overridden by dependency. |
| **1** | **F28** (canonical execution state) | Contract / authority violation | The only finding that is a *contract* violation with silent-failure consequences. Touching it changes types other work depends on, so it must precede dependent changes. |
| **2** | F1 (hooks gate) + F2 (document the real boundary) | Boundary violation | Highest-severity finding. F2 is its generalization: deciding the boundary is a prerequisite to applying it at F1. |
| **3** | F3 (containment), F17 (sandbox router) | Semantic duplication | F3 is duplicate semantics for a security primitive; F17 is the same shape in the sandbox. Both are "migrate consumers to the canonical implementation", which must follow the boundary decision. |
| **4** | F9, F10, F11, F12 | Runtime correctness → reliability | Independent of the above; fixing state reclamation after the state contract settles avoids rework. |
| **5** | F15, F16 (verification contract) | Correctness | Depends on F28 only in that both are "make an implicit contract explicit". |
| **6** | F13, F14, F18 | Reliability / maintainability | Local, low-risk, no dependents. |
| **7** | F21, F23, F24 (dead code, artifacts) | Maintainability | Deletion after canonicalization (brief Rule 3). |
| **8** | F19, F20, F22, F25, F26 | Documentation / cosmetic | Last: docs should describe the post-remediation state, not the pre-remediation state. Documenting first guarantees they are wrong again immediately. |
| — | F4, F5 | Document-or-enforce | Parked: each needs a scope decision, not code. |

### Deliberate non-ordering decisions

- **F4/F5 are parked, not sequenced.** Both reduce to "is the API key sufficient authorization for this class of operation?" — a policy answer. Writing code before that answer would encode a guess.
- **F26 (three CLI layers) is explicitly not remediated.** The brief's Rule 1 forbids rewriting working subsystems for stylistic consistency, and the migration is documented as in-progress. Forcing it would be the regression.
- **F19–F25 are last on purpose.** Documentation written before the code changes is stale on arrival — which is how F19/F20 happened in the first place.

---

## Summary

- **28 findings** (27 original + F28), **10 root causes**, **3 invalidated** and excluded.
- **4 of 9 canonicalization targets are already canonical**; the real work is **3 concepts** (F28 execution state, provider model listing, error taxonomy) plus **1 security primitive** (F3).
- The single most consequential finding (**F28**) was not in the original 27 and has silent-failure characteristics.
- Sequencing is dependency-driven, with the gates promoted to position 0 because they gate verification of everything downstream.
