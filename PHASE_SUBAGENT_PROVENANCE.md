# PHASE_SUBAGENT_PROVENANCE.md — the subagent subsystem: how it is wired, and what is not

> Generated 2026-09-27. Reported from a live session that crashed three times in a row:
> `AttributeError: 'SubagentRunner' object has no attribute '_parent_config'`.

---

## §1 — The crash: one character, on the one path nothing tested

`_runner.py` sets the attribute as `self.parent_config` at **:140** and reads it that way at
**:814, :815, :818, :836, :837**. **Line :808 was the only underscore:**

```python
profile=str(getattr(self._parent_config, "profile", None)   # AttributeError
```

`:808` lives in `_child_principal`, reached only via `_run_via_runtime` (`:669`). So **every subagent
run through the runtime raised**, was retried three times by the orchestrator, and failed — which is
exactly what the session showed.

**Fixed.** Verification is **AST-level**, not behavioural: the underscore now appears in no executable
position, and the attribute has one name everywhere else. I could not find a test that reaches `:808` —
tests *name* `_child_principal` and `_run_via_runtime`, but nothing failed on the typo. **That is a
weaker verification than this session's standard, and it is stated rather than glossed.**

## §2 — The inbound wiring is complete

Every subagent module is reached from outside the package. **Nothing here is unwired:**

| module | reached from |
|---|---|
| `subagent_orchestrator` | `composition.py` (the composition root) |
| `background` | `composition.py`, `tool_executor.py` |
| `roles` | `tool_executor.py` |
| `task` | `tool_executor.py`, `tools/orchestration.py`, `graph/runner.py`, `repl/commands/agents.py`, `server/routes/swarm.py` |
| `dag` | `tools/orchestration.py` |
| `schema_validator` | `graph/planner.py` |
| `_worktree_manager` | `arena.py`, `task/review.py` |
| `cli` | `__main__.py` |
| `capability_matcher`, `context_partition`, `shared_context`, `telemetry`, `_patterns` | intra-package |

**6,960 lines across 13 modules, and the entry points are all live.** The subsystem is not the
written-but-unwired shape the rest of this repository keeps producing.

## §3 — THE FINDING: `SwarmOrchestrator` has never existed, and only one of its two callers says so

```
wisp/server/routes/swarm.py:65   from wisp.multi_agent.orchestrator import SwarmOrchestrator
wisp/multi_agent/cli.py:17       # SwarmOrchestrator is not yet implemented — use
                                 #   SubagentOrchestrator as fallback
```

`wisp/multi_agent/orchestrator.py` **does not exist.** Measured: importing
`wisp.multi_agent.orchestrator` raises `ModuleNotFoundError`, and it is the **only** subagent module
that cannot be imported.

**Two callers, two different responses to the same absence:**

- **`cli.py` has the fallback**, with a comment saying why. The CLI works.
- **`swarm.py` had none** — it returned `503 {"error": "Swarm subsystem unavailable"}` **forever**.
  That message reads like a *configuration* problem; it is a *missing implementation*. The REST swarm
  endpoint could never work, and nothing said so.

**Fixed to name the real cause** — `501 Not Implemented`, with the missing module named and the two
working alternatives (`POST /api/subagents`, the `wisp agents` CLI).

**No fallback was wired into the route, deliberately.** `cli.py:74` records that *"SubagentOrchestrator
has a different API than SwarmOrchestrator"*, and the route calls
`SwarmOrchestrator(config, max_parallel=...)`. Substituting one for the other needs an adapter, and
inventing that here would be a design decision wearing a fix's clothes.

## §4 — A lapse of mine that this file caught

`tests/test_child_principal_wired.py::test_the_mode_gate_denies_before_the_principal_consult` was
**already failing** when I started this turn — verified by stashing my change and re-running (identical
`1 failed / 21 passed`).

**It was broken by my own earlier `run_bash` deny removal**, and my verification set that turn did not
include this file. The assertion named the cause precisely:

> *"the principal layer now decides run_bash in auto_edit; the policy gate no longer runs first, which
> changes the ordering P2 recorded"*

The test's **property** was unchanged — the mode gate denies *before* `authorize()` runs, so the denial
names no controlling layer, and the principal layer catches what the mode permits but the contract
excludes. Only its **witness** moved. Re-witnessed with `git_push`, still hard-denied in `auto_edit`,
with the move and its reason in the docstring.

**The lesson is about verification breadth, not about the code:** I ran six suites and missed the
seventh, and the one I missed was the one asserting the ordering I had just changed.

## §5 — What is not established

- **The `:808` fix is not behaviourally verified** (§1). A test that drives `_run_via_runtime` to
  `_child_principal` with a real config would close this; none exists.
- **`SwarmOrchestrator`'s intended design is unknown.** The comment says "not yet implemented", not
  what it should have been. Whether it is owed is a decision.
- **No provenance for the subsystem as a whole** — no ADR or spec authorises `multi_agent/`, unlike the
  M1a freeze. Its 13 modules cite no decision that I found.
