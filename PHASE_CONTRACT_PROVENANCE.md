# PHASE_CONTRACT_PROVENANCE.md — every contract symbol, its authoriser, and its consumers

> Generated 2026-09-27. Produced by `scripts/contract_provenance.py` (read-only; reports, does not
> decide). **28 symbols across 7 modules.**

---

## §1 — The method, and the two ways it lied first

Provenance needs three things per symbol: **defined in**, **authorised by**, **consumed by**. Only the
first is free.

**Consumers are counted by AST, and the count has two traps — both of which this instrument fell into
before it was right:**

1. **A re-export is not a consumer.** `wisp/contracts/__init__.py` re-exports the whole M1a family, so a
   naive count reported **every** symbol as `WIRED`. The instrument now separates the directory's
   `__init__.py` from everything else.
2. **A same-directory sibling IS a consumer.** Fixing (1) by excluding the whole directory then reported
   `ApprovalDecision` as unconsumed while `wisp/core/approval_gate.py` — same directory — was importing
   it. Only the `__init__.py` is a re-export; siblings count.

**And a third, which made every count zero:** the module name was built from the **absolute** path
(`/Users/…/wisp.contracts.tool`), which matches nothing. It reported `prod=0 test=0` for all 28 symbols
— including `ToolRisk`, which is imported across the tree. **A detector that returns zeros looks exactly
like a clean result**, and the only reason it was caught is that the expected answer for `ToolRisk` was
already known.

`authorised by` is read from each module's own docstring and comments — an ADR id or a spec path. It is
never inferred. `—` means the file does not say, which is a finding.

## §2 — Family 1: the M1a freeze (`wisp/contracts/`)

**10 symbols. 3 consumed. The other 7 are re-exported and nothing else.**

| module | symbol | consumers |
|---|---|---|
| `adapters.py` | `to_flat` | **none** |
| `adapters.py` | `from_flat` | **none** |
| `envelope.py` | `CanonicalEvent` | **none** |
| `manifest.py` | `PluginContract` | **none** |
| `manifest.py` | `MCPServerContract` | **none** |
| `policy.py` | `PolicyDecisionEnvelope` | **none** |
| `run.py` | `RunStatus` | **none** |
| `run.py` | `Transition` | `wisp/runs/store.py` |
| `tool.py` | `ToolRequest` | `wisp/core/proposal.py` |
| `tool.py` | `ToolResult` | `wisp/core/proposal.py` |

**This is a SEAM, not an oversight, and the authorising spec says so in its own words:**

> `docs/superpowers/specs/2026-09-04-enterprise-contracts-m1a-design.md`
> *"Pure addition: no existing producer or consumer changes behavior."*

The spec's goal is to *"freeze five backward-compatible interfaces before any Phase 1–6 refactoring, so
later authority/recovery/evidence work migrates behind stable seams"*. So an unconsumed symbol here is
the design working. **The three that ARE consumed arrived early.**

**No module in this family cites an ADR.** Its authorisation is the spec, and the spec is not in the ADR
log — so a reader who looks only at `WISP_ARCHITECTURE_DECISIONS.md` finds 10 unexplained classes.

## §3 — Family 2: the core vocabulary (`wisp/core/contracts.py`)

**18 symbols. 4 consumed. 14 have no production consumer at all** — test-only.

| symbol | external consumers |
|---|---|
| `risk_for_tool` | **8** |
| `ToolRisk` | **7** |
| `ApprovalDecision` | 1 — `wisp/core/approval_gate.py` |
| `is_cancellation` | 1 — `wisp/core/transport.py` |
| `ErrorKind`, `classify_status`, `WispError`, `TransientTransportError`, `FatalProviderError`, `ToolDeniedError`, `CancelledTurnError`, `TransportConfig`, `RetryPolicy`, `StreamGuardConfig`, `PrunePolicy`, `PruneStats`, `TurnBudget`, `SessionState` | **none** |

**The difference from §2 matters, and it is the finding.**

The M1a family has a spec that *says* it is additive — so "unconsumed" is authorised there. This module
says only *"Phase 1 contracts — pure interfaces for the Wisp remediation program"*, and **cites no ADR
and no spec**. So for its 14 unconsumed symbols there is **no recorded statement** that unconsumed is
intended.

Either they are seams whose authorisation was never written down, or they are the
**written-but-unwired control** this repository names as its dominant pathology
(`docs/audit-2026-08-24.md:270`, ≥12 instances). **This instrument cannot tell you which — and saying so
is the honest output.** What it can tell you is that the question is open for 14 symbols, and closed for
the 7 in §2.

## §4 — What is NOT established here

- **"Consumed" means imported, not used.** A symbol imported and then ignored counts as consumed. The
  AST sees the import edge, not the call.
- **`.workbuddy-ai/memory/` appears as a consumer** of `ToolRisk` and `risk_for_tool` — that is a
  session artefact directory, not production. It inflates those counts by one and should be excluded by
  any follow-up that wants a production-only figure.
- **No ADR mapping for `core/contracts.py`.** The instrument reports what the file says; it did not
  search the ADR log for a decision that might authorise these symbols without being cited at the
  definition. That is the obvious next step and it is not done.
- **Nothing was changed.** This is a report.
