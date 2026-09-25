# PHASE POST-M13 — F8 TOOL-ARGUMENT VALIDATION AUTHORITY RECON

**Mode: FORENSIC READ-ONLY.** Nothing was installed, nothing was modified, no default was changed. This
phase produces understanding, not code.

| Field | Value |
|---|---|
| Phase | POST-M13 — F8 tool-argument validation authority recon |
| Predecessor | `PHASE_POST_M13_STAGNATION_GATE_VALIDATION.md` (identified F8 as remaining risk 1) |
| `HEAD` | `7c15626630f6dca8f37e27c638e59df0acb216e5` · branch `main` |
| Production changes | **0** · dependencies installed **0** · lockfiles changed **0** · defaults changed **0** |
| Verdict | `REPAIR_CLASS: MIXED` · `ADR_REQUIRED: NO` · `IMPLEMENTATION_AUTHORIZED: NO` |

---

## 1. Mission Result

**F8 is a VALID-CALL INFRASTRUCTURE REFUSAL, and the host validator never runs.**

`jsonschema>=4.0` is a **declared runtime dependency** of this package and is **locked** in `uv.lock` — but
it is **absent from the virtualenv**. The engine's validator imports it *inside* the `try` whose
`except Exception` returns a schema-failure string, so `ModuleNotFoundError` is laundered into a
tool-argument verdict:

```
"Schema validation failed for tool 'read_file': No module named 'jsonschema'"
```

A **valid** call and a **model-invalid** call produce **byte-identical** refusal text. The refusal is not a
security decision, not a policy denial, and not a model error. It is a missing dependency wearing a
verdict's clothes.

**The refusal is not an architectural choice.** Validation is *correctness/type-safety* defense-in-depth, not
the security boundary (which is `SecurityPolicy.check` + `ApprovalGate`), and the intended contract is
explicit in three places at once (`pyproject.toml`, `uv.lock`, the code). The repair is therefore
**mechanical** — no ADR.

**And it is achievable in this environment**: the wheels for the whole dependency tree are in the local uv
cache, and the interpreter's TLS failure has a one-line, no-new-dependency fix. Neither was used, because
installing is a non-goal of this phase.

---

## 2. Entry Criterion

```
=== ENTRY CRITERION ===
POST_M13_GATE_VALIDATED: YES
F8_REPRODUCIBLE: YES
JSONSCHEMA_STATE_INSPECTABLE: YES
VALIDATION_PATH_TRACEABLE: YES
WORKTREE_BASELINE_IDENTIFIABLE: YES
OPEN_F8_ADR_CONTRADICTION: NO
ENTRY: PASS
========================
```

No ADR addresses F8. It appears in the record only as a **finding** (F8 in
`WISP_MIGRATION_STATUS.md` §23) and as a **known blocker** (`CONTEXT.md` §6, the workspace `MEMORY.md`), not
as a decision. Nothing supersedes it, and nothing authorises it either.

---

## 3. Baseline

| Fact | Value |
|---|---|
| `HEAD` / branch | `7c15626…` · `main` |
| Tree | 41 tracked modifications (20 under `wisp/`), 57→58 untracked |
| Pre-existing WIP | the uncommitted M13/POST-M13 chain — **not** this phase's |
| Interpreter | CPython **3.12.8**, venv at `<repo>/.venv` |
| Package manager | **`uv` is NOT on `PATH`**; the venv has its own `pip` |
| Lockfile | `uv.lock` present, current, contains `jsonschema 4.26.0` |
| Dependency metadata | `pyproject.toml` → `[project].dependencies` |

### `jsonschema` state — the six questions, answered

| Question | Answer | Evidence |
|---|---|---|
| DECLARED? | **YES** — a runtime dependency | `pyproject.toml`: `"jsonschema>=4.0",` in `dependencies = [...]` |
| LOCKED? | **YES** — pinned to 4.26.0 | `uv.lock`: `name = "jsonschema"` / `version = "4.26.0"` / `specifier = ">=4.0"` |
| INSTALLED? | **NO** | `pip show jsonschema` → *"Package(s) not found"* |
| IMPORTABLE? | **NO** | `ModuleNotFoundError: No module named 'jsonschema'` |
| OPTIONAL? | **NO** | it is in `dependencies`, not `[project.optional-dependencies]` |
| DEV-ONLY? | **NO** | same |
| TRANSITIVE? | **NO** — it is a *direct* declaration, and itself pulls `attrs`, `jsonschema-specifications`, `referencing`, `rpds-py` | `uv.lock` |

**`JSONSCHEMA_STATUS: DECLARED + LOCKED + ABSENT`.** That is an **incomplete environment**, not a packaging
defect: the metadata is correct and consistent in both directions.

---

## 4. F8 Reproduction

Deterministic, minimal, and executed (`.workbuddy-ai/memory/post-m13-f8-recon/reproduce_f8.py`). One call
through the **same** validator, five input shapes:

| Input | Kind | `_validate_tool_args` returns |
|---|---|---|
| `read_file(path="a.txt")` | **structurally VALID** | `"Schema validation failed for tool 'read_file': No module named 'jsonschema'"` |
| `read_file(path="a.txt", offset=0, limit=10)` | **structurally VALID** | *identical string* |
| `read_file()` | model-invalid (missing required) | *identical string* |
| `read_file(path=123)` | model-invalid (wrong type) | *identical string* |
| `read_file(path="a.txt", nonsense=True)` | model-invalid (extra property) | *identical string* |
| `definitely_not_a_tool(anything=1)` | no schema registered | `None` — **passes** |

```
valid call refused?     True
invalid call refused?   True
same refusal text?      True
refusal names the dep?  True
```

**The discriminator the brief asks for is decisive.** A valid call is refused with the same text as an
invalid one, and that text names the missing module. The validator did not reject the arguments; it never
evaluated them. `jsonschema` is absent from **every** `sys.path` entry.

