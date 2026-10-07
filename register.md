# register.md — every exception the runtime can raise, its phase, and its sites

> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**
> Regenerate with `python3 scripts/derive_register.py` — **stdlib only**, so unlike its
> three siblings it needs no venv and no `env -u PYTHONPATH`.
>
> Every `defined_at`, every `raise_sites` count, every `caught_at` and every `tripwire`
> below is **read from the tree by an AST walk** at generation time, not from prose — a
> disagreement between this page and the tree fails the derivation. The four judgements a
> walk cannot make (`base`, `role`, `phase`, `adr`) are held in the generator as data.
>
> This page states the exception surface's **current** state. It **introduces no
> decision**: it raises nothing, catches nothing, and removes nothing. A claim that cannot
> be pinned is a §Findings entry, not a row.
>
> **Sibling registers:** `CURRENT_AUTHORITIES.md`, `CURRENT_FINDINGS.md`,
> `CURRENT_FLAGS.md`, `CURRENT_OPEN_ITEMS.md`. All five are derived; none may decide.
> This one is named `register.md` rather than `CURRENT_EXCEPTIONS.md` because that is the
> name the mission gave it; the convention it follows is theirs.
>
> **Scope.** `wisp/`, `wisp_net/` and `agent/`, with `tests/` excluded. A `caught_at` of
> `—` therefore means *no catch inside these roots*, which is **not** the same claim as
> *unhandled* — see §(c).
>
> Generated 2026-09-30 at `fabff3b` · **51 classes** · **286 raise sites** · **53 catch sites** · **7 with no test naming them**.

---

## The register

