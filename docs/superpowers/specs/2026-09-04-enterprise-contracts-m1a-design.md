# M1a Design: Enterprise Contract Freeze (additive, no migration) — rev 3 (approved)

Date: 2026-09-04. Scope: Phase 0 deliverables 3–4 (versioned contracts +
compat suite). Approach A (additive freeze). Stack: frozen dataclasses + JSON
fixtures. Canonical shape: nested, adapters at edge.

## 0. Ground truth this spec builds on

- `AgentEvent` (frozen dataclass, `schema_version`, `trace_id`/`span_id`) in
  `wisp/core/events.py:55-73`; `to_dict` omits empty trace context (`:104-107`).
- The core emits **flat** dicts today: `_flatten_event` in
  `wisp/core/stateless.py:105` (used at every yield), a second copy in
  `wisp/core/provider_stream.py`, plus inline flattening in
  `wisp/transport/headless.py`. All three stay untouched; canonical nested is
  the target shape, adapters bridge both directions.
- Existing decision types: `ApprovalDecision` + `ToolRisk`/`TOOL_RISK_TABLE` +
  `SessionState` + error taxonomy in `wisp/core/contracts.py`; a *separate*
  `PolicyDecision` (`allowed/reason/modified_args/rule_name`) in
  `wisp/infra/policy_engine.py:31`. The new package adds **no third
  authority** — it defines wire envelopes with constructors from both.
- Existing manifests: `PluginManifest`
  (`name/version/description/author/license/namespace`,
  `wisp/plugins/manifest.py`) and `MCPServerConfig`
  (`name/command/args/url/env/disabled/transport/always_load/auth/
  auth_config/timeout_seconds/headers/disabled_tools`,
  `wisp/mcp/manager.py:73-86`). Contract schemas mirror these fields exactly;
  enterprise extensions (`origin`, `scopes`, `signature`) are reserved
  optional fields, unpopulated until Phase 3 defines the bundle format.
- Produced run vocabulary: background statuses are exactly
  `running/completed/failed/cancelled` (`wisp/multi_agent/background.py`);
  orchestrator `EventKind` wire values are exactly `planning`, `task_started`,
  `task_progress`, `task_completed`, `task_failed`, `task_retry`, `done`
  (`wisp/multi_agent/task.py:243-251`). No 8-state machine exists.

## 1. Goal

Freeze five backward-compatible interfaces before any Phase 1–6 refactoring,
so later authority/recovery/evidence work migrates *behind* stable seams.
Pure addition: no existing producer or consumer changes behavior.

## 2. Package layout (`wisp/contracts/`)

| Module | Contents |
|---|---|
| `__init__.py` | Re-exports, `CONTRACT_VERSION = 1` |
| `envelope.py` | Canonical event envelope (nested-only payload) |
| `tool.py` | `ToolRequest` / `ToolResult` envelopes |
| `policy.py` | `PolicyDecisionEnvelope` (wire form, see §3) |
| `run.py` | `RunStatus` (4 produced states), orchestrator `EventKind`, `Transition` |
| `manifest.py` | Plugin + MCP manifest schemas (mirrored fields + reserved extensions) |
| `adapters.py` | `to_flat` / `from_flat`, `for_cli/for_tui/for_websocket/for_headless` |

Small focused modules sharing one package, following the repo's one-class-
per-file spirit for transports where it applies (`tool.py` pairs request
with its matching result, as the executor already does).

## 3. Envelope rules

- Every envelope: frozen dataclass with a literal field named `version`
  defaulting to `1` — except the canonical event, whose field is literally
  named `schema_version` to match `AgentEvent.to_dict()` output exactly.
  Adapters map both 1:1 (no rename on the wire).
- Strictness is layered: envelope constructors reject unknown fields (fail
  fast on drift); `from_flat` is lenient and folds unknown keys into `data`,
  matching `AgentEvent.from_dict` behavior — canonical-*out* is always strict.
