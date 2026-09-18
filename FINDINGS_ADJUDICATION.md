# Findings Adjudication — Wisp

**Phase:** Judge (methodology step 4 of 5: *explore → model → verify → judge → change*)
**Inputs:** `REPOSITORY_INTELLIGENCE_REPORT.md` (§1–21), `repository_manifest.json`
**Repository:** `/Users/philosopher/Documents/wisp` @ `main` `5ea0ed9`, working tree dirty
**Discipline:** no code changed. This document judges; it does not fix.

---

## Verdict in one paragraph

The system is **better built than its documentation claims and better documented than its enforcement**. The model-authority path is genuinely chokepointed, the graph engine's durability is unusually rigorous, error handling is honest, and there are only 12 TODO markers in 84 K lines. The real problems are not in the agent loop — they are at the **edges**: a documented hook-persistence mitigation that is bypassable over HTTP (§A1), a strict type gate that is red on tracked files so CI cannot pass (§C1), a test suite that cannot complete on a developer machine (§C2), and a set of prior reconnaissance documents that now contain demonstrably false claims and will mislead the next engineer who reads them (§E1). **One finding is a genuine security defect; the rest are process, hygiene, or documentation debt.** Nothing here justifies an architectural rewrite.

### Severity rubric used

| Level | Meaning |
|---|---|
| **S1** | Unauthenticated/remote compromise, or a headline guarantee broken with wide blast radius |
| **S2** | Authenticated privilege escalation, or a documented mitigation bypassable by a normal actor |
| **S3** | Correctness/robustness defect or broken quality gate with bounded blast radius |
| **S4** | Hygiene, documentation, dead code, or consistency |

### Classification used

**DEFECT** = behaves contrary to intent or to a stated guarantee · **DESIGN** = deliberate, defensible trade-off · **ARTIFACT** = environment/measurement noise, not the codebase · **NON-ISSUE** = verified correct (including two of my own Phase 1 errors)

---

## A. Security findings

### A1 — `POST /api/hooks` persists an unvalidated shell command, executed without approval, in the directory the agent is denied — **DEFECT, S2**

This is the one finding I would act on first. The full chain, all verified:

| Step | Evidence |
|---|---|
| Route is gated by **API key + rate limit only** — no `require_tool_allowed`, no policy gate, no approval | `server/routes/hooks.py:75` (confirmed by the full 41-route audit, §B) |
| The `command` field has **no content validation** — only the *name* is sanitized | `hooks.py:41` (`command: str = Field(..., min_length=1)`) vs `_validate_hook_name` (`:25-35`, name only) |
| It writes into `<workspace>/.wisp/hooks/*.json` | `hooks.py:108-111` |
| Hooks execute via **`subprocess.run(cmd, shell=True, ...)`** | `infra/hook_types.py:350-358` |
| Hook events include `PRE_TOOL_USE`, `PRE_BASH`, `SESSION_START` — so it fires on the next tool call or session start, **with no approval step** | `hooks.py:87-95` event map |
| **The agent itself is denied this exact write by three independent guards** | `tools/_utils._is_hook_controlled_path`; `authorize()` L4 (`auth/decision.py:94-99`); the danger deny-list (`tools/_utils.py:236-243`) |
| **The threat model names this exact threat and claims this exact mitigation** | `docs/THREAT-MODEL.md`: "Malicious hook persistence → Hook-dir mutation guard in `authorize()` L4" |

**Why this is a defect and not a design choice.** The counter-argument is that the API key *is* the authorization and the developer is trusted. That argument would hold if the hook directory were treated as ordinary configuration. It is not: the codebase treats it as a protected boundary with three redundant guards, and the threat model lists hook persistence as a named risk. A mitigation that holds on three paths and not on the fourth is a **coverage gap**, not a policy. The privilege gradient makes it sharper — a hook is strictly more privileged than the file writes that *do* get gated (`routes/files.py:122-172`), because writing a hook yields shell execution on every subsequent tool call.

