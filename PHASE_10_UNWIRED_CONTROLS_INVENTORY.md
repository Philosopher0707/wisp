# Phase 10 — The Unwired-Controls Inventory, Re-Verified

**Repository:** `/Users/philosopher/Documents/wisp`
**Source of the list:** `docs/audit-2026-08-24.md:270` — *"Written-but-unwired controls (the dominant pattern, ≥12 instances)"*
**Why re-verified:** Phase 10 found a **13th** instance (the M4 policy bundle, `PHASE_10_M4_GOVERNANCE_UNWIRED.md`). If the pattern is dominant and the list is the only record of it, the list has to be current — otherwise the next person re-derives it, or worse, trusts it.

**Verdict: the list is half-remediated and was never maintained.** 5 of 12 are now wired, 6 remain unwired, 1 is in an unreachable module. Nothing on the list is marked in the code.

---

## 1. Method

For each named control: find every definition and every reference via AST + textual scan, then **separate a definition-only reference from a real consumer**. That distinction is the whole exercise — a symbol whose only reference is its own `def` line is unwired regardless of how many times its *name* appears.

Where the audit's name turned out to be an alias or a partial name, the real symbol was traced instead (§3).

---

## 2. The inventory

| # | Control (audit's name) | Audit | **Verified now** | Evidence |
|---|---|---|---|---|
| 1 | `scrub_sensitive_env` | unwired | ✅ **WIRED** | called at `infra/hook_types.py:264` |
| 2 | `_SENSITIVE_ENV_KEYS` | unwired | ✅ **DELETED — superseded duplicate** | see §3.1; the live implementation is `tools/_utils_env.py` |
| 3 | `DockerSandbox` (agent-side) | unwired | ✅ **WIRED** | constructed in the factory `sandbox/__init__.py:388` and reached by `sandbox/router.py:197` |
| 4 | `AuditLog.log_blocked` | unwired | ✅ **WIRED** | called at `tool_executor.py:1193` and `core/stateless.py:2270` |
| 5 | `_redact_sensitive_tool_args` | unwired | ✅ **WIRED** — real name `redact_sensitive_tool_args` | called at `transport/cli.py:746`, `transport/tui.py:53`, `cli/approval.py:167` |
| 6 | `spawn_with_guards` | unwired | ⚠️ **UNWIRED, but harmless** — superseded duplicate | the function at `subagent_orchestrator.py:1637` has no caller, **but its guards are live elsewhere**: depth at `:723`, depth+branching at `:1737-1753` |
| 7 | DAG `metadata["_budget"]` | unwired | ✅ **FIXED — now honored** | see §2.2; the runner applies it narrow-only |
| 8 | `execute_tool(security_policy=...)` | unwired | ⚠️ **STILL UNWIRED** | the only `security_policy=` occurrence in the repo is the parameter declaration at `tools/registry.py:907`; no caller passes it |
| 9 | `ToolRegistry.execute` | unwired | ⚠️ **STILL UNWIRED in production** | `composition.py:131` instantiates `ToolRegistry()`; `.execute()` is called only in `tests/test_tools_registry.py` |
| 10 | settings-table AUTO_EDIT default | unwired | ✅ **FIXED** | the schema default (`config.py:102`) *and* the resolution default (`config.py:674`) are both `AUTO_EDIT` — the "safe default loses at resolution" defect is gone |
| 11 | event-replay `TOOL_CALL` case | unwired | ❓ **REFERENT NOT IDENTIFIED** | see §3 — the two candidates are both wired |
| 12 | chain patch apply | unwired | ✅ **WIRED** | `subagent_orchestrator.py:922` → `_worktree_manager.apply_patch` |

**Tally: 7 wired since the audit · 1 deleted as superseded · 3 still unwired · 1 whose referent I could not identify.**

### 2.2 What was done about #7

`docs/audit-2026-08-24.md:112` (item 11) prescribed **three** fixes for the DAG
scheduler. Two had landed; the third had not:

| Prescribed fix | State |
|---|---|
| Skip descendants of failed nodes | ✅ `dag.py::_block_descendants` (`:219-236`) |
| Inject dependency outputs into the prompt | ✅ `metadata["_dep_results"]` (`subagent_orchestrator.py:1323`) |
| **Honor the metadata budget** | ❌ **was missing — now implemented** |