```
CLASSIFICATION: VALID-CALL INFRASTRUCTURE REFUSAL
NOT a model error. NOT a policy denial. NOT a security decision.
```

---

## 5. Exact Source Trace

```
provider / model output
  │  stateless.py  tool-call parsing → `pending_tool_calls`
  ▼
role / tool-allowlist check                        stateless.py:497-501
  ▼
★ _validate_tool_args(..., _dry_run=True)          stateless.py:511-514   ← F8 INTERRUPTS HERE
  ▼  on refusal:  tc_event["_denial"]="SCHEMA_INVALID"      stateless.py:517
  │               yield error_event(f"Blocked: {_schema_error}", recoverable=True)  :521-526
  │               continue                                            :527
  ▼
ApprovalGate.check_decision → SecurityPolicy.check  stateless.py:529-546  ← NEVER REACHED
  ▼
extensions intercept                                stateless.py:549
  ▼
_execute_tool                                       stateless.py:905
  ▼  _validate_tool_args(name, args)  (defense-in-depth, no _dry_run)   stateless.py:1924
  ▼  ToolExecutor.execute(name, args, workspace, …) stateless.py:1953
  ▼
tool implementation → tool result
  ▼
guard.note_tool_result(...)                         stateless.py:928   ← NEVER REACHED
  ▼
verification / completion
```

The single-tool path is the same shape at `stateless.py:616-632`.

```
F8_INTERRUPT_POINT:
wisp/core/stateless.py::WispAgentCore._validate_tool_args  (stateless.py:2225)
  — reached from stateless.py:511 (batch) and :616 (single), both with _dry_run=True

PRE_F8:   provider output parsed · tool call normalized · role/allowlist check passed ·
          schema located in TOOL_SCHEMAS
POST_F8:  approval gate · SecurityPolicy authorization · extension intercept ·
          ToolExecutor.execute · tool implementation · tool result ·
          guard.note_tool_result · verification floor · P3 acceptance
```

### The mechanism, in three lines

```python
try:
    import jsonschema                    # stateless.py:2286   ← the import is INSIDE the try
    jsonschema.validate(instance=args, schema=schema)
    return None
except Exception as exc:                 # stateless.py:2289   ← catches ModuleNotFoundError too
    ...
    return f"Schema validation failed for tool '{name}': {exc}"   # stateless.py:2298
```

`ModuleNotFoundError` is an `Exception`. The handler that exists to report *"your arguments are wrong"*
also catches *"the validator does not exist"*, and reports both as the former. There is a second, identical
import at `:2292` inside the `write_file` retry — it fails the same way and is dead in this environment.

---

## 6. Authority Map

| Authority | Owner | Source |
|---|---|---|
| **Schema** | `wisp/tools/registry.py::TOOL_SCHEMAS` (42) + `wisp/tools/primitives.py::PRIMITIVE_SCHEMAS` (3) | `stateless.py:2238, 2246`; `primitives.py:181` |
| **Validation** | `WispAgentCore._validate_tool_args` — engine-side, `jsonschema`-backed | `stateless.py:2225` |
| **Authorization** | `infra/security.SecurityPolicy.check()` via `ApprovalGate.check_decision` | `approval_gate.py:88` |
| **Approval** | the same gate, `REQUIRE_APPROVAL` branch only | `approval_gate.py:96-111` |
| **Execution** | `wisp/tool_executor.py::ToolExecutor.execute` | `stateless.py:1953` |
| **Verification** | `core/verification.py::VerificationFloorGuard` (+ P3 projection) | `stateless.py:928`; `verification.py:293` |

**The desired separation holds for the engine path**, with one correction the source forces: the gate's
internal order is **authorization → approval**, not approval → authorization. `SecurityPolicy.check` decides
first; `REQUIRE_APPROVAL` is what *may then* consult a human (`approval_gate.py:88-99`). A hard `DENY` never
invokes the handler, so no `y` can override policy (13F.1 R1).

---

## 7. Schema Contract

| Property | Finding |
|---|---|
| `required` | used by 47 schema fragments; correct and enforced (when the validator runs) |
| `properties` | 49 fragments |
| `type` | 186 fragments — the workhorse |
| `items` / `minItems` | 11 / 1 |
| `enum` | 3: `fanout.mode`, `fs_mutate.op`, `git_checkpoint.action` |
| `minimum` | 3 |
| `additionalProperties` | **declared `False` by exactly 1 of 42 tools (`fanout`)** |
| `default` / `description` | 46 / 128 — **annotations**, ignored by both validators |

**`additionalProperties` is unset for 41 of 42 tools**, which is JSON Schema's *permissive* default: extra
properties are accepted. So validation is **not** a strict-shape boundary for almost every tool — an
"unexpected property" refusal is not something the current contract can produce. (Reproduced: `read_file`
with `nonsense=True` is accepted by the fallback validator, which reads the same default.)

### The 13-J nullable-argument contract

`model`, `timeout_seconds` and `max_iterations` **are declared**, all optional, all as bare
`"type": "string"` / `"type": "number"`:

| Field | Tools | Declared | Required |
|---|---|---|---|
| `model` | `spawn`, `spawn_background` | `{"type": "string"}` | no |
| `timeout_seconds` | `spawn`, `spawn_background`, `subagent_wait` | `{"type": "number"}` | no |
| `max_iterations` | `spawn`, `spawn_background` | `{"type": "number"}` | no |

**No tool anywhere declares `"type": "null"` or a union containing `null`.** So a model that emits
`"timeout_seconds": null` — a very common way to say "unset" — is *rejected* by the declared schema
(reproduced: `read_file(limit=None)` → `Expected type number, got NoneType`).

**F8 does not interact with that contract; it masks it.** Under F8 every call is refused for the same
unrelated reason, so the nullable-argument class is currently invisible. **A repair will surface it** — and
it will be a *correct* refusal per the declared schema, but a hostile one for models. That is a
schema-policy question, **not** an F8 question, and this phase does not touch it.

