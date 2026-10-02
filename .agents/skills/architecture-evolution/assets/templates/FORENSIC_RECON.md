# FORENSIC RECON — <subject>

**Stage:** recon · **Date:** <date> · **Scope:** <what was investigated, and what was not>

## 1. Baseline

| Property | Value |
|---|---|
| Revision | `<hash>` — `<subject>` |
| Branch | `<branch>` |
| Working tree | `<clean / dirty: N tracked-modified, M untracked>` |
| Pre-existing work | `<files carrying uncommitted work — treated as IMMUTABLE>` |
| Stable failure set | `<path to the intersection of two runs, or "no suite">` |

Snapshot taken outside the repository at `<path>`. **No `git stash` / `reset` /
`checkout --` was used.**

## 2. Entry points traced

The live paths followed, from what actually runs:

```
<entry point>  ->  <module.function>  ->  <module.function>  ->  <outcome>
```

Record for each edge: what data crosses, what authority crosses (usually none),
what mutable state is touched, and whether it is an async/exception boundary.

## 3. Mechanisms inspected

| Mechanism | Where | Claimed role | Verified role | How verified |
|---|---|---|---|---|
| | `file:line` | | | `<command / test / read>` |

## 4. Interrogation results

One block per mechanism that matters. `UNKNOWN` is a valid and useful answer —
say so rather than inferring.

| Question | Answer | Evidence |
|---|---|---|
| Who produces it? | | |
| Who consumes it? (production, not tests) | | |
| Who acts on it? | | |
| Who persists it? | | |
| Can it be replayed? | | |
| Can another mechanism override it? | | |
| Who owns the decision? | | |

## 5. Contradictions found

> Report the contradiction. Do not silently reconcile it.

| # | Claim (and where) | Reality (and evidence) | Which is authoritative | Correction recorded |
|---|---|---|---|---|
| C1 | | | | |

## 6. Unconsumed or duplicated mechanisms

| Finding | Evidence | Consequence |
|---|---|---|
| `<mechanism has no production consumer>` | `<grep / AST / call-graph command>` | |
| `<two owners for one rung>` | | hand to `canonicalize-duplicated-authority` |

## 7. Candidate seams

| Seam | Existing boundary | Already carries | Would need | Owner |
|---|---|---|---|---|
| | | | | |

## 8. Verified by execution

Commands run and what they printed. Findings that were only read are marked
**unverified**.

```
$ <command>
<output>
```

## 9. Unresolved

| # | Question | What would settle it |
|---|---|---|
| Q1 | | |

## 10. Verdict

```text
AUTHORITY MODEL SUFFICIENT?   yes / no
IF NO: the unresolved decision is  <one sentence>
EVIDENCE THAT WOULD SETTLE IT     <one sentence>
```