| exception | base | role | phase | defined_at | raise_sites | first_raise | caught_at | adr |
|---|---|---|---|---|---|---|---|---|
| `ExitREPL` | `Exception` | verdict | cli | `wisp/exceptions.py:13` | 1 | `wisp/repl/commands/core.py:175` | `wisp/cli/dispatcher.py:179`, `wisp/cli/dispatcher.py:208`, `wisp/entry.py:700`, `wisp/repl/commands/__init__.py:108` | — |
| `ApprovalCancelled` | `Exception` | verdict | approval | `wisp/exceptions.py:17` | 1 | `wisp/transport/cli.py:947` | `wisp/core/approval_gate.py:117`, `wisp/core/approval_gate.py:146`, `wisp/tool_executor.py:882` | — |
| `ApprovalTimeout` | `Exception` | verdict | approval | `wisp/exceptions.py:33` | 1 | `wisp/transport/cli.py:910` | `wisp/core/approval_gate.py:127`, `wisp/tool_executor.py:895` | — |
| `_TransientOpenError` | `Exception` | recoverable | provider | `wisp/core/provider_stream.py:40` | 1 | `wisp/core/provider_stream.py:174` | `wisp/core/provider_stream.py:278` | — |
| `FirstTokenTimeout` | `asyncio.TimeoutError` | recoverable | subagent | `wisp/multi_agent/_runner.py:168` | 1 | `wisp/multi_agent/_runner.py:662` | `wisp/multi_agent/_runner.py:433`, `wisp/multi_agent/_runner.py:736`, `wisp/multi_agent/_runner.py:903` | — |
| `CircuitOpenError` | `Exception` | recoverable | provider | `wisp/infra/circuit_breaker.py:175` | 2 | `wisp/infra/circuit_breaker.py:93` | `wisp/core/stateless.py:1324` | — |
| `CircuitBreakerOpenError` | `Exception` | recoverable | subagent | `wisp/multi_agent/_circuit_breaker.py:102` | 1 | `wisp/multi_agent/_circuit_breaker.py:44` | — | — |
| `OllamaError` | `Exception` | recoverable | provider | `wisp/ollama_client.py:46` | 10 | `wisp/ollama_client.py:273` | `wisp/ollama_client.py:288`, `wisp/ollama_client.py:427` | — |
| `OllamaConfigurationError` | `OllamaError` | recoverable | provider | `wisp/ollama_client.py:72` | 2 | `wisp/ollama_client.py:347` | — | ADR-0038 |
| `BoundsError` | `RuntimeError` | guard | startup | `wisp/runtime/bounds.py:59` | 11 | `wisp/runtime/bounds.py:95` | `wisp/composition.py:134` | — |
| `CostError` | `RuntimeError` | guard | turn | `wisp/runtime/cost.py:48` | 4 | `wisp/runtime/cost.py:93` | — | — |
| `UnknownModel` | `CostError` | guard | turn | `wisp/runtime/cost.py:54` | 1 | `wisp/runtime/cost.py:159` | `wisp/runtime/cost.py:183` | — |
| `FleetManifestError` | `ValueError` | guard | cli | `wisp/fleet.py:28` | 15 | `wisp/fleet.py:98` | `wisp/cli/doctor_harness.py:288`, `wisp/cli/doctor_harness.py:310`, `wisp/fleet.py:387`, `wisp/fleet.py:426`, `wisp/fleet_ci.py:259` | — |
| `IdempotencyError` | `RuntimeError` | guard | tool | `wisp/runtime/idempotency.py:57` | 2 | `wisp/runtime/idempotency.py:295` | — | — |
| `KeyReuse` | `IdempotencyError` | guard | tool | `wisp/runtime/idempotency.py:63` | 1 | `wisp/runtime/idempotency.py:350` | — | — |
| `UnstableKey` | `IdempotencyError` | guard | tool | `wisp/runtime/idempotency.py:78` | 1 | `wisp/runtime/idempotency.py:376` | — | — |
| `StoreUnavailable` | `IdempotencyError` | guard | tool | `wisp/runtime/idempotency.py:99` | 1 | `wisp/runtime/idempotency.py:341` | — | — |
| `RedactionPointError` | `RuntimeError` | guard | observe | `wisp/runtime/redaction.py:69` | 1 | `wisp/runtime/redaction.py:98` | — | — |
| `GrowthBudgetExceeded` | `RuntimeError` | guard | plan | `wisp/core/task_graph.py:600` | 2 | `wisp/core/task_graph.py:620` | — | — |
| `TrustViolation` | `RuntimeError` | guard | context | `wisp/core/context_trust.py:95` | 1 | `wisp/core/context_trust.py:287` | — | — |
| `ContextOverflow` | `RuntimeError` | guard | context | `wisp/core/context_trust.py:104` | 1 | `wisp/core/context_trust.py:314` | — | — |
| `CriteriaDeclarationRejected` | `Exception` | guard | turn | `wisp/core/convergence.py:673` | 9 | `wisp/core/convergence.py:782` | `wisp/autonomous.py:325` | ADR-0050 |
| `ReplayDivergence` | `RuntimeError` | guard | persist | `wisp/core/replay_digest.py:64` | 2 | `wisp/core/replay_digest.py:125` | `wisp/core/runtime.py:1862` | — |
| `ImportGraphTooLarge` | `RuntimeError` | guard | tool | `wisp/import_graph.py:25` | 1 | `wisp/import_graph.py:133` | `wisp/test_runner.py:305` | — |
| `WalkBudgetExceeded` | `RuntimeError` | guard | tool | `wisp/core/workspace_walk.py:39` | 1 | `wisp/core/workspace_walk.py:130` | `wisp/import_graph.py:132` | — |
| `JobLimitError` | `ToolError` | guard | tool | `wisp/jobs/spawn.py:20` | 2 | `wisp/jobs/spawn.py:41` | — | — |
| `ToolError` | `Exception` | fault | tool | `wisp/tools/errors.py:8` | 87 | `agent/fast_tools.py:47` | `agent/tools/batch_reader.py:386`, `agent/tools/batch_reader.py:629`, `wisp/cli/dispatcher.py:383`, `wisp/jobs/supervisor.py:102`, `wisp/tool_executor.py:279`, `wisp/tool_executor.py:1409`, `wisp/tool_executor.py:1522`, `wisp/tools/bash.py:210`, `wisp/tools/registry.py:1309`, `wisp/tools/registry.py:1451` | — |
| `PlanError` | `Exception` | fault | plan | `wisp/graph/planner.py:72` | 28 | `wisp/graph/planner.py:87` | `wisp/graph/cli.py:300`, `wisp/graph/cli.py:342`, `wisp/graph/planner.py:314`, `wisp/graph/planner.py:396` | — |
| `LSPServerError` | `Exception` | fault | tool | `wisp/lsp/client.py:24` | 16 | `wisp/lsp/client.py:78` | `wisp/lsp/client.py:529`, `wisp/lsp/manager.py:174`, `wisp/lsp/manager.py:188` | — |
| `SearchReplaceError` | `ValueError` | fault | tool | `wisp/core/mutator/search_replace.py:38` | 3 | `wisp/core/mutator/search_replace.py:59` | — | — |
| `JsonExtractionError` | `RuntimeError` | fault | provider | `wisp/structured_output.py:28` | 3 | `wisp/structured_output.py:160` | — | — |
| `DockerUnavailable` | `RuntimeError` | fault | bench | `wisp/benchmark/adapters/docker_backend.py:35` | 4 | `wisp/benchmark/adapters/docker_backend.py:47` | `wisp/benchmark/adapters/docker_backend.py:74`, `wisp/benchmark/adapters/docker_backend.py:111` | — |
| `ExportRefused` | `Exception` | fault | observe | `wisp/trace/otlp.py:24` | 1 | `wisp/trace/otlp.py:50` | — | — |
| `AclError` | `ValueError` | fault | net | `wisp_net/acl.py:30` | 9 | `wisp_net/acl.py:38` | `wisp_net/sim/config.py:97` | — |
| `ApprovalError` | `ValueError` | fault | net | `wisp_net/governance/control.py:37` | 4 | `wisp_net/governance/control.py:64` | — | — |
| `LedgerCorrupt` | `ValueError` | fault | net | `wisp_net/governance/ledger.py:45` | 1 | `wisp_net/governance/ledger.py:61` | — | — |
| `PathError` | `ValueError` | fault | net | `wisp_net/paths.py:13` | 6 | `wisp_net/paths.py:38` | `wisp_net/safety/change.py:70` | — |
| `IntentError` | `ValueError` | fault | net | `wisp_net/reasoning/intents.py:32` | 8 | `wisp_net/reasoning/intents.py:39` | — | — |
| `ChangeError` | `ValueError` | fault | net | `wisp_net/safety/change.py:21` | 12 | `wisp_net/safety/change.py:49` | — | — |
| `SetError` | `ValueError` | fault | net | `wisp_net/sim/config.py:25` | 20 | `wisp_net/sim/config.py:43` | `wisp_net/actuation/engine.py:117`, `wisp_net/safety/whatif.py:101` | — |
| `DeviceUnreachable` | `RuntimeError` | fault | net | `wisp_net/sim/network.py:48` | 1 | `wisp_net/sim/network.py:672` | — | — |
| `SyslogParseError` | `ValueError` | fault | net | `wisp_net/telemetry/syslog.py:14` | 7 | `wisp_net/telemetry/syslog.py:43` | `wisp_net/telemetry/collector.py:141` | — |
| `WispError` | `Exception` | unwired | — | `wisp/core/contracts.py:69` | 0 | — | — | — |
| `TransientTransportError` | `WispError` | unwired | — | `wisp/core/contracts.py:94` | 0 | — | — | — |
| `FatalProviderError` | `WispError` | unwired | — | `wisp/core/contracts.py:102` | 0 | — | — | — |
| `ToolDeniedError` | `WispError` | unwired | — | `wisp/core/contracts.py:110` | 0 | — | — | — |
| `CancelledTurnError` | `WispError` | unwired | — | `wisp/core/contracts.py:118` | 0 | — | — | — |
| `LadderExhausted` | `RuntimeError` | unwired | — | `wisp/core/recovery.py:607` | 0 | — | — | — |
| `EventStreamError` | `Exception` | unwired | — | `wisp/stream_parser.py:17` | 0 | — | `wisp/stream_parser.py:191`, `wisp/stream_parser.py:197` | — |
| `SchemaValidationError` | `RuntimeError` | unwired | — | `wisp/structured_output.py:37` | 0 | — | — | — |
| `SchemaValidationError` | `Exception` | unwired | — | `wisp/multi_agent/schema_validator.py:16` | 0 | — | — | — |

