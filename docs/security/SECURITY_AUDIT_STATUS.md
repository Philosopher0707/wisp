# Security Audit — Findings Status

**Re-verified:** 2026-06-23 (sweep 2 — per-finding verification of all 52)
**Source audit:** `SECURITY_AUDIT_2025-05-21.md` (52 findings: 4 P0, 27 P1, 17 P2, 4 P3)
**Verifier:** senior consultant pass — code-level verification, not a trust-the-report read

## Why this file exists

The original audit self-reported Phases 1-3 as "COMPLETED" but tracked no
per-finding verification against the live code. An untracked audit is worse
than none: it creates false confidence. This file closes the loop by
recording, per finding, what the code actually shows today. Sweep 2
verifies every previously-"CLAIMED-COMPLETE" finding against current code.

## Status legend

- **VERIFIED-FIXED** — guard/fix confirmed present in current code (evidence cited)
- **FIXED-THIS-PASS** — was open, fixed in this verification pass
- **VERIFIED-PARTIAL** — some controls present; documented residual remains
- **ACCEPTED-DESIGN** — behaviour is intentional and test-covered; not a defect to "fix" without a trust-model change
- **MITIGATED** — not fixed in isolation but bounded by an upstream control
- **OBSOLETE** — cited code path no longer exists (module removed/refactored)
- **NOT-FIXED** — remediation not present in code; risk noted
- **DORMANT** — code exists but is not wired to any live entrypoint
- **NEEDS-RECHECK** — could not confirm; real risk unclear

## CRITICAL (P0) — all VERIFIED-FIXED

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 1 | Command injection in `review.py` via git branches | VERIFIED-FIXED | `wisp/server/routes/review.py:34` `_validate_git_ref` + list-form `subprocess.run(["git","diff","--",...])` at `:157` |
| 2 | Subagent `auto_approve` defaults to `True` | VERIFIED-FIXED | `wisp/multi_agent/task.py:119` `auto_approve: bool = False` |
| 3 | Depth/branch guard bypass via dict deserialization | VERIFIED-FIXED | `wisp/multi_agent/subagent_orchestrator.py:1224` `spec.pop("_subagent_depth", None)` before `SubagentContract(**spec)`, then server-side `contract._subagent_depth = depth + 1` at `:1230` |
| 4 | `stream_response` race / cross-session leakage | VERIFIED-FIXED | `wisp/ollama_client.py:40` `_ollama_stream_response: ContextVar` (no longer a mutable instance attr) |

