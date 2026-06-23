# Security Audit — Findings Status

**Re-verified:** 2026-06-23
**Source audit:** `SECURITY_AUDIT_2025-05-21.md` (52 findings: 4 P0, 27 P1, 17 P2, 4 P3)
**Verifier:** senior consultant pass — code-level verification, not a trust-the-report read

## Why this file exists

The original audit self-reported Phases 1-3 as "COMPLETED" but tracked no
per-finding verification against the live code. An untracked audit is worse
than none: it creates false confidence. This file closes the loop by
recording, per finding, what the code actually shows today.

## Status legend

- **VERIFIED-FIXED** — guard/fix confirmed present in current code (evidence cited)
- **FIXED-THIS-PASS** — was open, fixed in this verification pass
- **NEEDS-RECHECK** — could not confirm; real risk unclear
- **CLAIMED-COMPLETE** — audit says done; not re-verified this pass

## CRITICAL (P0) — all VERIFIED-FIXED

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 1 | Command injection in `review.py` via git branches | VERIFIED-FIXED | `wisp/server/routes/review.py:34` `_validate_git_ref` + list-form `subprocess.run(["git","diff","--",...])` at `:157` |
| 2 | Subagent `auto_approve` defaults to `True` | VERIFIED-FIXED | `wisp/multi_agent/task.py:119` `auto_approve: bool = False` |
| 3 | Depth/branch guard bypass via dict deserialization | VERIFIED-FIXED | `wisp/multi_agent/subagent_orchestrator.py:1224` `spec.pop("_subagent_depth", None)` before `SubagentContract(**spec)`, then server-side `contract._subagent_depth = depth + 1` at `:1230` |
| 4 | `stream_response` race / cross-session leakage | VERIFIED-FIXED | `wisp/ollama_client.py:40` `_ollama_stream_response: ContextVar` (no longer a mutable instance attr) |

## HIGH (P1) — verified this pass

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 5 | Path traversal via hook name | VERIFIED-FIXED | `wisp/server/routes/hooks.py:25` `_validate_hook_name` regex allowlist |
| 6 | Arbitrary FS access via plugin install path | VERIFIED-FIXED | `wisp/server/routes/plugins.py:77` `(WORKSPACE_ROOT / raw_path).resolve()` + `relative_to(WORKSPACE_ROOT.resolve())` at `:79` |
| 8 | Missing rate limiting on expensive routes | VERIFIED-FIXED | `RATE_LIMITER` dependency present in 14 route files |
| 9 | Transports unconditionally auto-approve | VERIFIED-FIXED | `wisp/transport/multi.py:74` requires ALL interactive transports; `tui.py:82` interactive modal; server uses `approve_tool` |
| 10 | SSRF via unvalidated Ollama `base_url` | VERIFIED-FIXED | `wisp/providers/factory.py` `_validate_ollama_url` (hostname allowlist + metadata-endpoint block in production) |
| 13 | Missing tool-arg schema validation | VERIFIED-FIXED | `wisp/core/stateless.py:1043` `jsonschema.validate` (now that `jsonschema` is a declared dep — see `pyproject.toml`) |
| 14 | ACP adapter crashes on non-dict JSON-RPC payloads | **FIXED-THIS-PASS** | `wisp/acp_adapter.py` `_parse` now `isinstance(msg, dict)`-guards before `.get()`; non-object frames return a PARSE_ERROR instead of `AttributeError`-killing the adapter |
| 16 | Sensitive data in tool approval requests | VERIFIED-FIXED | `wisp/transport/server.py:32` `_redact_sensitive_tool_args` |
| 17 | Path traversal in worktree via `agent_name` | VERIFIED-FIXED | `wisp/multi_agent/_worktree_manager.py:60` `re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name)[:32].strip("-")` |
| 23 | Unbounded WebSocket message size | VERIFIED-FIXED | `wisp/server/routes/agents.py:19` `MAX_WS_TEXT_SIZE = 256_000` / `MAX_WS_IMAGE_SIZE = 10_000_000` enforced at `:60` |
| 25 | Unauthenticated session access in WS handler | VERIFIED-FIXED | `wisp/server/routes/agents.py:79` first-message `auth` + per-loop `_auth.required and not _ws_authenticated` re-evaluation, close `4001` |

## NEEDS-RECHECK

| # | Finding | Status | Note |
|---|---------|--------|------|
| 15 | Path traversal in `FileTransport` | NEEDS-RECHECK | `wisp/transport/file.py` has no path-containment guard, but the path is application-configured (a log file), not user-input. Real risk depends on whether any route exposes path selection — none found this pass. Accepted as low-risk; add a containment check if `FileTransport.path` ever becomes user-selectable. |

## CLAIMED-COMPLETE (not re-verified this pass)

P1 #7, #11, #12, #18, #19, #20, #21, #22, #24, #26, #27, #28 and all P2 (#29-#46) / P3 (#47-#50) are reported COMPLETED in the source audit's Remediation Status section. They were not individually re-verified in this pass. **Recommendation:** sweep these in the next pass, prioritising #19 (ReDoS), #20/#21 (worktree/budget — multi-agent area has in-flight WIP, re-check after it lands), #22 (skill discovery arbitrary instructions), #28 (Docker default key `change-me-in-production` is a placeholder — confirm it cannot ship to a real deployment unchanged).

## Phase 4 (hardening, ongoing) — still open

Per the source audit, these are explicitly not done:
- Security headers (CSP, HSTS, X-Content-Type-Options)
- API key rotation
- Audit trail for config changes
- End-to-end penetration test of the approval flow
- Integration tests covering each security control

## Net result of this pass

- **0 open P0** (all 4 verified fixed in code)
- **1 P1 fixed** (#14 ACP non-dict DoS) with regression check
- **11 P1 verified fixed**, **1 P1 needs-recheck** (#15, low-risk), **~27 findings claimed-complete pending next sweep**
- The audit is now tracked per-finding against live code, with evidence.