`first_raise` is the **lowest-sorted** site, not the most important one — it is a
deterministic anchor, and the full list is one AST walk away in the generator.

---

## (a) The role vocabulary

Five roles, closed. A row that invents a sixth fails the derivation.

| role | what it means | why it is not the others |
|---|---|---|
| `verdict` | A **decision**, raised by design in a healthy run. `/exit`, a user's cancel, a lapsed prompt. | Not a fault: nothing is wrong. Not `recoverable`: there is nothing to retry — `ApprovalCancelled`'s docstring is explicit that it is *deliberately not* a `CancelledError`, and that genuine SIGINT must still propagate untouched. |
| `recoverable` | An **expected transient** fault the runtime retries or degrades around — a transport reset, a breaker, a provider that accepted the request and streamed nothing. | Not a `fault`: the caller does not have to handle it, the runtime already does. Not a `guard`: it is imposed by the world, not declared by the host. |
| `guard` | A **ceiling or contract the host enforces on itself** — a run bound, a cost ceiling, an idempotency row, a graph-growth budget, a replay digest. | Not a `fault`: it is the host refusing, not the world failing. `ContextOverflow` is the sharpest case: the assembler raises *because* the alternative — a silently shortened prompt — is worse. |
| `fault` | An error the caller must handle. The bulk. | |
| `unwired` | **Declared, and nothing in the tree raises it.** | Not a judgement about value: several are correct designs that were never adopted. The role exists so the page states the fact without deciding what to do about it. |

