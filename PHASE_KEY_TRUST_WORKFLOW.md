# PHASE_KEY_TRUST_WORKFLOW — the trust model is the operator's public key; absence is a configuration, invalidity is a refusal

**Baseline:** `HEAD` = `db29a11` (§0 records `08dd57e` — the handoff convention), tree = exactly the 29
WIP entries, ADR count **57**. **No drift.**
**Deliverable:** 1 of 2. **Type:** a decision. **ADR-0058** (see §2 — the brief said 0059).
**Report:** this file. Decision: `WISP_ARCHITECTURE_DECISIONS.md` **ADR-0058**. Guard:
`tests/reliability/test_key_trust_workflow.py` (12 tests).

---

## 1. The decision, as a sentence

> **The trust model is the operator-supplied organization public key.** `WISP_POLICY_PUBKEY` carries
> the organization's Ed25519 **public key, base64** (raw 32 bytes); `WISP_POLICY_BUNDLE` carries the
> **path** to a signed bundle (`bundle.json` plus its `.sig` sibling). The trust boundary is the
> **operator's own out-of-band channel** — the key is *public*, so its confidentiality is not required;
> its **integrity** is, and it reaches the host by the same ceremony that produced the bundle's
> signature. The mode is the spec's **`local-only`**.
>
> **Absence is a configuration; invalidity is a refusal.** An unset `WISP_POLICY_BUNDLE` means no
> organization policy is configured and the runtime is exactly today's. A bundle that is named but
> cannot be read, has no signature, or fails verification — **including a half-configuration** — means
> the *expected* control cannot be applied, and the runtime **refuses to boot**.
>
> **The key is shared, not per-device.**

**This is a decision, not a survey.** One candidate is chosen, three are rejected on their merits, and
the scope boundary is stated rather than hedged. The deployment-specific input the brief allowed this
ADR to stop on **was not needed**: the spec's own §3 (`local-only`) and §5 (storage) already describe a
single-operator deployment, and that is the deployment the wiring serves.

---

## 2. The brief's numbering is wrong — it is ADR-0058

The brief says *"One new ADR — **ADR-0059**"* throughout, and its index-row expectation is 0059. Driven:

```
$ grep -c "^## ADR-0058" WISP_ARCHITECTURE_DECISIONS.md   -> 0
$ grep -c "^| 0058 |"     WISP_ARCHITECTURE_DECISIONS.md   -> 0
$ grep -c "^## ADR-0"     WISP_ARCHITECTURE_DECISIONS.md   -> 57   (0001 … 0057)
```

**ADR-0058 is absent and 0058 is free.** The brief assumed the previous mission produced an ADR; it
explicitly did **not** — `PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md` §3 is titled *"The fix, and why it
is not an ADR"*, and its decision was that the rule was unchanged and the code was brought into
compliance. **The next free number is 0058**, and using 0059 would leave a permanent hole a reader would
hunt for. Recorded as **F90**; the ADR is **0058**.

---

## 3. What the code already decided — driven

The ADR's rules rest on measurements, not on a general notion of key trust. Each was driven:

| claim | measurement |
|---|---|
| `WISP_POLICY_PUBKEY` is a **path** | **FALSE.** It is **base64 key material**: `load_local(bundle_path, public_key_b64)` → `verify_bundle(bundle, sig, public_key_b64)` → `Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))` (`bundle.py:112-113`). `cli.py:8` documents it as *"base64 org public key"* |
| `WISP_POLICY_BUNDLE` is a path | **TRUE** — to `bundle.json`, with a `.sig` sibling from `_sig_path()` |
| verification needs a network or a chain | **FALSE** — `bundle.py`: *"Verification needs only the org public key — fully offline/air-gap compatible"* |
| the failure semantics must be invented | **FALSE — they are implemented.** bad/absent signature → `ValueError("policy bundle signature invalid")`; expired → `trim_expired`; no cached bundle → `FileNotFoundError`; a managed refresh failure → the cache is served |
| a registration surface exists | **FALSE.** `generate_keypair()` returns `(private_key, base64_public_key)` and **nothing registers it** |

