# Phase 10 — Protected-Path Guard Closure

**Repository:** `/Users/philosopher/Documents/wisp`
**Baseline:** Phase 10 at `83b10af` + the Target C REST gate work
**Discovered while:** investigating R1b (`POST /api/hooks` accepts an unvalidated `command`)
**Outcome:** a verified REST privilege-escalation path, now closed; guard canonicalized

---

## 1. Summary

`.wisp/hooks/` holds commands that **Wisp executes itself**, before later tool
calls, with the full process environment. Writing there is a
privilege-escalation primitive: anything that can write a hook can arrange
arbitrary code execution without a further approval. `docs/THREAT-MODEL.md`
names "malicious hook persistence" and points at the guard on this directory
as the mitigation.

**The guard was enforced on the agent tool path and absent from the REST
surface.** Concretely, with the server in `full` mode and a valid API key:

| Path | `.wisp/hooks/x.json` |
|---|---|
| agent tool `write_file` | **refused** — "hook-directory mutation refused (privilege-escalation guard)" |
| REST `POST /api/files` | **written** ← the defect |

This was not a gap in the *hook* route. It was a gap in the **files** route:
the route that mirrors `write_file` was not subject to the guard that
`write_file` is subject to. Adding the policy gate in Target C did not close
it, because the gate consults `SecurityPolicy`, which is mode-based and
performs no argument scan.

---

## 2. Evidence

Reproduction, driving the real FastAPI routes with a throwaway workspace:

```
=== before the fix — mode=full ===
POST /api/files         200   hook dir content = 'OWNED'
POST /api/files/edit    200   hook dir content = 'OWNED'
POST /api/files/binary  200   hook dir content = b'OWNED'
POST /api/files/rename  200   files = ['renamed.json']
DELETE /api/files       200   files = []

=== control: the agent path, same target ===
write_file   .wisp/hooks/pwn.json  allowed=False  layer=arguments
edit_file    .wisp/hooks/pwn.json  allowed=False  layer=arguments
delete_file  .wisp/hooks/pwn.json  allowed=False  layer=arguments
```

`POST /api/bash` **was** covered — by a third implementation (a shell verb
scan in `tools/_utils`), which refused `touch`, `echo >`, `cp`, `tee`, `mv`,
`rm`, `mkdir`, `ln`, `rsync`, `install`, `truncate`, `chmod`, `chown`, and
`sed -i` targeting the directory.

### Two further defects found in the same guard

**(a) `new_path` was never scanned.** The agent-side check read only
`args["path"]`, so renaming *into* the hook directory passed:

```
edit_file {path: "normal.txt", op: "rename", new_path: ".wisp/hooks/x.json"}
  -> allowed=True     (before)
```

**(b) A boundary false positive.** The agent-side check was a bare substring
test, so a look-alike directory was refused too:

```
write_file .wisp/hooksfoo/x.json  -> allowed=False   (before; it is not the hook dir)
```

---

## 3. Root cause — one concept, four implementations, one path with none

| Path | Mechanism | Covered |
|---|---|---|
| agent tools (`auth/decision.py` L4) | inline substring on `path`/`command` | partly — see (a) and (b) |
| shell commands (`tools/_utils.py`) | regex over write verbs | yes |
| filesystem tools (`tools/_utils.py`) | `_is_hook_controlled_path`, boundary-aware | yes |
| REST policy gate (`server/deps.py`) | — | **no** |

The deeper cause is the one this whole engagement kept finding: **two
authorization implementations.** `auth/decision.authorize()` is a 6-layer
model with an argument scan; `infra/security.SecurityPolicy.check()` is a
4-layer model without one. The agent uses the first; REST uses the second. So
every argument-level rule the first has, the second silently lacks.

Target C added the policy gate to the executable-config routes but left the
two-authority structure intact — which is why this gap survived it.

---

## 4. The fix

**One predicate, consulted by every path.** It lives in `wisp/pathsec.py`,
the module already declared as the canonical path-safety primitive
(stdlib-only, no internal dependencies):