**Honest blast-radius bound.** Preconditions are a valid `WISP_API_KEY` (`~/.config/wisp/auth_keys.json`, mode 0600) **or** an explicitly-warned `--no-auth` loopback boot. The server binds `127.0.0.1` by default and `SystemExit(2)` refuses to boot unauthenticated (`server/main.py:157-197`). So this is **not** unauthenticated or remote RCE. It is an authenticated principal gaining a capability the agent is deliberately denied, via a route that skips the documented control.

**Narrow residual model→authority path:** with `--no-auth` loopback **and** an approved `run_bash`, the agent could `curl` its own server to write a hook — bypassing the L4 guard entirely. This is the scenario `docs/THREAT-MODEL.md` does not enumerate.

*Confidence: HIGH. Every link read directly; the audit in §B is reproducible.*

### A2 — 35 of 41 mutating REST routes carry no tool-policy gate — **DESIGN (with one exception already counted in A1)**

My Phase 1 report named *three* ungated routes. That was a **spot sample and understated the picture**. The systematic audit:

```
TOTAL mutating routes (POST/PUT/PATCH/DELETE): 41
WITHOUT require_tool_allowed AND without a policy gate: 35
WITH a tool gate: 6  — files.py (5: create/edit/binary/rename/delete) + bash.py (1)
```

So `require_tool_allowed` is applied to exactly the routes that write files and run shell — i.e. the routes that mirror agent *tool* operations. Everything else (`/api/policy/publish`, `/api/policy/revoke`, `/api/workspace`, `/api/context`, `/api/models/select`, `/api/plugins/*`, `/api/mcp/*`, `/api/sessions/*`, `/api/hooks`) relies on the API key alone.

**Judgement: this is a defensible design, with one genuine exception.** `require_tool_allowed`'s own docstring explains its scope — *"REST has no human to approve, so approval-required verdicts deny — same as ApprovalGate with no handler"* (`server/deps.py:385-407`). It exists to substitute for the interactive approver on *tool-equivalent* operations. Administrative configuration endpoints are a different category, and the threat model declares the developer trusted. The exception is A1, where the thing being configured is itself a control.

**Consequence for the docs:** the README's framing *"`ToolExecutor` is the only action path"* is true **for the model** and false for the REST control plane. That distinction should be written down, because as it stands a reader reasonably concludes the policy layer covers the API.

### A3 — Control characters accepted by two containment re-implementations — **DEFECT, S4**

Differential test: 6 implementations × 12 adversarial inputs. Traversal, symlink escapes, sibling-prefix tricks, and absolute-outside-root are rejected **everywhere** — the boundary holds. One divergence:

| Input | canonical `pathsec` + 3 aliases | `server/routes/files._resolve_path` | `sandbox.resolve_sandbox_cwd` |
|---|---|---|---|
| `"ok\x01.txt"` (control char) | **DENY** | **ALLOW** | **ALLOW** |

`pathsec.py:27-30` rejects control characters deliberately — its comment cites terminal/log-injection smuggling through any path that gets displayed. The two re-implementations (`server/routes/files.py:40-62`, `sandbox/__init__.py:51-66`) lack that check. Real but low: it requires reaching the route with such a path, and the impact is display/log injection rather than escape. *Confidence: HIGH (executed).*

### A4 — The WebSocket route bypasses the rate limiter — **DEFECT, S4**

`/ws/agent` (`routes/agents.py:67`) authenticates per-frame (`:131-147`) rather than through the FastAPI dependency, so `RATE_LIMITER` — which is a `dependencies=[...]` entry on the REST routes — never applies. Also, `server/connections.py:79 ConnectionManager` exists but is dead relative to the mounted handler, which builds its own `WebSocketTransport` (`agents.py:82`). *Confidence: HIGH.*

### A5 — `POST /api/mcp/servers` skips the workspace-trust consent gate — **DESIGN / CONSISTENCY, S4**