---

## (b) The firing sequence — one turn, in order

A walk cannot see sequence, so this section is hand-ordered. Its **membership** is not:
the derivation refuses if a row's phase is absent here, or a phase here has no row.

- **`startup`** — `BoundsError` — the run's declared ceilings are read and validated before the first turn. The only exception on this page that fires before anything else can.
- **`context`** — `TrustViolation` and `ContextOverflow` — the assembler's two refusals, in that order: T1 (an untrusted item in instruction position) is checked before the budget (a protected item that does not fit). **Neither is reachable in the tree today** — see §Findings.
- **`turn`** — `CostError` / `UnknownModel` when a model's price is unknown and no policy covers it; `CriteriaDeclarationRejected` when an objective's declaration cannot be used.
- **`plan`** — `GrowthBudgetExceeded` bounds an expansion; `PlanError` covers compilation. Both precede dispatch.
- **`approval`** — `ApprovalTimeout` then `ApprovalCancelled` — a lapse and a verdict are different facts and carry different denial statuses. Order between them is not fixed; they are alternatives, not a sequence.
- **`provider`** — `_TransientOpenError` (an internal sentinel, caught in the same function that raises it), then `CircuitOpenError` once the breaker trips, then `OllamaError` / `OllamaConfigurationError`, then `JsonExtractionError` if the stream's JSON cannot be extracted.
- **`tool`** — `ToolError` dominates — 78 raise sites. `IdempotencyError` and its three subclasses guard a repeat; `SearchReplaceError` and `LSPServerError` are the edit and language-server paths.
- **`subagent`** — `FirstTokenTimeout` (accepted, streamed nothing) then `CircuitBreakerOpenError` — the runner's own breaker, distinct from the provider's.
- **`persist`** — `ReplayDivergence` — a resumed session's journal does not replay consistently.
- **`cli`** — `ExitREPL` — `/exit`. Terminates the REPL rather than the turn.
- **`net`** — The `wisp_net` surface: parse-time (`PathError`, `AclError`, `IntentError`, `SetError`, `SyslogParseError`) before runtime (`DeviceUnreachable`, `LedgerCorrupt`, `ApprovalError`, `ChangeError`).
- **`observe`** — `RedactionPointError` (a redaction at a point the project decided against) and `ExportRefused` (the tier forbids export).
- **`bench`** — `DockerUnavailable` — the benchmark harness's setup path.

**What the order is for.** Two exceptions that can both fire on one turn are not interchangeable if their *order* carries meaning. The clearest instance on this page is the `context` pair: `TrustViolation` is checked before `ContextOverflow`, and the source says the order *is* the point — a priority-0 item that did not fit used to be silently truncated, and the protected-item branch was placed above that truncation so the truncation can no longer reach a protected item.

---

## (c) The raise/catch asymmetry

**286 raise sites, 53 catch sites.** The asymmetry is large and it is mostly not a defect: a library-style module raises and lets its caller decide, and the caller is often outside the three roots this page covers.

What the asymmetry **does** let this page state precisely is the zero:

| | count |
|---|---|
| classes with at least one raise site | 42 |
| classes with **no** raise site | 9 |
| classes with at least one catch site | 23 |
| classes with **no** catch site | 28 |

**A zero-raise class is the load-bearing number.** `caught_at: —` is weak evidence — it
may mean the caller is out of scope. `raise_sites: 0` is strong: an AST walk over the
whole runtime found no `raise` of that name anywhere, so the class cannot fire in this
tree at all.

**7 distinct names — 7 of 51 rows — are named by no test file.** Derived by
searching `tests/` for each name, so it is a floor and not a proof: a test can exercise a
path without ever naming the exception. The list is a place to look, not a verdict.