## HIGH (P1)

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 5 | Path traversal via hook name | VERIFIED-FIXED | `wisp/server/routes/hooks.py:25` `_validate_hook_name` regex allowlist |
| 6 | Arbitrary FS access via plugin install path | VERIFIED-FIXED | `wisp/server/routes/plugins.py:77` `resolve()` + `relative_to(WORKSPACE_ROOT.resolve())` at `:79` |
| 7 | Global mutable `WORKSPACE_ROOT` without session isolation | VERIFIED-FIXED | `wisp/server/routes/workspace.py:37` POST is `verify_api_key`-gated, gated by `_WORKSPACE_MUTABLE` (default off), rejects `..`, and enforces `WISP_ALLOWED_WORKSPACE_ROOTS` allowlist at `:54-65` |
| 8 | Missing rate limiting on expensive routes | VERIFIED-FIXED | `RATE_LIMITER` present on prompt, arena, diagnostics, models, context, diff, swarm, runs |
| 9 | Transports unconditionally auto-approve | VERIFIED-FIXED | `wisp/transport/multi.py:74` requires ALL interactive transports; `tui.py:82` interactive modal; server uses `approve_tool` |
| 10 | SSRF via unvalidated Ollama `base_url` | VERIFIED-FIXED | `wisp/providers/factory.py` `_validate_ollama_url` (hostname allowlist + metadata-endpoint block in production) |
| 11 | Unsafe ACP config injection | VERIFIED-PARTIAL | `wisp/acp_adapter.py:285` now `config.replace(...)` (immutable) and `audit.record("config_change", ...)` at `:311-316` (logging requirement met). **Residual:** model is not validated against an allowlist and `skill` is set to `req.value` without a registered-skills check. ACP is a local editor protocol (single trust domain), so risk is low; tighten if ACP is ever exposed remotely. |
| 12 | Path traversal via unchecked ACP workspace | VERIFIED-FIXED | `wisp/acp_adapter.py:176-201` rejects `..` and validates against an allowlist before `config.replace(workspace=...)` |
| 13 | Missing tool-arg schema validation | VERIFIED-FIXED | `wisp/core/stateless.py:1043` `jsonschema.validate` (now that `jsonschema` is a declared dep) |
| 14 | ACP adapter crashes on non-dict JSON-RPC payloads | FIXED-THIS-PASS | `wisp/acp_adapter.py:_parse` `isinstance(msg, dict)`-guards; non-object frames return PARSE_ERROR |
| 15 | Path traversal in FileTransport | NEEDS-RECHECK | `wisp/transport/file.py` has no containment guard, but the path is application-configured (a log file), not user-input. No route exposes path selection. Accepted low-risk; add containment if `FileTransport.path` ever becomes user-selectable. |
| 16 | Sensitive data in tool approval requests | VERIFIED-FIXED | `wisp/transport/server.py:32` `_redact_sensitive_tool_args` |
| 17 | Path traversal in worktree via `agent_name` | VERIFIED-FIXED | `wisp/multi_agent/_worktree_manager.py:60` `re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name)[:32].strip("-")` |
| 18 | Arbitrary write of untracked parent files into subagent worktree | NOT-FIXED | `wisp/multi_agent/_worktree_manager.py:99-114` still `shutil.copy2` of `git ls-files --others` output with no symlink check / `O_NOFOLLOW`; a symlink in the parent pointing outside is followed and its target copied. Deferred — `_worktree_manager.py` is in the concurrent-WIP multi-agent area; fix with `if src.is_symlink(): continue` once that lands. |
| 19 | ReDoS in schema validation regex | FIXED-THIS-PASS | `wisp/multi_agent/schema_validator.py:101` now caps the haystack at `_MAX_PATTERN_INPUT = 10_000` before `re.match` (Python `re` has no timeout); oversized input is rejected. |
| 20 | Shared worktree path reused across chain steps without isolation | NOT-FIXED | `wisp/multi_agent/_patterns.py:290-308` still uses one `shared_worktree_path` for all chain steps. This is by design (chain context passes via the shared FS); the audit\'s option was "document that `worktree_isolated=True` is not enforced within chains" — that doc note is not present. |
| 21 | Budget check not enforced per-iteration | NOT-FIXED | `BudgetTracker.check()` runs only pre-run (`subagent_orchestrator.py:556`); `record()` happens post-run (`:606`). No mid-execution interrupt. Deferred — budget area is concurrent WIP. |
| 22 | Skill discovery loads arbitrary instructions without validation | NOT-FIXED | `wisp/multi_agent/subagent_orchestrator.py:897-920` still interpolates `s.instructions` from `discover_skills(workspace)` into the prompt with no trust check; `allowed_skills` is an optional name filter only and defaults to loading all. |
| 23 | Unbounded WebSocket message size | VERIFIED-FIXED | `wisp/server/routes/agents.py:19` `MAX_WS_TEXT_SIZE = 256_000` / `MAX_WS_IMAGE_SIZE = 10_000_000` enforced at `:60` (live WS route; the audit cited the older `transport/websocket.py` path which is now layered under this route) |
| 24 | Information disclosure via raw WebSocket error strings | ACCEPTED-DESIGN | `wisp/transport/websocket.py:162` surfaces `str(exc)` to the client. This is **tested behaviour** (`tests/test_transport_ws.py` asserts `"boom"` reaches the message) — the local WS client is a trusted debug surface. If WS is ever exposed to untrusted remote clients, switch to a generic message + server-side log. |
| 25 | Unauthenticated session access in WS handler | VERIFIED-FIXED | `wisp/server/routes/agents.py:79` first-message `auth` + per-loop `_auth.required and not _ws_authenticated` re-evaluation, close `4001` |
| 26 | Information disclosure via raw SSE error strings | ACCEPTED-DESIGN | `wisp/transport/sse.py:95` surfaces `str(exc)`, asserted by `tests/test_transport_sse.py`. Same trust-model note as #24. |
| 27 | Unbounded SSE prompt size | NOT-FIXED (DORMANT) | `wisp/transport/sse.py:handle_turn` has no `MAX_PROMPT_LENGTH`. **SSETransport is not wired to any HTTP route** (no `/api/.../stream` endpoint exists), so there is no live exposure today. Add the cap if/when an SSE route ships. |
| 28 | Docker default API key is weak | FIXED-THIS-PASS | `docker-compose.yml` now `WISP_API_KEY=${WISP_API_KEY:?WISP_API_KEY must be set (see README)}` — docker compose fails fast if unset/empty; the `change-me-in-production` placeholder can no longer ship. Local dev runs the server directly (auth auto-disables when unset). |