Verified but **weaker than it first appears**. The route persists a `command`/`args`/`env` and, when `always_load` is set, connects immediately (`routes/mcp.py:104-115`) — spawning the process. It consults no trust manager (zero hits for `trust`/`consent`/`always_load`/`allow_auto` in the route file). However, the manager's clone-and-run gate applies only to `config.source == "workspace"` (`mcp/manager.py:820-835`), and this route writes a **global** config — which that comment explicitly says was never trust-gated ("on-demand and global servers are unaffected; execution still goes through tool approval"). And MCP tool *calls* are still forced through approval (`tool_executor.py:1218-1223`). So: the gate isn't bypassed so much as scoped elsewhere. Worth documenting; not worth escalating.

### A6 — Verified-correct security properties (so they aren't re-litigated)

- **The model authority path is genuinely chokepointed.** `stateless.py:1840` is the sole delegation to `ToolExecutor.execute`; with no executor wired the fallback is risk-gated to `ToolRisk.READ` only (`:1846-1887`). Subagents share the executor; `wisp/graph/` contains zero subprocess/`execute_tool` calls.
- **`authorize()` is a real 6-layer narrowing model** that names the denying layer (`auth/decision.py:41-139`).
- **Graph validator output is advisory, `authorize()` is the law** — the runner passes data, never authority.
- **MCP tool calls cannot bypass approval** and are hard-blocked in READ_ONLY.
- **Sandbox fails closed**: Docker → Pty → Noop with a loud per-call `UNCONFINED` warning.
- **0 bare `except:`** across 1,437 handlers.
- The dangerous-command deny-list is **correctly documented as a heuristic, not a boundary** (`tools/_utils.py:79`).

---

## B. Gate-coverage matrix (evidence for A1/A2)

Reproducible via AST walk of `wisp/server/routes/*.py` decorators + body scan for `require_tool_allowed` / policy calls.

| Router | Mutating routes | Tool-gated |
|---|---|---|
| `files.py` | 5 | **5** |
| `bash.py` | 1 | **1** |
| `hooks.py` | 3 | 0 |
| `policy.py` | 2 | 0 |
| `mcp.py` | 3 | 0 |
| `plugins.py` | 3 | 0 |
| `sessions.py` | 3 | 0 |
| `review.py` | 3 | 0 |
| `arena.py` | 2 | 0 |
| `diff.py` | 2 | 0 |
| `runs.py` | 2 | 0 |
| `background.py` | 2 | 0 |
| `context.py`, `git.py`, `jsonrpc.py`, `models.py`, `prompt.py`, `search.py`, `swarm.py`, `workspace.py`, `codebase.py`, `complete.py` | 1 each | 0 |

All 41 carry `verify_api_key`. 33 of 41 also carry `RATE_LIMITER`.

---

## C. Quality-gate findings

### C1 — The strict mypy gate is red on tracked files, so CI cannot pass — **DEFECT, S3**

`uv.lock` pins mypy **2.3.1**; CI installs `mypy>=1.10` unpinned, so it resolves to 2.3.1 or newer. Running mypy 2.3.1 produces the **identical 16 errors** in `core/runtime.py` and `core/stateless.py` and **exit 1**. Both files are tracked, so this is not working-tree noise.

Root cause is a **ratchet applied out of order**: `pyproject.toml:65` lists `wisp/core/stateless.py` and `wisp/core/runtime.py` in the strict gate, but they were never annotated. Both the pyproject comment (`:64`, "Ratchet target: `wisp/multi_agent/task.py`, then the transport layer") and the CI comment (`ci.yml:44-47`, naming only `events.py`/`protocol.py`/`base.py`) still describe an earlier, smaller gate — so the two comments and the file list disagree with each other and with reality.

The errors are mundane (`no-untyped-def`, `type-arg`, `no-any-return`, one stale `type: ignore` at `stateless.py:1175`). This is a *process* defect — a hard gate that is known-red is not a gate. *Confidence: HIGH (executed at the pinned version).*

### C2 — The full test suite cannot complete on a developer machine — **DEFECT, S3**