---

## 8. Dependency Investigation

| Hypothesis | Verdict |
|---|---|
| **A. Not declared** | **NO** — it is in `[project].dependencies` |
| **B. Not locked** | **NO** — `uv.lock` pins 4.26.0 with a `>=4.0` specifier |
| **C. Absent from this environment** | **YES — this is F8** |
| **D. Optional / behind an extra** | **NO** — a plain runtime dependency |
| **E. Packaging could omit it** | Not shown. The metadata is self-consistent; a supported install that honours it would include it |
| **F. Incorrect import structure** | **YES, as a second defect** — the import is inside the `except Exception` that returns a verdict (see §5) |
| **G. Version mismatch** | **NO** — the lock pins 4.26.0, far above the `jsonschema.validate` API the code uses |
| **H. Test harness excludes it** | **NO** — the absence is the *venv's*, not a harness's; it affects the runtime identically |
| **I. Documented offline-secure behaviour** | **NO such mode is documented for tool validation** (see §9) |
| **J. A clean supported install would contain it** | **YES** — it is declared and locked |

### Why the obvious repair is not obvious here

Two environment facts compound, and both are outside the project:

1. **The interpreter has no CA path.** `ssl.get_default_verify_paths()` → `cafile: None`, `capath: None`.
   So `urllib`/`pip` fail with `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`. The
   project's standing note — *"No network (SSL cert verification fails), so `pip install` cannot fix any of
   it"* — is **precisely correct for the venv's Python**, and this phase confirmed it rather than assuming
   it. (Raw shell egress works, because `curl` uses the system trust store; the interpreter does not.)
2. **`uv` is not on `PATH`**, so the documented `uv sync` workflow cannot be run either.

**Both are repairable without installing anything new**, and this phase verified that without doing it:

- `certifi` is **already installed** in the venv
  (`.venv/lib/python3.12/site-packages/certifi/cacert.pem`), and the interpreter honours `SSL_CERT_FILE`.
  Setting it made the same request succeed: `TLS+HTTP OK`, PyPI reports `4.26.0` available.
- The uv wheel cache **already holds the whole tree offline**:
  `wheels-v6/pypi/jsonschema/4.26.0-py3-none-any.msgpack` — the exact universal wheel `uv.lock` names — and
  `wheels-v6/pypi/rpds-py/2026.6.3-cp312-cp312-macosx_11_0_arm64.msgpack`, a cp312/arm64 build matching
  this interpreter. `attrs`, `referencing` and `jsonschema-specifications` are cached too.

So the condition is **not permanently blocked**. It was not repaired here because installing is a hard
non-goal.

---

## 9. Supported Environments

No document declares a supported mode in which tool execution works without argument validation. There is
no offline-secure tool-validation mode, no "minimal install" extra, and no air-gapped exception.

| Environment | `jsonschema` expected? | Tool execution expected? | Current behaviour |
|---|---:|---:|---|
| local development | **yes** — a declared runtime dep | yes | **all calls refused (F8)** |
| offline / air-gapped | **yes** — it is a runtime dep, not fetched lazily | yes | refused, unless the wheel is present in a local cache |
| CI / headless | **yes** | yes | refused, if the venv is built without it |
| production | **yes** | yes | refused |