The name and row counts differ because `SchemaValidationError` is defined twice (§Findings); a name-keyed count would say 8 and a row-keyed count 9, and only the pair is honest.

`CancelledTurnError`, `CircuitBreakerOpenError`, `EventStreamError`, `LSPServerError`, `LadderExhausted`, `ToolDeniedError`, `_TransientOpenError`

---

## §Findings — what this page could not pin

- **The typed error taxonomy is declared and unwired — the largest single finding on this page.** `wisp/core/contracts.py` defines `ErrorKind` (ten members) and `WispError` with four subclasses. Its own module docstring says it *"Replaces: stringly `error_event(code, hint)` + substring matching in `wisp/core/transport.py:is_transient_error` + bare `except BaseException`"* (debt IDs **D2**, **D8**). Measured: **all five classes have zero raise sites and zero catch sites**, and a search for the four subclass names across `wisp/` excluding `contracts.py` returns **no matches at all** — nothing imports them either. The stringly path the docstring says it replaced is still present. This is the Phase-10 unwired-control class: a Phase-1 contract frozen as an interface and never adopted. **The claim is true of the target model and false of the tree**, which is why it is a finding rather than a comment. *A repair is a migration, not an edit — it needs the decision that owns D2/D8.*

- **`LadderExhausted` appears exactly once in the repository — at its own definition.** Not raised, not caught, not imported, not named in any test. Its docstring says *"Raised when a rung is requested and none is available"*, and the exhaustion it describes is real — but `RecoveryLadder` signals it a different way: `decide()` returns `self.escalate(...)` when no candidate rung remains, and `escalate()` returns a `RecoveryDecision`, sets `self.escalated`, and the `ladder_state` property returns `"ESCALATED_TO_HUMAN"`. The module contradicts itself: `escalate`'s own docstring reads *"Terminal honesty: exhaustion produces a STATE, not a hang."* **The state design is the better one** — a returned decision cannot be accidentally swallowed the way a raise can — so this is a superseded class that was never removed, and a docstring that still describes the mechanism it replaced.

- **`TrustViolation` and `ContextOverflow` are unreachable in production, and their docstrings promise a caller that does not exist.** Both raises sit inside `wisp/core/context_trust.py::assemble`. Measured: **`assemble` has no production importer.** The module's only production importer is `wisp/context_assembler.py`, and it takes `TrustTag` and `TRUSTED_TAGS` — the tag vocabulary — not the assembler. Three test files import the module; no runtime module calls `assemble`. `ContextOverflow`'s docstring is unusually explicit about the contract — *"the assembler **raises**, and the caller decides: shrink the context, raise the budget, or fail the turn"* — and the measured state is that the raise is correct and **there is no caller to decide**. The two refusals are well-argued and currently unenforced; whether the assembler should be wired in or the module retired is a decision, and this page takes neither.

- **A crashed approval handler is published as `DENIAL_USER_DENIED`.** In `wisp/core/approval_gate.py` the handler call is wrapped by four handlers: the cancellation quad re-raises untouched, `ApprovalCancelled` and `ApprovalTimeout` each return their own denial — and the generic `except Exception` **logs and falls through** to the block's final `return`, which stamps `denial=DENIAL_USER_DENIED`. So *"the handler raised"* and *"the human said no"* are published under one code. That is the same collapse `tool_executor.py:883-913` was fixed for: that site now emits `DENIAL_NO_APPROVER` and its comment states the rule — *"Nobody could be asked" and "the human said no" are different facts.* ADR-0061 R4 states it for the WebSocket path. **This site was not covered by that repair**, and it is live: `ApprovalGate` is imported by `wisp/core/stateless.py`. The fix is the same shape as the one already applied — a distinct code, not a distinct message. *Note the mis-attribution is the F8 defect class from the other side: a system failure reported as a human verdict.*

- **`SchemaValidationError` is defined twice, and the registry cannot see it.** `wisp/multi_agent/schema_validator.py:16` subclasses `Exception`; `wisp/structured_output.py:37` subclasses `RuntimeError`. Two classes, one name, two bases, no relation. The second has zero raise sites and zero catch sites. **This is also a finding about the instrument**: the first draft of the scanner keyed its registry by class *name* and reported **46** classes where the tree has **47** — a name-keyed scan silently merges the second definition into the first. This page keys rows by *definition site* so the totality check cannot repeat that error, and the derivation now refuses two rows sharing a pin. *A scanner that reports 46 has not shown the tree holds 46.*