**The brief's own worked example is wrong, and the error decides the workflow.** It says
`WISP_POLICY_PUBKEY` *"names a file path containing the operator's Ed25519 public key"*. Driven, it
carries the key itself. The workflow is therefore *"export this value"*, not *"place a file here"* —
and the ADR states the storage shape per setting rather than as one rule. Recorded as **F91**.

**The spec already fixed more than the deferral sentence suggests:**

| spec | what it says | what it fixes |
|---|---|---|
| §0 | *"local bundle files remain the authority; the API is a distribution convenience"* | the file is the source of truth |
| §3 | mode `local-only` — *"`load_local()` only; no socket use (test asserts no network via monkeypatched socket)"* | a fully offline mode exists and is tested |
| §5 | *"file perms 0600 + OS keychain for private keys **suffice for M4**"* | **the storage half is already decided** |
| §5 | deferred: *"Device registration + key distribution ceremony …; Postgres control plane; bundle encryption at rest"* | what is actually left open |

---

## 4. The four candidates

| candidate | disposition |
|---|---|
| **Operator-supplied pubkey** | **CHOSEN.** Feasible today: `load_local(path, pub_b64)` exists and is the spec's `local-only` mode. The key is public, so confidentiality is not the requirement; integrity is, and the operator supplies it out of band. Offline by construction. One key per deployment, which is what `verify_bundle`'s signature already implies |
| **Trust on first use** | **REJECTED.** TOFU authenticates a channel with **no prior authentication** — SSH host keys. Here the bundle is **already signed** and the point is to verify it against a key obtained out of band; TOFU would pin whichever key arrives **first on a path an attacker may be able to write**, verify the attacker's bundle against the attacker's own key, and report governance. It is also **stateful** (a new trust store to persist, migrate, revoke) for no gain, and it makes the first run's semantics differ from every later run |
| **A signed-key file with a built-in root** | **REJECTED.** Moves the root of trust to a key **baked into Wisp**, which needs its own distribution and revocation story — and the spec never specified one. Adds a **second signature layer** over a format §0 fixed as *"canonical JSON + detached Ed25519"*. And it does not remove the original problem: someone still has to *place* the root |
| **A registration ceremony** | **REJECTED AS OUT OF SCOPE, NOT AS WRONG — and this is the reading the ADR depends on.** It presupposes the control plane §5 deferred **in the same sentence**, and it answers a **multi-device** question. A single operator on a single host has nothing to register. Recorded in the ADR so a future reader cannot mistake the decision for the phrase |

---

## 5. The five questions, each answered as a rule

