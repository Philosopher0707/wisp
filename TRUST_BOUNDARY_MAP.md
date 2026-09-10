# Trust Boundary Map — Wisp Graph Engine

Every path from untrusted input to side effect, with the control that must hold.

## Data-flow spine

```text
graph input (CLI/SDK/YAML/file)
  ↓ [B1: DSL parse — safe_load, strict coercion, size caps]
validator (fail closed; narrow-only vs active policy)
  ↓ [B2: static authority check — advisory only, NOT enforcement]
GraphExecutor (scheduling, joins, routes, retries, budgets)
  ↓ [B3: runtime authority — inherited from runner path, never asserted]
NodeRunner → SubagentOrchestrator → agent loop → ToolExecutor → authorize()
  ↓ [B4: THE enforcement boundary — capabilities/workspace/risk/approval]
filesystem / process / network
```

Side-effect sinks reachable from graph execution:

| Sink | Path | Governing control |
|---|---|---|
| Agent tool calls | runner → orchestrator → ToolExecutor → authorize() | L0–L5 auth layers, approval, sandbox |
| Artifact files | `ArtifactStore.put` | MUST: type allowlist + containment + redaction + size cap (FIXED) |
| SQLite rows | `GraphStore` (parameterized) | DB-file location; event/checkpoint volume caps (FIXED) |
| Local functions | `function:` registry string → callable | MUST: host-owned registry only; SDK/CLI register safe set (DOCUMENTED + CLI allowlist) |
| Trace/CLI output | renderers → stdout | MUST: ANSI/DOT escaping + redaction (FIXED) |

## What the graph engine NEVER does (verified by grep)

No `subprocess`, `os.system`, `open()`-for-mutation outside artifacts,
`sockets`, `requests/httpx`, or direct `ToolExecutor` calls in `wisp/graph/`.
All model-adjacent side effects route through the injected runner; the stock
runner routes through `SubagentOrchestrator`. Direct-FS surface is limited to
`artifacts.py` (contained post-fix) and `store.py` (DB file).

## Trust classification

TRUSTED: developer/user intent, configured Wisp policy, OS boundary, crypto.
UNTRUSTED: graph YAML, node config, model/provider output, artifacts,
repo files, route labels, verdicts, persisted rows, env-provided paths,
timing/interleavings.

Key principle: **validator output is an opinion; `authorize()` is the law.**
The graph layer asserts nothing about tools — it only narrows requests.
Enforcement happens where it always did: ToolExecutor + subagent
`allowed_tools` filtering (`stateless.py` batch/single-path rejects).

## Threat matrix (condensed; full per-threat cards in red-team report)

| # | Attacker → target | Control | Status |
|---|---|---|---|
| T1 | Malicious YAML → parser | safe_load + strict types + caps | FIXED |
| T2 | Graph author → authority (`allowed_tools: [run_bash]`) | narrow-only validator + ToolExecutor enforcement | PASS (defense in depth) |
| T3 | Graph author → `idempotent: "false"` / `bool()` coercion | strict bool parser | FIXED (was privilege escalation) |
| T4 | Graph author → workspace escape via policy | abspath+realpath gate, empty=inherit-executor-ws | FIXED |
| T5 | Model → route label → privileged lane | code-owned table; unknown→default; default must exist | PASS + validator pins targets |
| T6 | Model → `ALLOW`/forged evidence → gate | fail-closed normalize; gates need measured evidence | PARTIAL (provenance is host duty; reference gate hardened) |
| T7 | Generator → verifier poisoning | separate contracts; verdict schema; evidence URIs | PASS (structural) |
| T8 | Artifact type → path traversal write | allowlist + realpath containment | FIXED (was P0, PoC confirmed) |
| T9 | Artifact ref → cross-file/run read | per-run scoping + hash verify on consume | FIXED |
| T10 | Secrets → durable artifacts/events/errors | `auth.secrets.redact` at persist boundaries | FIXED |
| T11 | Resume with escalated definition | full-definition fingerprint pin | FIXED (was P0, PoC confirmed) |
| T12 | State tamper (status/budget/approval) | transition validation on resume; approvals never from inputs | FIXED |
| T13 | Stale worker overwrite | per-attempt node rows; attempt-generation check on settle | FIXED |
| T14 | Cancel/resume races | terminal-state precedence; cancelled never succeeds | FIXED + tests |
| T15 | Fan-out DoS (1M branches) | absolute node/edge caps at validation | FIXED |
| T16 | Artifact/event bombs | size/count caps; join_wait throttle; read limits | FIXED |
| T17 | Provider/model confusion via DSL/retry | allowlist validation; fallbacks resolve via factory policy | PASS (validator) |
| T18 | `function:` → arbitrary callable | registry is host code; CLI registers fixed set | DOCUMENTED (host duty) |
| T19 | Prompt injection in repo → agent | cannot grant authority; ToolExecutor still gates | PASS (architectural) |
| T20 | Server/multi-tenant graph APIs | NOT IMPLEMENTED — no server route exposes graphs | GAP (explicit) |