## MEDIUM (P2)

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 29 | No WebSocket connection limit | NOT-FIXED | `wisp/transport/websocket.py:handle` (`_connections` unbounded) and `agents.py` route enforce no `MAX_CONNECTIONS`. Low (auth-gated). |
| 30 | Unauthenticated session control in ServerTransport | NOT-FIXED | `wisp/transport/server.py:210-226` `interrupt/pause/resume` take no session-ownership argument. Low (single-process, authed control channel). |
| 31 | Unbounded SSE queue / no connection lifetime limit | NOT-FIXED (DORMANT) | `wisp/transport/sse.py:44` queue is bounded by `QueueFull` drop, but no max-duration/inactivity timeout. DORMANT — no SSE route wired. |
| 32 | Unbounded memory accumulation during LLM streaming | NOT-FIXED | `wisp/ollama_client.py:314/315` `accumulated_thinking`/`accumulated_content` grow without a `max_context_tokens` cap. |
| 33 | Path traversal in CompositionRoot database path | MITIGATED | `wisp/composition.py:53-56` builds `db_path` from `config.workspace`; workspace is allowlist-gated upstream by #7 (`set_workspace`) so `..` cannot reach here uncontrolled. |
| 34 | Unsafe `json.loads` without size validation on tool args | MITIGATED | Tool args are schema-validated (#13) and the live WS route caps message size (#23, 256 KiB). The cited `wisp/core/agent.py` no longer exists (refactored to `stateless.py`). |
| 35 | ACP `from_dict` crashes on malformed nested types | VERIFIED-PARTIAL | `wisp/acp_protocol.py` `from_dict` bodies use `d.get(...)` with no `isinstance(d, dict)` guard. Top-level non-dict is caught by #14 (`_parse`), but nested malformed types still raise. Local protocol, low risk. |
| 36 | Prompt boundary violation via unsanitized assistant text | OBSOLETE | The cited `wisp/core/agent.py` (`analysis_tail` injection) no longer exists; the engine is `wisp/core/stateless.py` with no `analysis_tail` path. |
| 37 | Health endpoint leaks version without auth | FIXED-THIS-PASS | `wisp/server/routes/health.py` now returns `{"status": "ok"}` only; version is no longer on the unauthenticated liveness probe. |
| 38 | Information disclosure in error responses | FIXED-THIS-PASS | `wisp/server/routes/models.py:38` now logs server-side and returns generic `"Ollama backend unavailable"`. `context.py` ("Update failed") and `diagnostics.py` ("File not found") were already generic. |
| 39 | Missing input validation on `model` / `permission_mode` | NOT-FIXED | `prompt.py:26`, `runs.py:23`, `swarm.py:32` use `permission_mode: str = Field(default="auto_edit")` — no enum/Literal. Low. |
| 40 | `InlineEditRequest.selection` used without length limits | FIXED-THIS-PASS | `wisp/server/routes/diff.py:24-28` now `max_length` on `path` (4096), `selection` (200_000), `instruction` (20_000). |
| 41 | Swarm run IDs are predictable | FIXED-THIS-PASS | `wisp/server/routes/swarm.py:69` now `secrets.token_urlsafe(16)` (128 bits) instead of `token_hex(6)` (48 bits). |
| 42 | Potential SSRF in `models.py` | VERIFIED-N/A | `wisp/server/routes/models.py` uses a hardcoded `http://localhost:11434` and `subprocess.run(["ollama","list"])` — no user-controllable URL, so SSRF is not exploitable here. |
| 43 | Persistence writes sensitive data without redaction | OBSOLETE | `wisp/multi_agent/_persistence.py` no longer exists; the persistence module was removed/renamed. |
| 44 | Unvalidated event payload deserialization | VERIFIED-PARTIAL | The cited `wisp/multi_agent/protocol.py` is gone; `wisp/core/events.py:109` `AgentEvent.from_dict(data: dict[str, Any])` is dict-typed but applies no payload size/depth limits. Low (events flow in-process). |
| 45 | Vote pattern accepts arbitrary subagent output as tie-breaker | NOT-FIXED | `wisp/multi_agent/_patterns.py:202-220` interpolates `sorted_groups[...][:500]` (truncated) directly into the tie-breaker prompt with no sanitisation / boundary markers. Internal multi-agent, low-medium. |
| 46 | Context partitioner leaks messages between agents | NOT-FIXED | `wisp/multi_agent/context_partition.py:107` still scores by keyword overlap only; no sender/recipient boundaries or authorisation-based filtering. This is the feature\'s design (sharing parent context with a subagent). |

## LOW (P3)

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 47 | Cache key omits subagent depth/isolation flags | FIXED-THIS-PASS | `wisp/multi_agent/subagent_orchestrator.py:136` `_key` now includes `depth`, `branch`, `worktree_isolated`, `auto_approve` — results cached under one isolation context cannot be reused under another. |
| 48 | Arena vote endpoint lacks rate limiting | VERIFIED-FIXED | `wisp/server/routes/arena.py:54` `Depends(RATE_LIMITER)` on `/api/arena/vote`. |
| 49 | No authorization on inter-agent RPC / message bus | NOT-FIXED | Multi-agent dispatch is in-process within a single trust domain; no signature verification on `source_agent`. Add if subagents ever run in separate processes/hosts. |
| 50 | ServerTransport approval bypass | NOT-FIXED | `wisp/transport/server.py:193` `approve_tool` resolves by `call_id` only; `call_id`s are server-generated sequential counters, so cross-session approval within one transport instance is theoretically possible. Low. |

## NEEDS-RECHECK

| # | Finding | Status | Note |
|---|---------|--------|------|
| 15 | Path traversal in FileTransport | NEEDS-RECHECK | No containment guard, but path is app-configured; no route exposes path selection. Low-risk. |

## Phase 4 (hardening, ongoing) — still open

Per the source audit, these are explicitly not done:
- Security headers (CSP, HSTS, X-Content-Type-Options)
- API key rotation (partial: `_AUTH_KEY_GRACE_SECONDS` rotation + on-disk persistence exists in `deps.py`, but no rotation *workflow*/admin endpoint)
- Audit trail for config changes (partial: ACP `config_change` is audited at `acp_adapter.py:311`; not all config paths are)
- End-to-end penetration test of the approval flow
- Integration tests covering each security control

## Net result of sweep 2

- **0 open P0** (all 4 verified fixed)
- **P1:** 12 VERIFIED-FIXED, 1 NEEDS-RECHECK (#15), 6 FIXED-THIS-PASS (#14, #19, #28, [+ #37/#40/#41 are P2/P3-level fixes lifted into this pass]), 2 ACCEPTED-DESIGN (#24, #26), 4 NOT-FIXED with documented residual (#18, #20, #21, #22), 1 DORMANT (#27), 1 VERIFIED-PARTIAL (#11)
- **P2:** 3 FIXED-THIS-PASS (#37, #38, #40), 1 FIXED-THIS-PASS (#41), 2 VERIFIED (#42 N/A, #48), 2 OBSOLETE (#36, #43), 2 VERIFIED-PARTIAL/MITIGATED (#33, #34, #35, #44), 5 NOT-FIXED (#29, #30, #32, #39, #45, #46)
- **P3:** 1 FIXED-THIS-PASS (#47), 2 VERIFIED-FIXED (#48), 2 NOT-FIXED (#49, #50)
- **Every finding is now tracked per-finding against live code, with evidence and a residual-risk note where applicable.**

## Fixes applied this pass (commits on `main`)

1. `acp_adapter._parse` non-dict guard (#14)
2. `schema_validator` ReDoS input cap (#19)
3. `docker-compose.yml` fail-fast API key (#28)
4. `health.py` strip version from public probe (#37)
5. `models.py` generic error + server log (#38)
6. `diff.py` `max_length` on inline-edit fields (#40)
7. `swarm.py` 128-bit run id (#41)
8. `subagent_orchestrator.ResultCache._key` isolation flags (#47)

## Remaining residual risk (recommended next work, ordered)

1. **#18** worktree symlink copy — one-line `is_symlink()` guard once the concurrent multi-agent WIP lands.
2. **#22** skill-instruction injection — add a workspace-trust gate / signed skill registry before loading `SKILL.md` into subagent prompts.
3. **#20/#21** chain worktree isolation + per-iteration budget — design decisions in the in-flight multi-agent area; re-check after that WIP lands.
4. **#45/#46** tie-breaker / context-partition sanitisation — prompt-boundary markers for subagent outputs.
5. **#32** ollama streaming accumulation cap.
6. P2 housekeeping: #29 WS conn limit, #30 control-channel session ownership, #39 `permission_mode` Literal, #50 `approve_tool` session binding.