Two independent blockers:

1. **Collection errors abort the run.** Three untracked test files import symbols that no longer exist (`wisp.multi_agent.delegation`, deleted in `11fc949`; `_has_unclosed_brackets`, removed from `transport/cli.py`). pytest aborts by default: `3 errors in 1.49s`, **zero tests executed**. `AGENTS.md:96-102` documents the `--ignore` list, but nothing enforces it.
2. **With the documented `--ignore` flags, the run hangs at 59%** on exactly one test: `tests/test_provider_select.py::TestProviderCommand::test_switch_requires_key_when_missing` (`:210`). It deletes the env keys, calls `cmd_provider("openai")`, and **never mocks `getpass`** — so `_ensure_api_key` (`repl/commands/provider.py:343`) blocks forever on `/dev/tty`. Confirmed by isolation (no completion in 40 s).

**CI is unaffected** (no TTY → `getpass` raises → `_ensure_api_key` catches and returns `False`), and the collection errors are untracked files absent from a clean checkout. So this is a **developer-velocity** defect: the feedback loop the methodology depends on is broken locally while appearing green in CI. That asymmetry is itself the risk — it hides problems from exactly the people who would fix them. *Confidence: HIGH.*

### C3 — 25 apparent test failures are environment artifacts — **ARTIFACT (my measurement error)**

Reported in Phase 1 as 25 failures. All 25 are artifacts of **this checkout's** environment, and **zero are product defects**:

| Group | Count | Cause | Proof |
|---|---|---|---|
| policy suite (`policy_modes`/`policy_routes`/`policy_bundle`/`enterprise_integration`) | 22 | `.venv` is missing `cryptography` (declared at `pyproject.toml:27`) → `ModuleNotFoundError` at `policy/bundle.py:84` | All pass with `cryptography==50.0.1` supplied in an isolated env |
| `cli_surface_e2e` TestGroup3Headless | 3 | ambient `HTTP_PROXY` leaks into the hermetic child (`test_cli_surface_e2e.py:52` filters only 3 vars) → localhost calls hit a proxy → `502` | All 38 pass with proxy vars unset |

**Also found:** the project `.venv` is missing **4 of 14 declared dependencies** — `aiohttp`, `tiktoken`, `prompt_toolkit`, `cryptography`. Whether that drift exists on the maintainer's machine or is specific to this checkout is **not knowable from here**.

*Secondary observation:* the hermetic env builder filters `WISP_API_KEY`/`VIRTUAL_ENV`/`PYTHONHOME` but not `HTTP_PROXY`/`HTTPS_PROXY`, so that test is fragile on any machine behind a proxy. A test-hermeticity gap, not a product defect.

---

## D. Correctness and concurrency findings