The `offline` row is the one worth stating plainly: **offline operation is not the cause and not a licence
to degrade.** The dependency is a runtime requirement, and a correctly-provisioned offline environment
carries it (this machine's uv cache already does). F8 is a *provisioning* gap, not a supported mode.

---

## 10. Existing Fallback Analysis

**Three** argument-validation mechanisms exist. They are not equals, and they do not all guard the same
thing.

| # | Mechanism | Scope | Dependency | Authoritative for tool args? |
|---|---|---|---|---|
| 1 | `WispAgentCore._validate_tool_args` → `jsonschema` | every tool in `TOOL_SCHEMAS` | **third-party** | **yes — this is the canonical one** |
| 2 | `wisp/tools/primitives.py` — pydantic `BaseModel`s (`ExecSandboxArgs`, `FsMutateArgs`, `GitCheckpointArgs`) + `_validated()` | the 3 primitives only | pydantic (installed) | only when `thin_tools=True`, because mechanism 1 no-ops there |
| 3 | `wisp/tools/web.py:343` — hand-written checks, *"no pydantic; these checks ARE the schema"* | web tools | none | inside those tools |

**And the one that matters for F8:**

| # | Mechanism | Scope | Dependency |
|---|---|---|---|
| 4 | **`wisp/multi_agent/schema_validator.py::validate_json_schema`** — a hand-written, **stdlib-only** JSON Schema subset validator | **subagent structured *output*** contracts, not tool arguments | none (`json`, `re`, `typing`) |

Mechanism 4 is a genuine, tested, dependency-free validator that implements `type` (incl. unions),
`required`, `properties`, `additionalProperties` (when `False`), `items`, `minItems`/`maxItems`,
`minLength`/`maxLength`, `pattern` (with an explicit ReDoS guard), `minimum`/`maximum`, and `enum`.

**It is not currently a fallback for tool arguments.** It is wired to `validate_subagent_output` and serves a
different authority: a subagent's *prose* output against a declared *contract*. Nothing calls it from the
tool path, and its return shape `(bool, list[str])` differs from the engine's `Optional[str]`.

---

## 11. Fallback Safety Test (read-only)

Comparing mechanism 4 against the canonical schemas, **without modifying anything**.

### The decisive question: does any schema use a keyword it cannot interpret?

All **45** schemas (42 tool + 3 primitive) were walked for keyword usage:

| Keyword | Uses | Fallback verdict |
|---|---|---|
| `type` | 186 | implemented |
| `properties` | 49 | implemented |
| `required` | 47 | implemented |
| `items` | 11 | implemented |
| `enum` | 3 | implemented |
| `minimum` | 3 | implemented |
| `additionalProperties` | 2 | implemented (honoured when `False`) |
| `minItems` | 1 | implemented |
| `description` / `default` | 128 / 46 | annotations — ignored by **both** validators |

```
constraining keywords the fallback cannot interpret: NONE
unknown keywords:                                  NONE
```

**So the fallback is feature-complete for the 45 schemas that exist today.** It does not implement `$ref`,
`oneOf`/`anyOf`/`allOf`, `not`, `format`, `exclusiveMinimum`, `multipleOf`, `const`,
`patternProperties`, `uniqueItems` — but **no schema uses any of them**, so none of those gaps is reachable.

### The nine required input classes, executed against `read_file`'s canonical schema

| Input class | Fallback result | Agrees with the declared schema? |
|---|---|---|
| valid arguments | accepted | yes |
| missing required argument | rejected — `Missing required field: path` | yes |
| wrong primitive type | rejected — `Expected type string, got int` | yes |
| extra property | **accepted** | yes — `additionalProperties` is unset ⇒ permissive |
| nested invalid property | rejected — `offset: Expected type number, got dict` | yes |
| enum violation | rejected (verified on `fanout.mode`) | yes |
| null where forbidden | rejected — `Expected type string, got NoneType` | yes |
| null where allowed | rejected — **no field declares `null`**, so the class is empty | n/a |
| array item violation | rejected — `Expected type string, got list` | yes |

```
FALLBACK_EQUIVALENCE: NOT PROVEN
```

**Not proven, and not provable here** — `jsonschema` is absent, so no differential run against it is
possible. What *is* proven is narrower and useful: the fallback covers every keyword the current schemas
use, and agrees with the declared schema on all nine input classes that are reachable. **Equivalence in
general is not established** (the subset would silently accept an instance that `$ref`/`oneOf` should
reject), so per §11 of the brief this phase **does not recommend substituting it**.

---

## 12. Approval / Authorization Ordering

**Established from source, both levels.**

```
VALIDATION          stateless.py:511        _validate_tool_args(_dry_run=True)
   ↓
AUTHORIZATION       approval_gate.py:88     SecurityPolicy.check(action, context)
   ↓  hard DENY → returns, handler never invoked (13F.1 R1)
APPROVAL            approval_gate.py:96-111 only on REQUIRE_APPROVAL
   ↓
EXTENSIONS          stateless.py:549
   ↓
EXECUTION           stateless.py:1953       ToolExecutor.execute(...)
```

**Validation now precedes approval**, which is the 13-J1 repair: the source says so explicitly —
*"Structural validation BEFORE approval (13-J1): an invalid proposal must never reach the human prompt"*
(`stateless.py:502-504`, `:613-615`), and the refusal branch `continue`s so the prompt is never built.

**The gate's internal order is authorization → approval**, not the reverse. `SecurityPolicy.check` runs
first; only `REQUIRE_APPROVAL` may consult a human. This is worth stating because the brief lists
approval-before-authorization as a possibility and it is not the current shape.

**Not repaired, not changed.** Ordering is unchanged by this phase.

---

## 13. ToolExecutor Reachability

**Schema validation is NOT a universal precondition for `ToolExecutor`.** It is a property of *one* caller
and *one* schema table, and it silently no-ops in three situations.

| # | Path | Reaches `ToolExecutor`? | Schema-validated? |
|---|---|---|---|
| 1 | engine batch path | yes — `stateless.py:1953` | yes, at `:511` and again at `:1924` |
| 2 | engine single path | yes | yes, at `:616` and `:1924` |
| 3 | **`thin_tools=True`** | yes | **NO** — see below |
| 4 | **unknown tool name** | no (security layer handles it) | **NO** — by explicit design |
| 5 | **ACP `execute_tool`** | **yes — `acp_session.py:237`** | **NO** |
| 6 | subagent runner | receives the executor (`_runner.py:475`) | inherited from whichever path drove it |
| 7 | background / headless | via the engine or the ACP shape | as above |

**Finding 3a — `thin_tools=True` disables engine validation entirely.**
`_get_tool_schemas()` returns **`PRIMITIVE_SCHEMAS` only** when `thin_tools` is on (`stateless.py:1867-1870`),
so the model can see `exec_sandbox`, `fs_mutate`, `git_checkpoint`. But `_validate_tool_args` searches
**`TOOL_SCHEMAS`** (`stateless.py:2238, 2246`), where **none of the three appears** (verified: the overlap
between the two tables is empty). Every lookup returns `schema is None` → `return None` → *valid*. So with
`thin_tools=True`, the engine's schema validation is a **complete no-op for every tool the model can see**,
and argument validation rests solely on the pydantic models inside the primitives.

**Finding 3b — an unknown tool name passes validation by design.**
`if schema is None: return None  # Unknown tool — let security layer handle it` (`stateless.py:2251-2252`).
This is deliberate delegation, and it is why validation cannot be described as *the* boundary.

**Finding 3c — the ACP path reaches `ToolExecutor` with no engine validation.**
`acp_session.py:237-242` calls `executor.execute(tool_call.name, dict(tool_call.arguments), …)` directly.
`ToolExecutor` contains **no schema validation** — the only `jsonschema` import in the entire tree is in
`stateless.py`. So this path is governed by policy and approval, and by whatever the tool itself checks.

---

## 14. Bypass Analysis

| Path | Classification | Reasoning |
|---|---|---|
| unknown tool name passes `_validate_tool_args` | **INTENDED INTERNAL BYPASS** | the comment says so; the security layer is the intended gate for names it does not know |
| `thin_tools=True` no-ops engine validation | **BUG** (a silent consequence, not a decision) | the model is shown `PRIMITIVE_SCHEMAS` while validation reads `TOOL_SCHEMAS`; no comment acknowledges it; the primitives are still pydantic-validated, so the effect is a *weaker* boundary rather than none |
| ACP `execute_tool` skips engine validation | **SAFE BY DESIGN** | `acp_session.py:195-201` documents that direct `execute_tool` was removed because it *"bypassed every guard (audit P2 #10)"*; the path now goes through `ToolExecutor`, so policy and approval apply. Schema validation is absent, but it was never the boundary |
| `_execute_tool`'s second `_validate_tool_args` call | **SAFE BY DESIGN** | defense-in-depth on the same engine path; not a bypass |

**`ToolExecutor_bypasses_found: 3`** — one intended, one a bug, one safe by design. **None repaired here.**

---

## 15. Coding-Agent Impact

**Everything is blocked, and it is blocked at the same place for the same reason.** Because the refusal
happens before dispatch, the effect is uniform:

| Capability | Blocked? | Blocked by |
|---|---|---|
| file reads (`read_file`, `list_files`, `search_symbols`) | **yes** | validation infrastructure — the validator never ran |
| file writes / edits (`write_file`, `edit_file`) | **yes** | same |
| shell (`run_bash`) | **yes** | same |
| git / checkpoints | **yes** | same |
| verification tools (`run_tests`) | **yes** | same |
| subagents (`spawn`) | **yes** | same |
| web fetch / search | **yes** | same |

**The mandatory distinction, stated:** these are **not** authorization denials (`POLICY_DENIED`), **not**
approval denials (`USER_DENIED`), and **not** tool-specific failures. They are
**`SCHEMA_INVALID`** — a validation-infrastructure refusal that is *classified as a content failure*.
Reproduced directly: a valid `read_file` is refused, and the only denials the engine can emit at that site
are `_denial = "SCHEMA_INVALID"` (`stateless.py:517`).

One consequence compounds: `SCHEMA_INVALID` is a **final** denial in the model's own instructions —
*"DENIALS ARE FINAL: a tool result whose status is POLICY_DENIED, USER_DENIED, APPROVAL_TIMEOUT, CANCELLED
or SCHEMA_INVALID never succeeds on retry"* (`context_assembler.py:157`) — and `recovery.py:143` lists it
among the classes that never retry. So the model is told, correctly per its own rules, that retrying is
pointless: it cannot discover that the real problem is a missing package.

---

## 16. P3 / ADR-0016 Impact

**Every arrow verified by execution** (`.workbuddy-ai/memory/post-m13-f8-recon/f8_to_p3_impact.py`), not
argued from source.

```
F8  →  the call is refused before dispatch            stateless.py:511-527
    →  the blocked branch `continue`s before _execute_tool      :889-898
    →  guard.note_tool_result is never reached                  :928
    →  wrote_code stays False                            verification.py:144-148
    →  rejection() returns None  (nothing to block on)   verification.py:170
    →  the floor criterion is VACUOUSLY satisfied        verification.py:252
    →  but floor_guard_evidence emits []                 verification.py:272-273
    →  a required criterion with no evidence
    →  P3 VERDICT = INCONCLUSIVE                         acceptance.py:227
    →  ADR-0016's measurement is unobtainable
```

Measured, with controls:

| Scenario | `wrote_code` | `rejection()` | evidence | **P3 verdict** |
|---|---|---|---|---|
| **no tool ran at all (the F8 shape)** | False | None | 0 | **INCONCLUSIVE** |
| a refused *mutating* call | False | None | 0 | **INCONCLUSIVE** |
| a real mutation, unverified | True | *HARNESS REJECTION* | 1 | **FAIL** |
| a real mutation, then `run_bash` exit 0 | True | None | 1 | **PASS** |

**F8 is the sole cause of this, and the source proves it.** Nothing else in the chain is broken: with a
real mutation the guard blocks, and with a real mutation plus a green verification P3 reaches `PASS`. The
only thing standing between the current environment and `PASS` is that no tool can execute.

**The sharper statement, which matters more than "unavailable".** ADR-0016's measurement is the
`INCONCLUSIVE` **rate** at P3 stage 3b. In this environment **every** turn is `INCONCLUSIVE` by
construction, so the measurement would return a degenerate 100% and would characterise the *environment*,
not the gate. A measurement taken here would be worse than none: it would look like data.

---

## 17. Repair Options

Not ranked. Each with its required attributes.

### Option A — dependency / package repair

Ensure the declared runtime dependency is present wherever tool execution is supported.

| Attribute | Assessment |
|---|---|
| `AUTHORITY_IMPACT` | none — `jsonschema` is already the intended validation authority |
| `SECURITY_IMPACT` | none — validation is not the security boundary |
| `SCHEMA_COMPATIBILITY` | full — this is the library the schemas were written for |
| `REPLAY_IMPACT` | none — validation is pre-dispatch, produces no durable record of its own |
| `OFFLINE_IMPACT` | none in principle; a correctly-provisioned offline environment carries it. **Verified feasible here**: the uv cache already holds `jsonschema 4.26.0` + `rpds-py cp312/arm64` |
| `PACKAGING_IMPACT` | none — the metadata is already correct and needs no edit |
| `TEST_SCOPE` | the ~11 test files currently red *because* of F8; plus a new test asserting a valid call executes |
| `ADR_REQUIRED` | **NO** |

### Option B — import / runtime repair

Correct the dependency-loading path so its absence cannot masquerade as a verdict.

| Attribute | Assessment |
|---|---|
| `AUTHORITY_IMPACT` | none — the same validator, correctly reported |
| `SECURITY_IMPACT` | none |
| `SCHEMA_COMPATIBILITY` | unchanged |
| `REPLAY_IMPACT` | none |
| `OFFLINE_IMPACT` | none |
| `PACKAGING_IMPACT` | none |
| `TEST_SCOPE` | a test that an absent validator is *not* reported as an invalid argument |
| `ADR_REQUIRED` | **NO** if the error is simply surfaced as an error; **YES** if it introduces a *new denial class* (that changes the denial taxonomy, `core/events.py` + `core/recovery.py`) |

### Option C — existing-validator repair

Promote `multi_agent/schema_validator.py` to a tool-argument fallback.

| Attribute | Assessment |
|---|---|
| `AUTHORITY_IMPACT` | **creates a second validation authority** for tool args — the exact defect class this migration exists to remove |
| `SECURITY_IMPACT` | validation stays security-adjacent, but the *semantics* would now depend on which validator ran |
| `SCHEMA_COMPATIBILITY` | **feature-complete for today's 45 schemas** (§11) — but not equivalent to `jsonschema` in general; a future `$ref`/`oneOf` would silently pass |
| `REPLAY_IMPACT` | none directly, but two validators could accept/reject differently over time |
| `OFFLINE_IMPACT` | positive — no third-party dependency |
| `PACKAGING_IMPACT` | none |
| `TEST_SCOPE` | a differential suite, which cannot be written without `jsonschema` present |
| `ADR_REQUIRED` | **YES** — it changes validation authority and fallback behaviour |

### Option D — validation-architecture repair

Introduce a canonical validation abstraction so the implementation is replaceable without weakening
semantics.

| Attribute | Assessment |
|---|---|
| `AUTHORITY_IMPACT` | preserves one authority; makes the *implementation* pluggable |
| `SECURITY_IMPACT` | neutral if semantics are preserved |
| `SCHEMA_COMPATIBILITY` | must be proven per implementation |
| `REPLAY_IMPACT` | none |
| `OFFLINE_IMPACT` | enables an offline story *deliberately* rather than accidentally |
| `PACKAGING_IMPACT` | none |
| `TEST_SCOPE` | large — a conformance corpus per implementation |
| `ADR_REQUIRED` | **YES** — a dependency-policy and authority change |

### Option E — deliberate capability degradation

Keep validation unavailable and explicitly disable tool execution.

| Attribute | Assessment |
|---|---|
| `AUTHORITY_IMPACT` | removes the agent's ability to act |
| `SECURITY_IMPACT` | arguably *safer* (nothing executes) |
| `SCHEMA_COMPATIBILITY` | n/a |
| `REPLAY_IMPACT` | n/a |
| `OFFLINE_IMPACT` | none |
| `PACKAGING_IMPACT` | none |
| `TEST_SCOPE` | large — many tests assume execution |
| `ADR_REQUIRED` | **YES** — it would reverse the product's purpose |

**No option is selected here.** A is the one that restores the intended contract; B is a real second defect
found on the way; C and D are architecture changes that the evidence does **not** require, and C is
explicitly not recommended while equivalence is unproven.

---

## 18. Minimal Repair Boundary

**One canonical boundary, already existing: `WispAgentCore._validate_tool_args` (`stateless.py:2225`).**

```
AUTHORITY BOUNDARY   wisp/core/stateless.py::WispAgentCore._validate_tool_args
                     — the single site that decides "do these arguments satisfy the schema?"
DEPENDENCY EDGE      the `import jsonschema` inside it (stateless.py:2286)
```

**The smallest repair is not a code change at all: it is provisioning.** `jsonschema` is already declared
and already locked; the venv simply does not have it. Nothing in `wisp/` needs to move for the primary
repair.

The **secondary** repair — one line of intent, at the same function — is to stop the dependency's absence
from being reported as an argument verdict. Two contract-preserving shapes exist, and this phase chooses
neither:

- import the validator at module scope, so its absence fails loudly at import rather than per-call; or
- keep the lazy import but separate *"the validator is unavailable"* from *"the arguments are invalid"*.

The second is a **taxonomy** question (does a new outcome exist?) and is the boundary between a mechanical
fix and an ADR — see §19.

**Explicitly out of scope:** redesigning `ToolExecutor`, rewriting schemas, migrating validation frameworks,
promoting the fallback, changing approval ordering, and the three reachability findings in §13. All are
recorded, none is touched.

---

## 19. ADR Requirement

**`ADR_REQUIRED: NO`** for the repair that restores the intended contract.

Against §19's four conditions for a mechanical repair:

| Condition | Verdict |
|---|---|
| the intended contract is already explicit | **yes** — declared in `pyproject.toml`, locked in `uv.lock`, and written into the code as a plain `jsonschema.validate` |
| source and docs agree | **yes** — nothing claims tool validation works without `jsonschema`, and no doc describes an offline no-validator mode |
| the dependency is clearly required | **yes** — a direct `[project].dependencies` entry |
| the repair restores existing behaviour | **yes** — it restores the behaviour the code was written to have |

Against the eight things that would force an ADR:

| Would the repair change… | Verdict |
|---|---|
| validation authority | no — `jsonschema` is already the authority |
| the security boundary | no — validation is not the boundary (§20) |
| supported offline behaviour | no — offline was never a mode in which validation is absent |
| dependency policy | no — the dependency is already declared |
| schema semantics | no — the schemas are unchanged |
| approval ordering | no — unchanged |
| `ToolExecutor` authority | no |
| fallback behaviour | no — no fallback is introduced |

**Two things that *would* require an ADR, neither of which is this repair:**

1. **Promoting `multi_agent/schema_validator.py` to a tool-argument fallback** (Option C) — changes validation
   authority and fallback behaviour, and would put two validators behind one question.
2. **Introducing a distinct outcome for "the validator is unavailable"** — this adds a member to the denial
   taxonomy (`core/events.py`, `core/recovery.py`, and the model-facing "DENIALS ARE FINAL" rule in
   `context_assembler.py`). Fixing the *reporting* mechanically is not an ADR; minting a **new denial class**
   is.

**No ADR was created, and none should be created merely because F8 is important.**

---

## 20. Validation as a Security Boundary

**`SECURITY_BOUNDARY: PRESERVED`.** Argument validation is **not** the security boundary. It is
**correctness / type-safety / model-hygiene**, security-adjacent because it is a *precondition* for the
policy layer's own string analysis.

Evidence, from source rather than preference:

| Fact | Where |
|---|---|
| the source calls it **"defense-in-depth"** | `stateless.py:1923` |
| an unknown tool passes it, explicitly deferring to security | `stateless.py:2251-2252` |
| it can be disabled wholesale (`thin_tools=True`) with policy and approval still applying | `stateless.py:1867-1870`; §13 |
| the actual authorization decision is `SecurityPolicy.check` | `approval_gate.py:88` |
| a hard `DENY` never invokes the approval handler | `approval_gate.py:89-95` |

What validation *does* protect, per §6 of the brief: **missing required properties** and **wrong primitive
types** (both reproduced as rejected); it does **not** protect against **unexpected properties** for 41 of
42 tools, because `additionalProperties` is unset.

**STOP 3 does not fire**, so no security-equivalence proof is required before considering a fallback. The
independent reason not to substitute one remains: equivalence is unproven (§11).

---

## 21. Risks

| # | Risk | Assessment |
|---|---|---|
| 1 | **The laundering is the real hazard, not the missing package.** A dependency gap is reported as a content verdict, and the model is told that verdict is final. In a *supported* install this path is unreachable — but it will silently mislead anyone whose install is incomplete, and it did exactly that for the whole POST-M13 chain. | **High consequence, low likelihood after repair.** Report as its own defect. |
| 2 | **Repairing F8 will surface the nullable-argument class.** No schema admits `null`; models emit `null` for unset optionals; the refusal will be *correct* and *hostile*. | **Medium.** A schema-policy question, deliberately not touched. |
| 3 | **`thin_tools=True` silently disables engine validation** (§13 finding 3a). | **Medium.** Latent, not currently enabled. |
| 4 | **A second validator already exists** and would be the obvious thing to reach for. Substituting it would create a second validation authority for the same question — the defect class this migration exists to remove — while its equivalence stays unproven. | **Medium.** Mitigated by this report; §11 gives the reason. |
| 5 | **The environment's TLS gap looks like a project limitation.** *"No network, so pip cannot fix it"* is true of the interpreter but not of the machine, and it made a one-variable fix look permanent. | **Low.** Documented in §8 with the evidence. |

---

## 22. Non-Goals

Not done, and not authorised here: installing `jsonschema`; editing `pyproject.toml` or `uv.lock`; adding
any dependency; creating a fallback validator; modifying `ToolExecutor`, tool schemas, approval ordering or
authorization; modifying `VerificationFloorGuard`, P3, goal-state, M13, stagnation, recovery, the graph or
fanout; changing F8 behaviour; enabling `stagnation_gate`, `goal_state` or `recovery_ladder`.

Also not done: repairing the `thin_tools` reachability gap, the unknown-tool delegation, the ACP path, the
nullable-argument contract, or the error-classification defect. **All four are recorded, none is touched.**

---

## 23. Evidence

### Exact metrics

```
production_files_changed:                    0
production_lines_changed:                    0
config_defaults_changed:                     0
dependencies_installed:                      0
lockfiles_changed:                           0
valid_tool_calls_reproduced_as_refused:      2
invalid_tool_calls_reproduced:               3
tool_execution_paths_traced:                 7
ToolExecutor_bypasses_found:                 3
existing_fallbacks_found:                    3
ADR_required:                                NO
```

### Artefacts (all read-only, outside `wisp/`)

| Path | Purpose |
|---|---|
| `.workbuddy-ai/memory/post-m13-f8-recon/reproduce_f8.py` | the F8 reproduction and the valid/invalid discriminator |
| `.workbuddy-ai/memory/post-m13-f8-recon/f8_to_p3_impact.py` | every arrow of the F8→P3 chain, with controls |
| `.workbuddy-ai/memory/post-m13-f8-recon/fallback_analysis.py` | keyword coverage and the nine input classes |

### Files written by this phase

`PHASE_POST_M13_F8_TOOL_VALIDATION_AUTHORITY_RECON.md` (this report), one ledger change-log row, the daily
log, and the three scripts above. **No production file, no test, no config default, no lockfile was
modified**, and no package was installed.

---

## 24. Exit Criterion

| # | Required | Verdict |
|---|---|---|
| 1 | exact F8 failure mechanism | **§5** — a `ModuleNotFoundError` caught by the handler that returns a schema verdict |
| 2 | exact source location(s) | **§5** — `stateless.py:2285-2298`, reached from `:511` and `:616` |
| 3 | exact authority responsible | **§6** — `WispAgentCore._validate_tool_args` |
| 4 | exact reason valid calls are refused | **§4** — the validator never runs; `jsonschema` is absent |
| 5 | whether `jsonschema` is required / optional / mis-imported / mis-packaged / intentionally absent | **§3, §8** — **required** (declared + locked), **correctly packaged**, **not optional**, **not intentionally absent**; the *import* is structurally at fault as a second defect |
| 6 | whether a valid-call fallback already exists | **§10** — a dependency-free validator exists, but for a *different authority*; not wired to tool args |
| 7 | whether a fallback would preserve the schema contract | **§11** — `FALLBACK_EQUIVALENCE: NOT PROVEN`; feature-complete for today's 45 schemas, not equivalent in general |
| 8 | classification of the refusal | **§4, §8** — **VALID-CALL INFRASTRUCTURE REFUSAL**, root cause *dependency/environment*, aggravated by an *implementation* error-class conflation |
| 9 | impact on ToolExecutor / authorize / approval / verification / `wrote_code` / P3 / goal state | **§15, §16** — all pre-dispatch; `wrote_code` stays False; P3 is `INCONCLUSIVE` by construction |
| 10 | minimal repair boundary | **§18** — provisioning; the code boundary is `_validate_tool_args` |
| 11 | whether repair requires an ADR | **§19** — **NO** |
| 12 | required tests for a future implementation phase | **§17** Option A/B rows |

---

## 25. Final Status

```
=== F8 FORENSIC VERDICT ===

F8_REPRODUCED: YES

VALID_CALL_REFUSED:
YES — byte-identical refusal text for a valid call and an invalid one

ROOT_CAUSE:
DEPENDENCY/ENVIRONMENT FAILURE (a declared + locked runtime dependency absent from the venv),
aggravated by an IMPLEMENTATION defect: `import jsonschema` sits inside the `except Exception`
that returns a schema-validation verdict (stateless.py:2285-2298), so ModuleNotFoundError is
reported as "Schema validation failed"

VALIDATION_AUTHORITY:
WispAgentCore._validate_tool_args (wisp/core/stateless.py:2225) — engine-side, jsonschema-backed.
NOT universal: it no-ops for a tool absent from TOOL_SCHEMAS, and entirely under thin_tools=True.

SECURITY_BOUNDARY:
PRESERVED — validation is correctness/type-safety defense-in-depth, NOT the security boundary.
The boundary is SecurityPolicy.check (authorization) + ApprovalGate (approval).

TOOLEXECUTOR_PRECONDITION:
Schema validation is NOT a universal precondition. Three findings: (a) thin_tools=True makes the
engine lookup a no-op for every visible tool; (b) an unknown tool name passes by explicit design;
(c) the ACP path (acp_session.py:237) reaches ToolExecutor with no engine validation. ToolExecutor
itself contains no schema validation.

APPROVAL_ORDER:
validation -> authorization -> approval -> extensions -> execution.
Validation precedes approval (the 13-J1 repair, stateless.py:502-504). Inside the gate,
authorization precedes approval (approval_gate.py:88-111).

JSONSCHEMA_STATUS:
DECLARED + LOCKED + ABSENT (not installed, not importable; not optional, not dev-only,
not transitive — a direct runtime dependency)

EXISTING_FALLBACK:
YES — wisp/multi_agent/schema_validator.py, a stdlib-only JSON Schema subset validator,
serving SUBAGENT OUTPUT contracts. Two further mechanisms exist (pydantic in primitives.py;
hand-written checks in web.py). None is wired to tool-argument validation.

FALLBACK_EQUIVALENCE:
NOT PROVEN — and not provable here (jsonschema is absent, so no differential run is possible).
Proven narrower: feature-complete for all 45 current schemas (no unimplemented keyword is used)
and agrees with the declared schema on all nine input classes. Not recommended for substitution.

P3_IMPACT:
F8 is the SOLE cause, verified arrow by arrow. No mutation -> wrote_code False -> rejection()
None -> the floor criterion is vacuously satisfied but emits no evidence -> a required criterion
with no evidence -> INCONCLUSIVE. Controls: mutated+unverified -> FAIL; mutated+verified -> PASS.
Sharper: this environment can ONLY produce INCONCLUSIVE, so ADR-0016's rate would be a
degenerate 100% that characterises the environment, not the gate.

ADR_REQUIRED:
NO — the intended contract is explicit in pyproject.toml, uv.lock and the code; the repair
restores existing behaviour and changes no authority, boundary, policy or ordering.
(An ADR WOULD be required to promote the fallback, or to mint a new denial class for
"validator unavailable". Neither is this repair.)

MINIMAL_REPAIR_BOUNDARY:
wisp/core/stateless.py::WispAgentCore._validate_tool_args (stateless.py:2225) — the single
existing authority boundary. The primary repair is PROVISIONING, not code: the dependency is
already declared and locked. Secondary, same function: stop reporting an absent validator as an
invalid argument.

IMPLEMENTATION_AUTHORIZED:
NO

PRODUCTION_CHANGES:
0

NEXT:
A separate, authorised implementation/provisioning phase. Feasible in this environment without
network: the uv cache already holds jsonschema 4.26.0-py3-none-any plus rpds-py cp312/arm64,
matching uv.lock exactly; alternatively SSL_CERT_FILE pointed at the already-installed certifi
bundle restores pip's TLS. Then re-run the ~11 F8-blocked test files and the P3 measurement that
ADR-0035 §9 defers to.
========================
```

```
=== STATUS: COMPLETE ===
PHASE: POST-M13-F8-TOOL-VALIDATION-AUTHORITY-RECON
MODE: FORENSIC-READ-ONLY
PRODUCTION_CHANGES: 0
DEPENDENCIES_INSTALLED: 0
LOCKFILES_CHANGED: 0
CONFIG_DEFAULT_CHANGED: 0

F8_REPRODUCED: YES
VALID_CALL_REFUSAL: PROVEN
VALIDATION_AUTHORITY: WispAgentCore._validate_tool_args (engine-side, jsonschema-backed, not universal)
SECURITY_BOUNDARY: PRESERVED
TOOLEXECUTOR_BOUNDARY: schema validation is not a universal precondition (3 findings, none repaired)
P3_IMPACT: INCONCLUSIVE by construction; F8 is the sole cause (verified with controls)
ADR_REQUIRED: NO

REPAIR_CLASS:
MIXED — ENVIRONMENT (a declared, locked runtime dependency absent from the venv; plus an
interpreter with no CA path) + IMPLEMENTATION (the error-class conflation that made a
missing package indistinguishable from a model error)

IMPLEMENTATION_AUTHORIZED: NO

REPORT:
PHASE_POST_M13_F8_TOOL_VALIDATION_AUTHORITY_RECON.md

NEXT:
An authorised provisioning phase — install the declared dependency (offline from the uv cache,
or with SSL_CERT_FILE set), then re-measure. No code change is required for the primary repair.

EXIT:
COMPLETE
========================
```
