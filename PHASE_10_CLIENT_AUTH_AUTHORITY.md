# PHASE 10 — Client Auth Authority (R10, functional half)

**Date:** 2026-09-21
**Scope:** `wisp-desktop/` — the Electron client. The server-side work is in
`PHASE_10_AUTHORIZATION_PARITY.md`.
**Status:** defect **FIXED**, duplication **MEASURED + RATCHETED**, canonicalization
**OPEN** (a decision).

---

## 1. What was asked

`CONTEXT.md` §12 listed **R10** as *"Desktop client `tsc -b` is red (32 pre-existing
errors) — needs a decision. One is functional: `useApi.ts:368` sends no
`Authorization` header."*

The pre-existing errors are a decision. The functional defect is not — it is a bug
with one correct fix. It is fixed here.

## 2. The defect, verified

`useApi` exposes ~40 methods. All of them except one go through `apiFetch`, which
builds the `Authorization` header inline:

```ts
init.headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {};   // apiFetch
```

The exception is `getCheckpointDiff`. It cannot use `apiFetch`, because `apiFetch`
returns `resp.json()` and a diff is plain text. So it calls `fetch` directly — and
it built its headers from a *second* helper, called with no argument:

```ts
const resp = await fetch(url, { headers: makeAuthHeaders() });   // ← no argument
```

`makeAuthHeaders(apiKey)` returns `{}` for a falsy key. Given no argument at all,
`apiKey` is `undefined`, so the function returns `{}` — the request goes out
**unauthenticated**. There is no fallback: `authParams` is the empty string by
design (the key is never sent as a query param, where it would leak into logs).

| Path | Header sent |
|---|---|
| every other `useApi` method (via `apiFetch`) | `Authorization: Bearer <key>` |
| `getCheckpointDiff` (raw `fetch`) | **none** |

## 3. The duplication — measured, not estimated

The defect is one instance of a much larger pattern. An AST scan of `wisp-desktop/src`
(the `Authorization` header is constructed in 19 files / 28 sites; comments are not
AST nodes, so prose does not register):

| Scope | Files | Sites |
|---|---|---|
| `src/main/backend.ts` | 1 | 1 |
| `src/renderer/**` | 18 | 27 |
| — of which canonical (`useApi.ts`) | 1 | 1 |
| **— of which re-implementations** | **17** | **26** |

Twenty-six independent re-implementations of a one-line concept, across seventeen
files. Each is written slightly differently — `: undefined`, `: {}`, or a spread
into an existing object — which is exactly why the divergence was invisible.

**This is why nothing caught the bug.** The canonical builder is *one of* the 27
renderer sites, so the question *"does this module build the header?"* answers
**yes** for every module — including the broken one. The defect was not an
absence; it was a *duplicate that had drifted*.

`src/main/backend.ts` is a genuine exception: it is the Electron main process, a
separate bundle, and cannot import the renderer helper. Its single site is pinned
as-is rather than counted as debt.

## 4. The compiler was already reporting it

`tsc -b` builds **two** projects (`tsconfig.node.json`, `tsconfig.web.json`).
Measured on the renderer project:

| State | Errors | Detail |
|---|---|---|
| defect present | **33** | includes `useApi.ts(380,48): error TS2554: Expected 1 arguments, but got 0` |
| defect fixed | **32** | no `TS2554` anywhere |

The bug was **statically visible in the repo's own build the whole time**. It was
missed because the `typecheck` npm script runs `tsc --noEmit`, and the root
`tsconfig.json` is project-references with `"files": []` — so `tsc --noEmit`
typechecks *nothing*. (This is the same finding as D4 in `CONTEXT.md` §4; it now
has a second consequence.)

### 4.1 The 32-vs-38 discrepancy, resolved

`CONTEXT.md` recorded *"32 pre-existing errors across 12 files"*. `tsc -b` reports
**38 across 12 files**. Both numbers are right; they measure different things:

| Command | Errors | Files | Contents |
|---|---|---|---|
| `tsc -b tsconfig.web.json` | **32** | 11 | renderer only |
| `tsc -b` (both projects) | **38** | 12 | renderer + `src/main/menu.ts` (6) |

The recorded 32 was a **renderer-only** count. The "12 files" spanned both
projects. No unexplained drift.

## 5. The fix

One authority, used by both paths.

| File | Change |
|---|---|
| `useApi.ts` | `apiFetch` builds headers via `makeAuthHeaders(apiKey)` — the inline duplicate is gone |
| `useApi.ts` | `getCheckpointDiff` passes `apiKey`; dependency array corrected (`makeAuthHeaders` is module-scope and stable, `apiKey` was missing) |
| `useApi.ts` | `getCheckpointDiff` reports failures through `describeApiError` instead of a bare `API ${status}` — the same Target C treatment the other paths already had |
| `useApi.ts` | `makeAuthHeaders` carries the doc comment that names it the single authority |