- Round-trip guarantee is bounded: lossless over the canonical **nested**
  form, with named exclusions (empty trace-context omission per
  `events.py:104-107`). Verified with hand-rolled round-trip loops (no new
  test dependencies).
- Trace lineage: adapters preserve `trace_id`/`span_id`/`schema_version`
  where present and document that today's flat path drops them — the goldens
  pin current behavior, the contract states the target.
- `PolicyDecisionEnvelope` is a wire form only, with
  `from_gate_decision()` (`core/contracts.ApprovalDecision`) and
  `from_engine_decision()` (`infra/policy_engine.PolicyDecision`)
  constructors. It serves the gate/executor boundary. Canonical cancel
  representation: denied decision with the normalized reason code
  `cancelled_by_user` — never an exception on the wire. (This normalizes the
  existing free-text strings `cancelled by user at approval (...)` in
  `approval_gate.py:100,111` and `[Cancelled by user at approval for ...]` in
  `tool_executor.py:513`; the normalization is pinned by goldens.) The three
  existing approval signatures are documented as-is — `ApprovalGate.check`
  returning a tuple (`approval_gate.py:120-126`),
  `ApprovalGate.check_decision` returning `ApprovalDecision` (`:54-60`), and
  `approval_handler(func_name, func_args, reason)` (`tool_executor.py:502`);
  unifying them is M2 work.
- `principal_id` / `correlation_id`: optional reserved fields, supplier TBD
  in Phase 1 (`Principal` model). Unpopulated until then — no identity
  plumbing is invented here.
- No span taxonomy: only `trace_id`/`span_id` passthrough. Span kinds are M1b.
- `ToolRequest`/`ToolResult` cover the executor's real outcome vocabulary:
  `auto_approved` flag, `modified_args` (hook-modified), block reason codes
  (`repeat_guard`, `pre_tool`, `plan`, `danger`, `permission`), and the
  `{status, data, metadata}` wrapper shape. Anything else found during
  implementation gets an explicit exclusion note, not silent omission.
- `run.py` freezes the **produced** vocabulary (`RunStatus` 4 states +
  orchestrator `EventKind` + append-only `Transition` records). The 8-state
  lifecycle (`queued → planning → … → succeeded | failed | cancelled`) is
  Phase-2 design and moves to M1b.

## 4. Adapters

`to_flat(event)` / `from_flat(d)` with four named aliases sharing one
implementation today — the seam matters, not divergence. All three flatten
sites (§0) stay untouched; deprecation is M2 business.

## 5. Compat suite

- `tests/test_contracts_{envelope,tool,policy,run,manifest,adapters}.py`
- `tests/fixtures/contracts/*.json` goldens asserting: version field
  present, required fields present, unknown-field rejection (constructors),
  lenient `from_flat` folding, nested round-trip with named exclusions,
  run-transition legality over produced states.
- Per-transport goldens scoped to the `AgentEvent` family, plus a flat-output
  byte-stability test (headless + `Transport` ABC consumers read flat shapes).
- Out of scope (explicit): subagent/background lifecycle envelopes
  (`OrchestratorEvent`, `agent_started/settled/progress`) — deferred to M1b
  with rationale (second event family, needs its own freeze).
- This suite is the M2+ migration gate: refactors that break it fail loudly.

## 6. Acceptance mapping (Phase 0 criteria)

- Same canonical events on all paths: adapters + per-transport goldens
  (AgentEvent family; lifecycle family in M1b).
- Migration without data loss: additive-only, nothing to migrate; the
  byte-stability test pins what adapters must keep accepting.
- Version + fixtures per contract: §3 + §5.

## 7. Non-goals

No producer migration, no `Principal`, no `RunStore`, no 8-state lifecycle,
no policy bundle, no telemetry changes. M1b (target-arch doc + 5 ADRs,
including lifecycle + lifecycle-envelope freeze) is a separate spec cycle.