| # | Finding | Class | Sev | Evidence | Why this severity |
|---|---|---|---|---|---|
| **D1** | `BackgroundAgentManager.send()` spawns without admission, bypassing the 8-running bound | DEFECT | **S3** | `launch()` gates at `background.py:255-263`; `send()` calls `asyncio.create_task` at `:513` with no check | A documented bound silently not holding; bounded impact (resume requires a *finished* agent) |
| **D2** | `GraphExecutor` unlocked lazy init (`_stores()` assigns `_store`/`_artifacts` with no lock, `:91-96`); `_cancelled`/`_approvals` keyed by run id and never pruned (`:82-85`) | DEFECT | **S3** | read | Long-lived executor leaks per-run entries; concurrent `run()`/`resume()` can double-create |
| **D3** | `BackgroundAgentManager._entries` + telemetry rings grow unbounded unless `prune()` is called explicitly; `launch` never auto-prunes; `_subscribers` queues unbounded | DEFECT | **S3** | `background.py:563-573`, `:127-136` | Server-lifetime memory growth under repeated delegation |
| **D4** | Telemetry ring `append` recomputes `sum(len(...))` over the deque under the lock → O(n²) amortised; byte accounting counts chars, not UTF-8 bytes | DEFECT | **S4** | `telemetry.py:142-145`, `:132` | Bounded by the 500-event/256 KB caps; a constant-factor inefficiency |
| **D5** | TUI task ownership is partial — three bare `asyncio.create_task` remain despite `OwnedTasks` existing to prevent leaks | DEFECT | **S4** | `tui/screens/workspace.py:195,418`, `tui/data/ws_client.py:47` | The module docstring states the exact failure mode it is meant to prevent |
| **D6** | `_auto_retry_safe` body duplicated verbatim after its `return` — unreachable duplicate | DEFECT (cosmetic) | **S4** | `subagent_orchestrator.py:990-1026` | Signals an interrupted refactor; no behavioural effect |
| **D7** | `_CONTEXT_TTL` is unbounded | **NON-ISSUE** | — | `_ttl_get` keys by `kind`; 3 fixed literals (`stateless.py:1445,1542,1654`) | **My Phase 1 error.** Bounded to 3 entries. No lock, but the stored key is compared before serving → a race costs a miss, never a wrong-workspace result |
| **D8** | Verification gate satisfiable by a malformed result | **NON-ISSUE** | — | `_format_bash_output` emits `[exit code: N]` **only on non-zero exit** (`bash.py:39-40`) | **My Phase 1 error.** Driving the real formatter into the real guard gives green→verified, red→not |
| **D9** | Verification gate depends on an undocumented cross-module contract | RISK (coupling) | **S4** | `verification.py:123` ↔ `bash.py:39-40` | Correct today; inverts silently if the formatter ever emits the code unconditionally |
| **D10** | `run_bash` result whose stdout begins with the literal `[exit code: N]` is a false negative | DEFECT (edge) | **S4** | executed: `resolved()` stays False | Contrived; needs a command that echoes that exact prefix |
| **D11** | Sandbox has two routing mechanisms (`get_sandbox` in `bash.py:78` vs `get_router` in `primitives.py:83`) | DEFECT | **S4** | read | Divergence risk in which tier runs a command |
| **D12** | Sandbox failover is silent at the decision point | DESIGN | **S4** | `router.py:214-246` | Mitigated: a loud per-call `UNCONFINED` warning is emitted |

---

## E. Documentation and hygiene findings