Behaviour is unchanged when no key is configured; when a key is configured, the
checkpoint-diff request is now authenticated like every other request.

## 6. The guards

Two new test files, **9 tests**, both green.

| File | Tests | Pins |
|---|---|---|
| `src/renderer/hooks/useApi.test.ts` | 5 | the header is sent on the raw-`fetch` path *and* both `apiFetch` paths; and is **not** sent when no key is set |
| `src/renderer/hooks/authHeaderAuthority.test.ts` | 4 | there is exactly **one** header builder; the main-process site is pinned; the 17-file / 26-site duplication is **ratcheted**; no renderer module calls `/api/` without building a header |

The RED-first discipline was observed: `useApi.test.ts` was written before the fix
and failed with `expected undefined to be 'Bearer secret-key-123'` — the exact
symptom. The four companion assertions passed *before* the fix, which is what makes
the guard non-vacuous: the same test that catches the missing header also proves
the header is produced when a key is present.

The ratchet follows the `test_authorization_parity.py` precedent: it fails when a
**new** file starts building the header, when a recorded one **changes count**, and
when a recorded one **disappears** — so the list cannot quietly decay the way the
2026-08-24 audit's inventory did (`docs/audit-2026-08-24.md:270`).

## 7. A test-infrastructure fragility, found while verifying

`npx vitest run` intermittently fails with:

```
[vitest-pool-runner]: Timeout waiting for worker to respond
```

This is **not** a test failure — no test runs. Cause, measured:

| Fact | Value |
|---|---|
| vitest's worker-start timeout | `START_TIMEOUT = 6e4` — a **hardcoded** constant in `vitest/dist`, not configurable |
| jsdom environment setup, reported by vitest | **50.9 s / 52.9 s** |
| machine load average during measurement | **4.25 – 5.07** (3 days uptime) |

Worker startup sits at ~51 s against a hard 60 s limit. Under load it crosses, and
the run dies before a single test executes. The same run succeeded alone and failed
under any concurrency.

**Consequence:** the desktop suite is flaky *as a suite* on a loaded machine. This
is pre-existing and independent of this change — but it means "run the desktop
tests" is not a reliable gate here, and it should not be trusted as one until the
jsdom setup cost or the timeout is addressed.

Workaround used: run a single file at a time. Both guard files were verified green
that way.

## 8. Verification

| Check | Result |
|---|---|
| `useApi.test.ts` | **5 passed** (RED before the fix, GREEN after) |
| `authHeaderAuthority.test.ts` | **4 passed** |
| `tsc -b tsconfig.web.json` | **32** errors — the pre-existing renderer set, no `TS2554` |
| `tsc -b` (both projects) | **38** errors = 32 renderer + 6 `src/main/menu.ts` |
| New test files typecheck | clean — neither appears in the error list |

## 9. What is NOT done (decisions, not patches)

| # | Item | Why it is a decision |
|---|---|---|
| 1 | **Canonicalize the 26 re-implementations** into `makeAuthHeaders` | A 17-file refactor of the shipped client. It would delete the ratchet list entirely. Mechanical, but it touches the client's request layer broadly — and the *architectural* fix may be to export the helper from a shared module rather than thread it through every component's `useApi` |
| 2 | **The 32 pre-existing renderer errors** | 7 of the 32 are in `ErrorBoundary.test.tsx` (a test file being typechecked by the *build* config — arguably a config bug). `useApi.ts` still contributes 3, at lines 166/233, unrelated to auth |
| 3 | **Fix the `typecheck` script** | `tsc --noEmit` against a project-references root with `"files": []` checks nothing. Changing it to `tsc -b` makes the repo's own gate honest — and turns 32 pre-existing errors into a red gate, which is a policy call |
| 4 | **The vitest jsdom startup cost** (§7) | 51 s against a hardcoded 60 s limit. Options: split the AST/scan tests into a node-environment project (blocked today because the global `src/test/setup.ts` assumes `window`), or trim what `setup.ts` imports |

Item 3 is the one that would have caught this defect at commit time.

## 10. Files touched

| File | Change |
|---|---|
| `wisp-desktop/src/renderer/hooks/useApi.ts` | the fix (§5) |
| `wisp-desktop/src/renderer/hooks/useApi.test.ts` | **new** — 5 tests |
| `wisp-desktop/src/renderer/hooks/authHeaderAuthority.test.ts` | **new** — 4 tests |
