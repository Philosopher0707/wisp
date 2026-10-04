---
name: wisp-codemap
description: Navigate the wisp coding agent's 1000-file tree without reading it — which module owns a decision, which layer a symbol lives in, and where the authority for a behaviour actually is. Use when working in this repo and asking "where does X live", "who decides Y", "which file do I edit to change Z", "what calls this", or when a change touches wisp/core, wisp/transport, wisp/infra, wisp/tools, wisp/server, wisp/multi_agent, or wisp/graph. Also use before proposing an architectural change in wisp, and when docs and observed behaviour disagree.
---

# wisp codemap

wisp is ~1000 Python files. Reading it is not the way in; a map is. This
skill gives you the layer taxonomy, the derived file index, and the rules
for deciding which module owns what — so a change lands in the one place
that has authority over it.

## The rule that matters most

**Every decision in wisp has exactly one owning module.** Before you edit
anything to change a behaviour, find the owner. If you cannot name the
owning module, you do not yet understand the change.

Most failed changes to an agent runtime share one cause: the behaviour
was patched at a *consumer* instead of at the *owner*, so two modules now
disagree and the disagreement is invisible until it diverges at runtime.

| You want to change | Edit here | Not here |
|---|---|---|
| Turn lifecycle, convergence, stop criteria | `wisp/core/` | the transport or CLI |
| How output is rendered/framed | `wisp/transport/renderer.py` | individual call sites |
| Whether a tool call is permitted | the gate owner (see `BOUNDARIES.md`) | the tool implementation |
| Tool registry contents | `wisp/tools/` | callers that filter |
| Policy / audit / telemetry | `wisp/infra/` | `wisp/core` |
| Orchestration of many agents | `wisp/multi_agent/` | ad-hoc `asyncio.gather` |
| HTTP surface | `wisp/server/routes/` | ad-hoc handlers |

## Read these first, in this order

1. `AGENTS.md` — the contributor contract. Claims here are *normative*.
2. `BOUNDARIES.md` and `TRUST_BOUNDARY_MAP.md` — who may decide what.
3. `CLAUDE.md` / `CURRENT_AUTHORITIES.md` — current pointers.
4. `INDEX.md` (next to this file) — the derived per-layer file list.

Two cautions that are not obvious:

- **`CURRENT_AUTHORITIES.md` is generated.** It is derived by
  `scripts/derive_current_authorities.py`. Never hand-edit it; change the
  source and re-run the derivation, exactly as with `INDEX.md` below.
- **`PHASE_*.md` at the repo root are forensics, not contracts.** There are
  well over a hundred of them. They record what a given investigation
  concluded. They are evidence about the past, not authority over the
  present — when one contradicts live code or a ratified decision, live
  code wins.

## Verify the map, do not trust it

This skill's index is derived from git, but derived-once is still stale the
moment you add a file. Before you rely on a path:

```bash
# is the index still current?
python .agents/skills/wisp-codemap/scripts/build_index.py --check

# regenerate when you have added or moved files
python .agents/skills/wisp-codemap/scripts/build_index.py
```

`--check` exits 0 when current and 1 when stale. Run it after any
structural change, and treat a stale index as a reason to regenerate — not
as evidence that a file moved.

## Finding the owner of a behaviour

Work from the symptom to the authority, not from the file you happen to
know:

1. **Name the decision.** Not "the spinner looks wrong" but "which module
   chooses the spinner frame".
2. **Grep for the decision, not the symptom.** Search the value or branch,
   not the rendered string.
3. **Find every producer and every consumer.** The owner is the module that
   *produces* the decision; consumers read it. Count the consumers — a
   behaviour reimplemented in several places has no owner yet.
4. **Check the gate, if one can veto.** Permission, policy, and approval
   layers are separate from the thing they gate. A change that makes a
   tool "work" by bypassing its gate is not a fix.
5. **Check whether anything calls the mechanism.** A gate that is correct,
   tested, and never invoked is a documented intention, not a live control.

## The output-mode contract

Rendered output honours four modes, all defined in **`wisp/terminal_width.py`**:
`OutputMode.UNICODE`, `ASCII`, `ACCESSIBLE`, `MINIMAL` — selected by the
`WISP_OUTPUT_MODE` / `WISP_ACCESSIBLE` environment variables, with
`BoxChars` supplying the per-mode glyphs and `display_width()` doing the
width maths so alignment survives wide characters.

`wisp/transport/renderer.py` is the canonical implementation of the
contract. When you add rendered output, follow the pattern there rather
than embedding a literal `│` or `─` in your own string.

**Known coverage gap (measured this session):** only 10 of 393 files
under `wisp/` import `terminal_width`, so the contract is honoured in the
renderer and a handful of pickers but is *not* universal. Treat AGENTS.md's
"all rendered output honours the modes" as an aspiration for the majority
of the tree, not a fact about it. If you extend the contract's reach,
measure adoption again rather than assuming it grew.

## Verification

Prove the change through the real path, then run the suite:

```bash
python -m pytest tests/ -x -q
```

`tests/reliability/` holds the invariant tests — when you alter a gate, a
boundary, or a stop criterion, that directory is the one that should go
red first. If it does not, your change may not be reaching the layer you
think it is.

## What this skill does not do

It does not tell you how a subsystem works internally — for that, read the
module and trace its callers. It gives you the map and the ownership rules;
the code remains the authority on behaviour. If this map and the code
disagree, the code is right and this file needs a fix.