| brief's question | the rule |
|---|---|
| How does an operator establish trust in a key? | R1/R5/R7 — the operator exports the organization's **public** key; the trust boundary is the out-of-band channel; the private half is never Wisp's |
| Missing / expired / invalid key? | **R2/R3/R4**, and the split is the decision: **absence → inert** (today's behaviour); **invalidity → refuse to boot**; **expiry → narrow, never refuse** |
| Where is the key stored, how is it read? | R1 — `WISP_POLICY_PUBKEY` is the **key material**; `WISP_POLICY_BUNDLE` is a **path**. Both are read once, by the composition root (ADR-0006) |
| Shared or per-device? | R5 — **shared**; the phrase is read as the multi-device item |
| The offline story? | R6 — offline by construction; `WISP_POLICY_CACHE`/`load_managed` are **not** engaged |

**R1 refines §6 in the stricter direction, deliberately.** §6 says *"if both are set"*; the rule is
*"iff `WISP_POLICY_BUNDLE` is non-empty"*. The difference is the half-configuration: with §6's guard as
written, an operator who sets the path and forgets the key gets an **inert** runtime and no signal —
which is precisely the false-assurance failure mode `PHASE_10_M4_GOVERNANCE_UNWIRED.md` §4 names as
this finding's whole harm. With R1, that operator gets a startup failure. §6's condition remains
*sufficient*; it is not the guard.

**R8 reasons against ADR-0036 §5 rather than citing it.** ADR-0036 §5 chose fail-**open** for a broken
stagnation predicate, and that remains right: the predicate's absence is **benign** — the model cannot
exploit a measurement that is not taken. A policy bundle's absence is the opposite: an **expected**
control is **silently** not applied. Different cost of being wrong, different rule. R2 is the one place
absence is allowed, and it is allowed only when the operator never claimed otherwise.

---

## 6. Findings — all by running

**F88 — the M4 policy suite is red in this environment, and nothing in the corpus said so.**
`cryptography` is declared (`pyproject.toml:27`) and pinned (`uv.lock:472`, 50.0.1) but **absent**, so
`verify_bundle` returns `False` for everything — its `except Exception` swallows the failed import — and
`load_local` **always raises**. Measured, per file:

| file | result | cause |
|---|---|---|
| `tests/test_policy_bundle.py` | **4 failed, 3 passed** | `cryptography` absent |
| `tests/test_policy_precedence.py` | 8 passed | — (pure merge logic, no crypto) |
| `tests/test_policy_modes.py` | **10 failed** | `cryptography` absent |
| `tests/test_policy_cli.py` | **2 passed, 5 errors** | `cryptography` absent |
| `tests/test_policy_routes.py` | **1 error** | `httpx` absent (F80's class) |

**14 failed, 6 errors, 13 passed — and none of the five files is in the canonical block** (`grep` for
`test_policy_modes|test_policy_bundle|test_policy_precedence` in `AGENTS.md` and `CONTEXT.md`: **0**).
So the layer this mission wires has a test suite that cannot run here, and the corpus has never
recorded it. Same class as F80; **not repaired** (it needs the dependency, not a code change).

**F89 — a bundle that omits `expires_at` crashes the loader with an unrelated message.**
`merge_layers` computes `expires_at=min(h for h in (higher.expires_at, lower.expires_at) if h)`. With
both falsy the generator is empty and `min()` raises:

```
PolicyBundle(org_id='a')                      -> ValueError: min() iterable argument is empty
PolicyBundle(org_id='a', expires_at=1000.0)   -> OK  expires_at=1000.0
```

`expires_at = 0.0` is the **dataclass default**, so a bundle that omits the field — malformed per §1's
format, but a plausible operator error — fails with a message that names neither the bundle nor the
cause. It fails **closed**, which is the right direction, so this is a message defect and not a
security one. **Named, not fixed:** `wisp/policy/` is a non-violation of ADR-0058, and the fix is not
this deliverable's subject. R4 carries the measured precondition.

**F90 — the brief's ADR number is wrong** (§2): 0059 is not the next free number; 0058 is.

**F91 — the brief's worked example for `WISP_POLICY_PUBKEY` is wrong** (§3): it is key material, not a
path. **The eighth consecutive brief with a wrong specific claim.**

### The non-vacuity probe found two defects in *this* guard

Not in the code — in the instrument, which is the class the corpus cares about. Both were **MISSED** on
the first run and both were fixed:

| probe | what it exposed | fix |
|---|---|---|
| **NV1** | `pytest.raises(ValueError)` on an unverifiable bundle **does not falsify its claim**: with the signature check disabled, the loader reaches `_bundle_to_effective` and raises `ValueError("min() iterable argument is empty")` from **F89** — an unrelated defect satisfying the type. The test passed while the property was broken | pin the **message**: `pytest.raises(ValueError, match="signature invalid")` |
| **NV7** | searching the whole 6 000-line ADR file for `**R4 —` is **non-falsifying**: a dozen other ADRs contain that string, so renaming ADR-0058's R4 was still found elsewhere | scope the search to **ADR-0058's own section**, with a length floor |

A third probe (NV8) had an anchor that matched zero times and was reported as **SKIPPED** rather than
silently counted — the probe's own floor doing its job.

### And the landing exposed a third: a tripwire that fired on a legitimate addition

`test_the_loader_entry_points_have_no_runtime_caller` pins an exact **set** of callers
(`{wisp/policy/cli.py, tests/test_policy_modes.py}`). D1's guard drives `load_local`, so the set grew and
the tripwire fired — **for the wrong reason**: a *test* file calling the loader is not *"the layer may be
wired"*, and the tripwire's own docstring already says *"reachable only from the CLI and **tests**"*.
This is the class the brief names: *a guard that pins a state rather than a property is a nuisance; write
it so it fails on a real violation, not on the next legitimate addition.*

**Repaired, not weakened** — the property is now stated as a rule and probed in both directions:

```
a runtime CALL in wisp/composition.py   -> CAUGHT   (1 failed)
a new TEST caller                      -> MISSED   (1 passed — correct)
```

`wisp/composition.py` restored byte-identical. The tripwire keeps its floor (`the CLI must still be a
caller`) and still fires when Deliverable 2 wires the composition root.

---

## 7. Non-violations, asserted

`tests/reliability/test_key_trust_workflow.py`, 12 tests:

1. **`authorize()` unchanged** — parameter list, `AuthorizationDecision`'s fields, and L0 driven both
   ways (inert with no bundle; `controlling_layer == "organization"` with a deny bundle).
2. **`wisp/policy/` unchanged** — the package's `__all__` by content (with a non-empty floor) and the
   signatures of `load_local`, `load_managed`, `merge_all`.
3. **`ToolExecutor.__init__`'s `policy` still defaults to `None`**, and the executor still hands
   `effective_policy=self.policy` to `authorize()`. The tripwire is **not** inverted here; Deliverable 2
   does that.

Plus the premises (§3) and R3/R4/R6/R7, each mechanically.

---

## 8. Non-vacuity

**9/9 CAUGHT, 7 files restored byte-identical (sha256).**
`.workbuddy-ai/memory/post-m13-gate-enablement/key_trust_nonvacuity.py`

| probe | what it breaks | result |
|---|---|---|
| NV1 | an unverifiable bundle stops raising (R3) | **CAUGHT** |
| NV2 | expiry stops narrowing (R4) | **CAUGHT** |
| NV3 | `authorize()` gains a parameter | **CAUGHT** |
| NV4 | `verify_bundle`'s key parameter is renamed (the premise) | **CAUGHT** |
| NV5 | the package stops exporting `trim_expired` | **CAUGHT** |
| NV6 | the executor's `policy` stops defaulting to `None` | **CAUGHT** |
| NV7 | the ADR loses a rule id | **CAUGHT** |
| NV8 | a runtime surface accepts a private key (R7) | **CAUGHT** |
| NV9 | the local loader imports a network module (R6) | **CAUGHT** |

---

## 9. Residuals, open

1. **The multi-device ceremony** — deferred by the spec, read as such (R5's reversal condition).
2. **`WISP_POLICY_CACHE` / `load_managed` are not engaged** (R6).
3. **REST does not receive L0** — `SecurityPolicy.check()` has no organization layer, and L0 lives
   inside `authorize()`, which REST does not call for these actions (ADR-0055). Loading a bundle into
   `request_policy` would be **dead data**. Deliverable 2 states this.
4. **F88 — the M4 policy suite cannot run here** (`cryptography`).
5. **F89 — the falsy-`expires_at` crash**, named not fixed.
6. **The happy path of the key-trust workflow is now exercised — CLOSED 2026-09-27.** `cryptography` is
   installed: `test_policy_bundle.py::test_sign_verify_round_trip` drives a real Ed25519 keypair through
   `generate_keypair` → `sign_bundle` → `verify_bundle(...) is True`, with tamper and wrong-key rejected.

---

## 10. Verification

| check | result |
|---|---|
| the new guard | **12 passed** |
| non-vacuity | **9/9 CAUGHT**, 7 files restored byte-identical (sha256) |
| the M4 policy suite | **14 failed, 6 errors, 13 passed** — all from `cryptography`/`httpx` (F88) |
| the M4 wiring guard | 25 passed (unchanged — the tripwires are Deliverable 2's) |
| `ruff` on the changed files | clean |
| `mypy` | not re-run — stated as an argument, not a measurement |

**Rollback:** a documentation change plus one new test file. No production behaviour moves.
