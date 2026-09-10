# Graph Security Red-Team Report

## Executive Summary

**Security status: PASS (with explicit residual risks below — no P0/P1 open).**

Adversarial audit of `wisp/graph/` found **4 P0, 9 P1, and 6 P2** issues.
All P0/P1 are fixed with regression tests; P2 fixed where cheap. Attack
surface verified by PoC before each fix. 363 new security tests + 53
functional tests + 182 surrounding regression tests pass; ruff clean;
150-iteration race soak clean.

The core architectural claim holds and is now tested: **no graph-defined,
model-produced, artifact-carried, or persisted value can grant authority.**
Enforcement lives where it always did (`ToolExecutor` → `authorize()`);
the graph layer only narrows requests and now fails closed everywhere it
previously trusted input.

## Threat Model

Untrusted: graph YAML, node config, model/provider output, artifacts, repo
files, route labels, verdicts, persisted rows, env paths, timing.
Trusted: developer intent, configured Wisp policy, OS boundary, crypto.
Full boundary map: `TRUST_BOUNDARY_MAP.md` (T1–T20).

## Attack Surface

Parser (YAML→Graph) → validator → executor/scheduler → runner →
orchestrator → ToolExecutor → fs/process/net; plus artifact files, SQLite
rows, trace/CLI output, checkpoints/resume, cancel/retry interleavings.

## Findings

### P0-1 Artifact path traversal (write escape + arbitrary read) — FIXED
- Component: `wisp/graph/artifacts.py`
- Attack: `type="../../evil"` wrote outside the store (PoC confirmed file
  in workspace root; `../../../` escapes the workspace). `get()` joined
  unsanitized refs (read traversal, `.json`-suffixed).
- Root cause: unsanitized `type` interpolated into a filesystem path.
- Fix: filename allowlist (rejects `.`/`..`, requires alnum) + realpath
  containment on every read/write (mirrors `tools/_utils._resolve_path`).
- Test: `test_graph_artifacts.py::TestTraversal` (14 cases).

### P0-2 Resume accepted privilege-escalated definitions — FIXED
- Component: `types.fingerprint` + `executor.resume`
- Attack: fingerprint hashed only ids/edges; changing tools→`run_bash`,
  model, function, policies, or prompts kept the hash → resume executed
  the escalated graph (PoC: equal fingerprints confirmed).
- Fix: canonical fingerprint over all security-relevant fields (contracts,
  tools, models, routes, joins, cycles, policies, budgets, config).
- Test: version/hash refusal tests + tamper matrix in `test_graph_resume.py`.

### P0-3 DSL `bool("false")` idempotency escalation + crash paths — FIXED
- Component: `wisp/graph/dsl.py`
- Attack: `idempotent: "false"` (quoted) coerced to `True` → auto-retry of
  non-idempotent side effects. Garbage types crashed with raw exceptions;
  `tuple("all")` silently split strings; `workspace`/`approval_required`
  silently dropped (governance bypass by omission).
- Fix: strict typed coercion (`_str/_bool/_int/_float`) with path-named
  `ValueError`; 1 MB document cap; string-splitting banned; dropped
  governance fields now parsed.
- Test: `test_graph_dsl.py` (19 cases).

### P0-4 Secrets persisted to artifacts/SQLite/events unredacted — FIXED
- Component: `artifacts.put`, `executor._event`, error messages, CLI inspect
- Attack: API keys/Bearer tokens/AKIA/gh tokens/passwords stored verbatim
  (was a documented known limitation).
- Fix: `wisp/graph/security.py::scrub` (reuses `auth.secrets` + narrow
  graph extras) applied at every persist/display boundary: artifact write,
  event append, exception messages, runner errors, `inspect` output.
- Test: `TestRedaction` (5 secret families + PEM + event sweep).

### P1-1 Failure ran downstream of denied gates — FIXED
- Attack: APPROVAL deny → gate CANCELLED → dependent AGENT still launched
  (test caught it). Any FAILED predecessor released unconditional children.
- Fix: `blocked_by_failure()` — non-JOIN nodes SKIP on failed unconditional
  preds; verdict honesty: sinks must all succeed for SUCCEEDED, denied/
  upstream-skipped sinks → CANCELLED (untaken lanes stay benign via
  `LANE_UNTAKEN`).
- Test: `TestApprovalArmor`, verdict tests.

### P1-2 Cancel unresponsive during long nodes — FIXED
- Attack: 30 s node delayed cancel until timeout (test caught it).
- Fix: `settle_one` waits in 0.5 s slices; cancel preempts, cancels tasks,
  drains, returns CANCELLED.
- Test: cancel-before/during/after/approval matrix.

### P1-3 Stale attempt overwrite — FIXED
- Attack: superseded attempt completing late overwrites current result.
- Fix: per-attempt node rows (`rid:nid#attempt`); generation check on
  settle drops stale completions as superseded (evented, auditable).
- Test: stale-attempt test + 150-iteration soak.

### P1-4 State-tamper distrust — FIXED
- Attack: forged/corrupt rows, bogus checkpoints, cross-workspace resume.
- Fix: only well-formed success rows trusted; corrupt JSON/checkpoints
  refuse or safely re-run; recorded failures never become success;
  resume pins workspace (stored in `graph_def`); foreign node rows ignored.
- Test: `TestStateTampering` (5 cases).

### P1-5 Approval surface — FIXED
- Attack: `approval=true` via inputs/node outputs/artifacts; broken
  `--approve node=true` CLI form silently dropped decisions.
- Fix: approvals readable ONLY from the `resume()` channel; both CLI forms
  parsed strictly; cancel-during-approval honored.