**The defect.** The orchestrator builds a `ResourceBudget` from a node's
`metadata["budget"]`, calls `start()`, and attaches it to
`contract.metadata["_budget"]` — with the comment *"Pass budget to contract
metadata for runner"*. Both runner sites then constructed their **own**
`ResourceBudget()` from contract fields and never looked at the metadata. A
node's declared budget was built, attached, and silently dropped.

It mattered most for **`max_tool_calls`**: `ResourceBudget` has that field, but
the contract has no equivalent, so the DAG declaration was the *only* way to
bound tool calls per node — and it was ignored.

**The fix.** One helper, `_runner._budget_from_contract(contract, deadline)`,
now used by both sites. It keeps the contract-derived limits as the floor and
applies the declared budget **narrow-only**: a graph author can bound a node,
never widen it past the contract's own limits. With no declaration, behaviour is
byte-for-byte what it was.

Pinned by four tests: the cap is applied, a widening declaration is ignored, the
undeclared path is unchanged (and a contract with no `metadata` attribute does
not raise), and all three prescribed fixes are present together.

### 2.1 What was done about #2

`_SENSITIVE_ENV_KEYS` was a static deny-list with **no consumer and no dynamic
access**. Its intended job is done by `wisp/tools/_utils_env.py`, which is
stricter and more nuanced than any static list:

| Helper | Consumer | Mechanism |
|---|---|---|
| `scrub_sensitive_env` | hooks (`infra/hook_types.py:264`) | **allow-list** (`_ALLOWED_ENV_KEYS`) — everything else is stripped |
| `credential_free_env` | bash, sandbox, MCP (`sandbox/__init__.py:275`, `sandbox/router.py:101`, `mcp/manager.py:519`, `benchmark/adapters/terminal_bench.py:128`) | deny-list **plus** `_CREDENTIAL_ENV_PATTERN` |
| `minimal_process_env` | strict MCP | reduced POSIX set |