- **`EventStreamError` is caught twice and raised never.** Two `except EventStreamError` clauses in `wisp/stream_parser.py::parse_stream` (at `:191` and `:197`), zero raise sites, and no importer outside the module. A handler for a signal the tree cannot produce — either a removed raise or one that was always intended to come from a dependency. Recorded, not resolved: which of the two it is cannot be settled from the tree alone.

- **Two denial statuses are emitted but never classified.** `wisp/core/events.py`'s `_DENIAL_STATUSES` has **seven** members; `OUTCOME_BY_STATUS` has only the **original five** plus `ok` and `error`. `DENIAL_BUDGET_EXCEEDED` and `DENIAL_NO_APPROVER` were added to the first and never to the second, so `classify_status("BUDGET_EXCEEDED")` returns `OutcomeClass.UNKNOWN` and `is_terminal_outcome` returns **`False`** — a verdict that by construction must not be retried reads as retryable — while `is_denial_text` on the same value returns `True`. One status, two answers: the "second classifier" failure `events.py:330-333` warns about in its own comment. Both are emitted live (`tool_executor.py:726-732` and `:900-905`). **The guards cannot catch it because they pin the same five**: `tests/test_outcome_classification_authority.py:53` and `wisp/core/recovery.py:152` each hard-code the original list, so a guard whose subject is a *copy* of the vocabulary cannot see the vocabulary grow. *The repair is two lines and the decision is not — `OUTCOME_BY_STATUS` is a published vocabulary, ADR-0052's blast radius.*

- **The circuit-breaker authority is duplicated.** Two `CircuitBreakerConfig` classes and two `CircuitBreaker` classes exist — `wisp/infra/circuit_breaker.py` and `wisp/multi_agent/_circuit_breaker.py` — with two exception types for one concept, `CircuitOpenError` and `CircuitBreakerOpenError`. The provider path imports the first (`wisp/core/stateless.py:53`); the subagent runner uses the second. Two implementations of one concern is the shape this corpus has a standing name for, and the two exception names make it visible from the outside: **a caller cannot write one handler that covers both.** *Recorded; unifying them is a behaviour change with its own decision.*

- **`ReplayDivergence`'s docstring states an absolute the tree does not keep.** The class docstring says a divergence *"must **escape** the loop, not be caught and reported as one more way a run can end"*. It **is** caught — at `wisp/core/runtime.py:1858`, inside `_recover_unfinished_turn`. The catch is deliberate and its own comment documents it as a repair: the divergence used to be swallowed by a bare `except Exception: pass`, so the log claimed a replay that never happened. **The code is right and the docstring is stale**: what must escape is the *turn loop*, not every handler. The catch does not reconcile silently — it discards the journal and says why. *A docstring absolute that the tree deliberately narrows is a trap for the next reader; the sentence should name the loop.*

- **`adr` is `—` for 45 of 47 rows, and that is a limit, not a claim.** Only two rows carry a decision, and each is cited because the class's **own docstring** names it — `ADR-0038` for `OllamaConfigurationError`, `ADR-0050` for `CriteriaDeclarationRejected`. Most of these exceptions arrived in a phase whose ADR exists but does not name the class. **The provenance was not traced**, and a plausible ADR is worse than a blank — guessing a decision is the defect this corpus exists to prevent. `—` states *not pinned*, not *none exists*.

- **`tripwire` is derived, and a derived tripwire is weaker than a declared one.** The column is the first `tests/` file that names the exception, found by search. It shows what is *referenced*, not what is *asserted*: a test that imports a name and never exercises the raise still counts. It is included because the zero is informative — 7 of 51 classes are named by no test file at all — and it is labelled derived so nobody reads it as a guard. **The number is a floor.** `LSPServerError` has 16 raise sites and no test names it; that is a live path with no assertion on its failure shape, and it is the kind of zero this column exists to surface.

### What this page did not do

- **No exception added, removed, or re-based.** The register's totality is asserted, not
  extended.
- **No finding repaired.** Every §Findings entry below is recorded. Each repair is a
  behaviour change, and several touch a published vocabulary (ADR-0052's blast radius),
  which makes them decisions rather than mechanical edits.
- **No ADR.** Nothing here surfaced a conflict that requires one; each finding names the
  decision that would.