- Test: `TestApprovalArmor` (poison matrix + channel test).

### P1-6 Router/verifier lane confusion — FIXED
- Attack: `reject.tests.extra`, `ACCEPT`, unicode lookalikes, unknown lanes
  entering privileged edges; unknown verifier decisions passing through;
  truthy `"false"` passing gates; unbounded labels.
- Fix: unknown decisions → `reject`; lanes charset-pinned; gates require
  `allowed is True`; exit codes must be int (not bool/str); labels capped.
- Test: 11-case label matrix + verifier/gate tests.

### P1-7 Resource exhaustion — FIXED
- Attack: 1M-branch fanout, huge artifacts/outputs/events, unbounded
  `join_param`/`max_attempts`/timeouts (NaN/Inf hangs), O(n²) dup check.
- Fix: absolute ceilings (1024 nodes/8192 edges/256 mappings/…),
  non-finite/negative number rejection, output/input/artifact/event caps,
  `join_wait` change-only emission, per-attempt rows bounded by attempts.
- Test: caps tests + bomb tests + fuzz suite.

### P1-8 `put_artifact` column-order corruption — FIXED
- Pre-existing bug: `schema_version`/`content_hash` columns swapped, so
  integrity metadata was wrong for every artifact.
- Fix: corrected INSERT order (this also enables hash verification).
- Test: integrity round-trip + tamper rejection.

### P1-9 Runner plumbing — FIXED
- Unbounded task text, unvalidated `max_iterations`/`timeout` (NaN/Inf),
  negative/huge usage stats (budget bypass), spoofable `node_id`,
  non-NodeResult runners crashing settle.
- Fix: caps + finite checks + id pinning + type enforcement.
- Test: `test_runner_tools_come_from_contract_only`.

### P2 items (fixed)
ANSI/DOT injection in trace renderers; `dedupe_findings` empty-key bypass;
`split_by_items`/`classify_label` unbounded; CLI crashes (`--format`,
`--max`, JSON shape) → guarded diagnostics; `inspect` unredacted;
`_result_from_dict` corrupt-row crashes; `2**attempt` overflow cap;
`list_*` negative-limit unbounded reads; scheduler str-policy confusion;
compat caps + clean errors; API `wait()` paused-status guard +
`max_concurrency` clamp; `cancel` false confirmation.

## Security Invariants

| Invariant | Verdict | Evidence |
|---|---|---|
| No graph value grants authority beyond policy | PASS | narrow-only validator + ToolExecutor enforcement + 40 authority tests |
| Every tool call passes ToolExecutor→authorize() | PASS | no subprocess/fs/net in `wisp/graph/` (grep-verified); runner-only path |
| Mutations keep auth→checkpoint→verify→audit | PASS | graph adds no mutation path; artifacts contained + redacted |
| No workspace escape | PASS | realpath containment + charset + policy gate + traversal tests |
| No unintentional secret durability | PASS | scrub-at-rest + redaction tests (known-limitation CLOSED) |
| Router/verifier/approval fail closed | PASS | lane/verdict/approval matrices |
| Stale/cancelled work never succeeds | PASS | generation checks + cancel matrix + soak |
| Resume distrusts tampered state | PASS | hash pin + row validation + workspace pin |
| Resource-bounded (no fan-out DoS) | PASS | absolute ceilings + bomb tests |
| Auditability of security decisions | PARTIAL | graph events persisted + redacted, but NOT yet hash-chained into `ImmutableAuditTrail` (gap G-1) |
| Server/multi-tenant isolation | NOT IMPLEMENTED | no server route exposes graphs — no bypass possible, but also no authz model (gap G-2) |

## Test Statistics

- New security tests: **363** (`tests/security/`, 9 files)
- Functional graph tests: **53** (updated for explicit skip records)
- Surrounding regression: **182** (dispatcher/transports/subagents/runs/tasks)
- Fuzz cases: **~230** parameterized DSL/label/URI/topology cases
- Race iterations: **150-iteration soak** + seeded jitter suites, all clean
- Attack PoCs confirmed pre-fix: traversal write, fingerprint equality,
  `bool("false")` escalation, gate-denial downstream run, cancel delay
- Findings: 19 (4 P0, 9 P1, 6 P2) — all fixed, all with regression tests

## Residual Risk

1. **G-1 (P2):** graph events are not hash-chained into `ImmutableAuditTrail`.
   Tampering is *detected* (hash/integrity/row checks) but not *tamper-evident*
   in the audit sense. Recommended: emit security decisions via existing audit.
2. **G-2 (accepted):** no server exposure — mark before any remote graph API.
3. **G-3 (accepted):** DB-write access implies workspace-write access; a local
   writer can forge success rows. Same trust domain as file writes — no new
   exposure, documented.
4. **G-4 (P3):** `function:` registry is host-trust. CLI/SDK register only
   `default_functions`; hosts must never register I/O callables blindly.
5. **G-5 (P3):** model-behavior (prompt injection persuasion) contained by
   authority boundary, not eliminated — by design.
6. Wisp-shared gaps noted but out of scope: `SECRET_PATTERNS` misses
   `sk-*`/AWS-secret/DB-URIs (graph layer compensates locally); bash tool
   is heuristic-confined (unchanged); post-authorize arg rewrites are never
   re-authorized (upstream issue, flagged).

## Security Gate Verdict

No open P0/P1. All critical invariants have concrete test evidence.
**Status: PASS — production-ready from the graph layer's side**, conditional
on G-1/G-2 before remote/multi-tenant exposure.