```python
PROTECTED_PATH_FRAGMENTS = frozenset({".wisp/hooks", ".wisp\\hooks"})
PATH_BEARING_ARGS        = frozenset({"path", "command", "new_path", "dest", "target"})

def is_protected_path(candidate: str) -> bool: ...
```

`is_protected_path` is boundary-aware — the fragment must end the path or be
followed by a separator — and normalises separators so a Windows-style path is
caught on POSIX and vice versa. It is total: empty/`None`/non-string returns
`False` rather than raising.

| Consumer | Change |
|---|---|
| `wisp/tools/_utils.py` | `_is_hook_controlled_path` delegates; `_SENSITIVE_HOOK_DIR_FRAGMENTS` **aliases** the canonical set (`is` identity, not a copy), so the 5 existing call sites are untouched |
| `wisp/auth/decision.py` L4 | scans **every** `PATH_BEARING_ARGS` key instead of `path`/`command`, using the canonical predicate — closes (a) and (b) |
| `wisp/server/deps.py::require_tool_allowed` | applies the guard **before** the policy verdict, so REST is covered |

`PATH_BEARING_ARGS` is defined beside the predicate so a new path-bearing
argument is added in one place rather than silently escaping one guard.

### Deliberate deltas, stated rather than smoothed over

1. **REST bash is now stricter than agent bash.** The agent's bash path
   distinguishes read from write with a verb scan, so `cat .wisp/hooks/x.json`
   is allowed there. The REST guard is unconditional, so the same command over
   `POST /api/bash` is refused (403). Rationale: REST has no approver, so it
   fails closed, and `GET /api/hooks` is the supported way to inspect hooks.
   Pinned by `test_rest_bash_is_deliberately_stricter_than_the_agent_bash`.
2. **The guard is unconditional on mode.** It denies in `full` mode, because
   `full` relaxes *approval*, not the escalation guard. Pinned by
   `test_guard_applies_even_in_full_mode`.
3. **Reads stay allowed on the agent path.** The guard applies only when
   `risk_for_tool(...) != READ`. Pinned by
   `test_agent_path_allows_reads_of_the_hook_dir`.
4. **The `security.py` policy engine was not changed.** Adding the argument
   scan to `SecurityPolicy.check()` would also have closed the gap, but that
   class has 12 consumers and its own layer model; the guard belongs at the
   REST adapter, which is where the REST authorization decision is made. The
   two-authority structure remains — see §7.

---

## 5. Verification

```
before -> after, mode=full, all five files routes targeting .wisp/hooks/
  POST /api/files         403  content='ORIGINAL'
  POST /api/files/edit    403  content='ORIGINAL'
  POST /api/files/binary  403  content=b'ORIGINAL'
  POST /api/files/rename  403  files=['pwn.json']
  DELETE /api/files       403  files=['pwn.json']
  rename INTO hook dir    403  injected=False

regressions checked
  write src/ok.txt              200  (ordinary writes unaffected)
  write .wisp/hooksfoo/ok.txt   200  (look-alike now correctly allowed)
  read_only write               403  (mode denial unchanged)
  agent rename INTO hook dir    allowed=False  (was True)
  agent .wisp/hooksfoo/x.json   allowed=True   (was False)
  agent read_file hook dir      allowed=True   (reads unaffected)
```

| Command | Result |
|---|---|
| `pytest tests/test_protected_path_guard.py` | **26 passed** (new) |
| `pytest <26 affected files: guard, policy gate, containment, hooks, sandbox, server routes, tool executor, security, MCP>` | **1195 passed** |
| `ruff check wisp/` | **All checks passed** |
| `mypy` (2.3.1) | **exit 0** |

---

## 6. What prevents a second guard

`tests/test_protected_path_guard.py` carries three structural tests:

| Test | Catches |
|---|---|
| `test_authorization_modules_do_not_compare_against_the_fragment` | an AST scan for a **comparison** whose operand is the fragment literal, across `wisp/auth/` and `wisp/server/deps.py` — the exact shape of the old bug. Docstrings and refusal messages are not comparisons and are not flagged. |
| `test_no_unexpected_module_mentions_the_fragment` | an allowlist ratchet over every mention of the fragment anywhere in `wisp/`; a new mention fails until it is routed through the predicate or allowlisted with a reason |
| `test_fragment_list_has_one_definition` + `test_utils_alias_is_the_canonical_object` | a second copy of the fragment list, or an alias that quietly becomes a copy |