| # | Finding | Class | Sev | Detail |
|---|---|---|---|---|
| **E1** | **Prior reconnaissance docs contain false claims** | DEFECT (docs) | **S3** | `WISP_ARCHITECTURE_HEALTH.md` P1-1 names `core/agentic_graph.py` — **the file does not exist**; P1-2 names an 8-module arena/server cycle — **not reproducible** (my Tarjan run found a different 7-SCC set). These are the *headline* findings of that document. Highest-value doc fix: an engineer acting on them wastes a cycle chasing a removed subsystem. |
| **E2** | `ARCHITECTURE.md` / `AGENTS.md` reference removed code | DEFECT (docs) | **S4** | `multi_agent/delegation.py` + `DelegationAnalyzer` (deleted in `11fc949`); `_MockIO` (does not exist); "310 test files, ~4,200 tests" (actual 376 / 5,845); tests-mirror-source-paths (they don't) |
| **E3** | Dead modules with no importer | DEFECT (dead code) | **S4** | Zero importers: `core/speculative/` (3 files), `multi_agent/resource_budget.py`, `cli/commands/{doctor,model}.py`. Test-only: `coding.py`, `core/graph/`, `core/context/repomap.py` (the last is already documented as experimental in an uncommitted docstring) |
| **E4** | `setup.py` contradicts `pyproject.toml` | DEFECT (hygiene) | **S4** | `python_requires=">=3.10"` vs `>=3.11`; duplicate entry point |
| **E5** | Generated artifacts tracked despite `.gitignore` | DEFECT (hygiene) | **S4** | `.coverage`, `graphify-out/*`, `qa-results/report.md`, `.wisp/mcp.json` |
| **E6** | Two orphan satellite trees | DEFECT (hygiene) | **S4** | `wisp-ts/` and `agent/` untracked; `agent/` has zero imports in either direction with `wisp/` |
| **E7** | `wisp/test_distill.py`, `wisp/test_runner.py` are production modules whose names match pytest's collection glob | DEFECT (hygiene) | **S4** | Harmless today (0 `def test_`), but collected by a bare `pytest` from the root |
| **E8** | Three CLI dispatch layers coexist | DESIGN | **S4** | Documented strangler-fig; `__main__` table is authoritative for process entry, `Dispatcher` for slash names, `repl/commands/` the fallback |
| **E9** | `mypy` gate covers 8 of 366 files (2%) | DESIGN | **S4** | "Strict" overstates coverage — but the ratchet direction is right; see C1 |

---

## F. Ranked action queue

Ordered by *consequence if left alone*, not by ease. Nothing here is a rewrite.

| Rank | Action | Finding | Sev | Why this rank |
|---|---|---|---|---|
| **1** | Decide whether `POST /api/hooks` should honour the hook-dir guard (or document why the API key is sufficient) | A1 | S2 | Only genuine security defect; a documented mitigation is bypassable; writing a hook yields unapproved shell execution |
| **2** | Get the mypy gate green — annotate the two files or narrow the `files` list back; then reconcile the two stale comments | C1 | S3 | A known-red hard gate is not a gate; it trains people to ignore CI |
| **3** | Make the suite completable locally — mock `getpass` in `test_provider_select.py:210`; delete or fix the 3 stale test files | C2 | S3 | The local feedback loop is broken while CI looks green; that asymmetry hides problems |
| **4** | Correct or retire the stale prior recon docs (`WISP_ARCHITECTURE_HEALTH.md`, `WISP_CODEBASE_CENSUS.md`, `WISP_DEPENDENCY_MAP.md`) | E1 | S3 | They assert removed subsystems as headline risks; actively misleading |
| **5** | Restore the missing `.venv` dependencies (`pip install -e ".[dev]"`) | C3 | S3 | Environment only, but it invalidates every local test run until fixed |
| **6** | Apply admission in `send()`, or document that the bound is launch-only | D1 | S3 | Bounded guarantee that silently doesn't hold |
| **7** | Prune per-run state (`_cancelled`/`_approvals`, `_entries`, telemetry rings) | D2, D3 | S3 | Server-lifetime growth under repeated delegation |
| **8** | Unify the two containment re-implementations onto `pathsec` (control-char check) | A3 | S4 | One helper, one semantics — the codebase already has the canonical version |
| **9** | Document the real boundary: policy layer covers tool-equivalent routes; the API key covers the rest | A2 | S4 | Prevents the next engineer re-deriving this from scratch |
| **10** | Sweep hygiene: dead modules, `setup.py`, tracked artifacts, orphan trees, the `_auto_retry_safe` duplicate | E3–E7, D6 | S4 | Cheap, low-risk, improves signal |
| **11** | Document the `bash.py`↔`verification.py` contract in one place | D9 | S4 | Prevents a silent inversion |
| **12** | Cover the 8 server routes with no test file (`codebase`, `complete`, `diagnostics`, `jsonrpc`, `models`, `plugins`, `suggestions`, `swarm`) | — | S4 | Test desert at the API edge |

---

## G. What I would **not** change (and would defend)

Explicitly listed because "found a difference" is not "found a bug", and a judge who cannot say *don't touch this* is not judging.

| Property | Why leave it |
|---|---|
| **The `ToolExecutor.execute` → `authorize()` chain** | Verified single enforcement point for the model path; 6-layer narrowing with layer attribution; fail-closed. Highest-value asset in the repo. |
| **The graph engine's durability model** | Fingerprint pinning, per-attempt idempotency keys, stale-worker rejection, "recorded terminal failures are not trusted on resume", terminal precedence where a join timeout can never become SUCCESS. Genuinely rigorous — this is the model other subsystems should copy. |
| **Honest terminal paths** | The code prefers "no `done` + a typed error" over a fabricated success (`provider_stream.py:269-281`, `stateless.py:703-726`). Rare and valuable. |
| **The ContextVar sub-event channel** | `tool_executor.py:870-877` explains why an instance slot would let child A's events clobber child B's. Correct pattern for a shared executor. |
| **Broad `except Exception` in loop/transport code** | 802 handlers, 0 bare. A deliberate resilience posture in paths that must not die mid-turn, and they log. |
| **The 31-branch `__main__` dispatch table** | Structural complexity (a branch table), not algorithmic. Ugly; not a defect. Refactoring it is a taste decision, not a correctness one. |
| **`require_tool_allowed` not covering config routes** | Defensible: its docstring scopes it to substituting for a human approver on tool-equivalent operations. Only A1 breaks the pattern. |
| **The dangerous-command deny-list being a heuristic** | Correctly documented as such (`tools/_utils.py:79`); the real confinement is the sandbox tier + workspace containment. |
| **`_CONTEXT_TTL` being a plain dict** | Bounded to 3 keys; the unlocked write is benign because the stored key is compared before serving. |
| **Three CLI dispatch layers** | A documented, in-progress strangler-fig migration. Deleting the legacy layer before the dispatcher covers all names would be the actual regression. |

---

## H. What would change my mind (falsifiers)

Stated so the judgements above are testable rather than rhetorical.

| Judgement | Falsified if… |
|---|---|
| A1 is the only genuine security defect | a route or tool path is found that reaches `subprocess`/`shell=True` **without** auth, approval, or the deny-list — I swept `wisp/` for subprocess sites and found 37 modules, all of which I attributed to tools/sandbox/git/lsp/benchmark, but I did not trace every call site to its gate |
| A1's blast radius is bounded by the API key | the key is discoverable by, or exported to, anything model-influenced (e.g. a workspace `.env`, a subprocess env, or a log). I checked that subprocess envs are credential-stripped but did not audit every env-construction site |
| A2 is a design choice, not a defect | a documented intent exists that config routes *should* pass the policy layer — I found no such statement, but I also did not read all 95 docs |
| C1 means CI is red | CI's resolved mypy version differs in a way that changes the 16 errors. I tested 2.3.1 (the `uv.lock` pin) and the local 1.20.2 — both give 16 identical errors, so version sensitivity is unlikely but not exhausted |
| C3 is purely environmental | the `.venv` drift also exists in CI — impossible, since CI does `pip install -e ".[dev]"` |
| D1's bound is actually exceeded | `send()` is unreachable for an agent that isn't already finished — true by construction (`background.py:477-482`), but N finished agents can still be resumed in parallel |
| The validator's O(N·E) cost matters | profiling at the real fan-out shows it's negligible. I carried forward a prior 512-fan measurement and did **no** independent profiling — this is the weakest claim in the report |
| Dead-module list is safe to delete | dynamic loading exists that I missed. I checked `importlib`/`__import__` sites and none name these modules, but TUI CSS/themes and `mcp_servers/` load by convention, so those are **not** safe to delete on my evidence |

---

## I. Residual uncertainty

Honest limits of this adjudication:

1. **No independent performance profiling was done.** Every performance claim is inherited or inferred. §H flags this as the weakest area.
2. **Not all 37 subprocess-touching modules were traced to a gate.** A1's uniqueness among security findings depends on that trace, which I did partially.
3. **I read 95 docs selectively.** A2's "design, not defect" verdict rests on absence of contrary intent, which is weaker evidence than presence of supporting intent.
4. **The `.venv` drift may be local to this checkout.** If it is, C3 is noise; if it is not, local development is broken for everyone.
5. **Two of ten Phase 1 questions were my own errors** (D7, D8). That is the strongest available argument that remaining "verified" findings deserve the same adversarial treatment before anyone acts on them.

---

*Adjudication complete. No code was modified. The next methodology step is "change last" — and per §F, the first change worth making is a decision about A1, not a patch.*