**The pattern covers every key the static list named.** `_CREDENTIAL_ENV_PATTERN`
matches `apikey|token|secret|passw|credential|privatekey|accesskey|signingkey|ssh|docker|ollama`,
which reaches `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
`AZURE_OPENAI_API_KEY`, `HF_TOKEN`, `GITHUB_TOKEN`, `GITLAB_TOKEN`, and
`OLLAMA_HOST`; the remainder (`WISP_API_KEY`, `AWS_ACCESS_KEY_ID`,
`DOCKER_CONFIG`, `KUBECONFIG`, `SSH_*`) are in `_ALWAYS_STRIPPED_ENV_KEYS`.

**One deliberate difference, and it is the right way round.** The static list
named `HOME` and `USER` as sensitive; the live implementation **keeps** them for
the agent's bash tool (a usable POSIX environment) while excluding them from
hooks via the allow-list. So the dead constant encoded a *stricter* intent that
was consciously relaxed for usability on one path only.

**Deleted, with a note left in its place** explaining why. Keeping it was a
trap: wiring the static list instead of the pattern would be a narrower control
wearing a similar name — the exact failure mode this engagement keeps finding.

Pinned by `test_the_superseded_env_list_is_gone` and
`test_the_live_env_scrubbers_are_wired`.

---

## 3. Three naming artifacts, and one claim I got wrong

Two of the audit's entries look worse than they are, and one is genuinely ambiguous:

- **`_redact_sensitive_tool_args`** does not exist. The function is `redact_sensitive_tool_args` (`infra/security.py:296`), and it *is* wired — the audit's name was the alias a test happens to import it under.
- **`_SENSITIVE_ENV_KEYS`** exists but with no consumer. Its intended partner, `scrub_sensitive_env`, uses its **own** list in `tools/_utils_env.py`. So the question is not "is the constant used" but **"do the two lists agree?"** — which this sweep did not answer.
- **`spawn_with_guards`** is a genuine dead duplicate, but the audit's framing ("unwired guard") implies missing protection. The protection is present; only the function is dead. "Unwired guard" and "dead copy of a live guard" warrant different responses.

### The claim I got wrong

I first recorded **#11 as "in an unreachable module"**, on the strength of a sweep that reported
`semantic_compressor.py` had no importer. **That was false.** `infra/session_dto.py:67` imports
`SemanticCompressor` *inside a function* (a deferred import), and `session_dto` is imported by
`__main__.py:881` and `repl/commands/core.py:126`. The module is reachable and its code can run.

My own pinning test caught the error — which is the argument for writing the guard.

The sweep was wrong for a now-familiar reason: BSD `grep` silently ignores `--include` when it
follows the path, so the search matched nothing and I read that as "no importer". **This is the
third time that exact mistake produced a false "unreferenced" claim** in this engagement.

**#11 stays unverified.** I could not identify what the audit meant by "event-replay `TOOL_CALL`
case". The two plausible candidates are both fine:

- `trace/export.py::replay_plan` — *does* handle `span.kind == "tool_call"`, and is wired to `wisp replay --dry-run` (`__main__.py:503`).
- `semantic_compressor` — reachable, as above.

Recording an ambiguous entry as "not identified" is better than substituting a confident guess; the
first draft of this document did exactly that and was wrong.

---

## 4. Severity of what remains

| # | Item | Live risk | Why |
|---|---|---|---|
| 8 | `security_policy=` never passed | **Low** | The executor performs its own `authorize()` per tool call; the registry-level check is defence-in-depth that never runs. |
| 9 | `ToolRegistry.execute` unused | **Low** | Latent trap, not a live path: it is a parallel implementation *without* truncation or security, so wiring it later would silently drop both. |
| 6 | dead `spawn_with_guards` | **None** | Guards are live elsewhere. Deletion candidate, not a defect. |
| 11 | event-replay `TOOL_CALL` | **Unknown** | Referent not identified; both candidates are wired. No action until it is pinned down. |

**#2 and #7 are closed** (§2.1, §2.2). What remains is one latent trap (#9), one
annotation (#8), one deletion (#6), and one unidentified entry (#11) — **no
remaining item is a live control that fails silently.**

---

## 5. The meta-finding

The prior audit called this "the dominant pattern" and listed 12 instances. **It then went unmaintained:** five were fixed and six were not, and nothing in the repository distinguishes them. The list itself became stale in exactly the way it was warning about.

That is why this document is **pinned** rather than written once:
`tests/test_unwired_controls_inventory.py` fails if any of the six starts being wired (the document would be stale) or if a *new* definition-only symbol appears among the named controls. The same tripwire shape as `test_m4_governance_wiring.py`.

Phase 10's M4 finding is the 13th instance and the only one with runtime governance consequences. **The pattern is not rare in this codebase; it is the default failure mode** — a subsystem is built, tested, and left unconnected, and nothing marks the seam.

---

## 6. Recommended next steps

| Priority | Action |
|---|---|
| 1 | ~~Wire the DAG budget (#7)~~ | ✅ **DONE** (§2.2) |
| 2 | ~~Reconcile the two sensitive-env lists (#2)~~ | ✅ **DONE** (§2.1) |
| 3 | **Delete the dead duplicate** (#6) — `spawn_with_guards` is superseded; keeping it invites the next reader to wire the wrong one |
| 4 | **Annotate #8 and #9** — a comment saying "deliberately not wired; the executor authorises" prevents them being read as defects or, worse, being wired without the missing checks |
| 5 | **Pin down #11** — find what the audit meant by "event-replay `TOOL_CALL` case" before acting on it |

---

## 7. Honest note

This is the **ninth** instance of the engagement's recurring pattern, and the first one found by *auditing the audit*. Its value is not the list — it is the demonstration that the list decayed. A pattern named in a document and never re-verified is indistinguishable from a pattern that was fixed.

It is also the fourth time in this engagement that **my own claim was wrong and a mechanical guard caught it** (after the verification gate, the "dead modules", and the containment re-implementation). Here the error was in this document's first draft: a confident "unreachable module" verdict built on a grep that matched nothing. The test I wrote to pin the inventory failed on it before anyone read the claim.

**Write the guard; it will embarrass the audit in a useful way.**