Plus 20 behavioural tests pinning the predicate's semantics and each path's
verdicts.

---

## 7. Remaining debt (stated, not hidden)

| # | Item | Nature |
|---|---|---|
| G1 | **Two authorization implementations remain.** `auth/decision.authorize()` (6-layer) and `infra/security.SecurityPolicy.check()` (4-layer). REST uses the weaker one. This finding was a *symptom*; the structure is unchanged. | **Measured — see `PHASE_10_AUTHORIZATION_PARITY.md`.** The relationship is not weaker/stronger but *different-and-incompletely-composed*: the agent consults both layers, REST only one. 9 of 36 (route, mode) pairs diverge; 6 remain (the approval layer, in the **default** mode). Pinned by `tests/test_authorization_parity.py`. One decision open. |
| G2 | The `run_bash` verb scan in `tools/_utils` is still a separate mechanism from the predicate. | Defensible — a shell command's target is not determinable from its text, so a heuristic is unavoidable. Now defence-in-depth rather than the only guard. |
| G3 | `POST /api/hooks` still accepts an unvalidated `command`. | Unchanged from R1b. The gate controls *who* may register a hook; *what* it may run is a separate question, and hooks exist precisely to run arbitrary commands. |
| G4 | The protected-fragment allowlist has six entries, three of which are path *constructors* rather than guards. | Acceptable; the ratchet makes any seventh entry a deliberate decision. |

---

## 8. Honest note on discovery

This was found by asking whether R1b was still a defect after Target C closed —
not by looking for a bug. The reasoning that surfaced it: *"if the agent is
refused a hook write and REST is not, which of them is wrong?"* The answer was
that they disagreed, and the disagreement was the finding.

It is the **sixth** time in this engagement that a conclusion changed under
execution rather than reading, and the second time (after the `tool_health`
scoring bug) that a defect was found in a subsystem nobody had listed. Both
came from the same habit: drive the real path, then compare what the paths say
about the same input.

---

## 9. A test-isolation defect this work exposed

Adding the guard tests made six *pre-existing* tests fail:

```
tests/test_server_policy_gate.py::test_bash_denied_in_read_only          -> 429
tests/test_server_policy_gate.py::test_bash_allowed_in_full              -> 429
tests/test_server_policy_gate.py::test_hook_create_denied_in_read_only   -> 429
...
```

They passed alone and failed together, and stayed red across repeated runs.

**Cause.** `RATE_LIMITER` is a *process-external* singleton: a SQLite file at
`~/.config/wisp/rate_limits.db`, **30 requests per 60 s**, keyed by client IP
(`deps.py:321-330`). Route tests therefore shared one budget with each other
**and with every previous run** — once a run made 30 requests to gated routes
inside a minute, every later route test received 429 instead of the verdict it
was asserting. The suite already neutralized the *auth* singleton
(`conftest.py:58`) but not the limiter.

**Not a product defect.** The limiter behaves as designed; the tests were not
hermetic. It is the same class as the `test_sandbox_fallback_contract` ordering
pollution noted elsewhere in this engagement.

**Fixed** — `tests/conftest.py::_neutralize_server_rate_limit`, a session-scoped
autouse fixture alongside the existing auth neutralization. It replaces
`deps.get_rate_limiter` with a permissive double; `RATE_LIMITER` resolves
`get_rate_limiter()` at request time (`deps.py:355-356`), so the patch takes
effect without touching route definitions.

**The limiter's own tests are unaffected**: `tests/test_server_deps.py`
constructs `SQLiteRateLimiter` instances against a `tmp_path` rather than going
through the singleton. `WISP_E2E_LIVE=1` runs keep ambient behaviour.

**Result:** the combination that failed now passes (40 passed), and the
affected-subsystem sweep went from 1195 to **1295 passed** — the difference
being tests that had been silently reachable only in the right order.
