---
name: architecture-evolution
description: Orchestrate a safe architectural change to an existing production codebase — forensic reconnaissance, authority mapping, contract and decision engineering, minimal implementation, tripwires, adversarial and replay verification, and an evidence-based phase report. Use when asked to "evolve the architecture", "make this the authoritative path", "wire up this mechanism", "implement the next phase", "change how completion is decided", "close an architectural gap", "who owns this decision", or when a mechanism exists and is tested but nothing calls it. Also use before any change that alters which component decides an outcome.
agent_created: true
---

# Evolve an architecture, evidence first

A structural change to a working system fails in one of two ways. The code is
wrong — loud, caught by tests. Or the **record** is wrong: the plan claims a
mechanism is wired, the ledger says a phase is complete, an ADR describes
behaviour the source does not have. The second is quieter, outlives the first,
and is what this skill exists to prevent.

This skill orchestrates four stages. **Do the minimum stage that the evidence
requires — never all four by default.**

## The one rule that matters

> A mechanism is not live because it exists, is typed, is flagged, is documented,
> or is tested. **Find the production consumer.**

The dominant pathology in a long-lived codebase is *complete, tested,
unreachable*: a subsystem built to completion with nothing calling it. Before
building a fifth mechanism, check whether four already exist.

## Stage selection

Read the evidence, pick one stage, and say why.

| Evidence | Stage | Load |
|---|---|---|
| Nobody knows what the system currently does | **Recon** | `architecture-forensics` |
| Two mechanisms can decide the same thing and no rule says which wins | **Decision** | `architecture-decision-engineering` |
| The contract is ratified and the authority is unambiguous | **Implementation** | `reliability-phase-engineering` |
| Code landed; the evidence is incomplete or the failure set moved | **Verification** | `reliability-phase-engineering` |
| The phase is done | **Report + next seam** | the master (below) |

Two rules for stage selection:

- **Ambiguity is not a licence to implement.** If precedence, ownership, replay
  semantics or boundedness are undefined, the correct output of this session is
  a *decision artifact*, not a diff.
- **A ratified decision does not need re-litigating.** If an ADR already fixes
  the semantics, implement it. Reopening it is a separate, named decision.

## The loop

```
Establish baseline (snapshot outside the repo, stable failure set)
        |
Forensic recon  ->  what the system ACTUALLY does
        |
Authority mapping  ->  producer / consumer / authority / persistence
        |
   Ambiguous?  --yes-->  Decision engineering (ADR, precedence, conflicts)
        |                        |
        no <---------------------+
        |
Implement the minimal seam
        |
Tripwires (assert the boundary the change depends on)
        |
Adversarial + replay + concurrency verification
        |
Evidence report  ->  NEXT SEAM
```

## The three specialised skills

| Skill | Answers | Its output |
|---|---|---|
| `architecture-forensics` | *What actually happens, and who decides it?* | inventory, authority map, contradictions, seams |
| `architecture-decision-engineering` | *What is the rule, and what happens when inputs disagree?* | contract, ADR, precedence and conflict matrices, rejected alternatives |
| `reliability-phase-engineering` | *How do I land it without breaking an authority?* | minimal seam, tripwires, verification matrix, phase report |

Load only the ones the stage requires. Each is usable standalone.

## Skills this composes with

Two specialised procedures already exist and are **not** duplicated here. Delegate
to them rather than restating them:

- **`evidence-first-refactor-phase`** — landing one phase of a multi-phase
  migration on a large codebase where a plan exists, the tree is dirty, and the
  suite is already partly red. Use it for the mechanics of baseline capture,
  RED-first safety nets and stable-set regression comparison.
- **`canonicalize-duplicated-authority`** — finding and closing a concept that
  more than one module decides independently. Use it when recon's authority map
  shows two owners for one decision.

If a future change would require re-deriving what those skills already say,
extend them instead of writing a third copy. **Two producers of one procedure is
the same defect as two producers of one decision.**

## Shared resources

Templates and checklists are **single-sourced** in the `architecture-evolution`
skill directory (the directory holding this file), so no template can drift from
another copy:

| Resource | Path within this skill |
|---|---|
| Seven templates | `assets/templates/` |
| Six checklists | `references/checklists/` |
| Worked case study | `references/wisp-case-study.md` |
| Integrity checker | `scripts/validate_skill_system.py` |

Work each checklist at the stage it belongs to — they are gates, not reading:

| Checklist | Work it |
|---|---|
| `references/checklists/pre-implementation.md` | after the contract is ratified, before the first production edit |
| `references/checklists/authority-verification.md` | after implementing, and again before declaring the phase complete |
| `references/checklists/adversarial-testing.md` | when writing the tests that try to break the change |
| `references/checklists/replay-durability.md` | when the change creates or alters a durable decision |
| `references/checklists/concurrency.md` | when the change introduces state, a bound, or a callback |
| `references/checklists/phase-completion.md` | before writing "complete" anywhere |

Run `scripts/validate_skill_system.py` after editing any skill in this set: it
checks frontmatter, resolves every referenced path, catches project-specific
leakage into the generic rules, and fails on a resource no skill points at. It
defaults its root to the install it lives in, so it works from any copy.

**If this set is installed into more than one skill root** — which happens when
different agent tools each read only their own — the copies must stay identical.
That is two *delivery* targets, not two authorities, but nothing forces them to
agree, so assert it rather than trusting it:

```bash
python3 <this skill>/scripts/validate_skill_system.py --mirror <other skill root>
```

Drift between installs is the same defect class as two producers of one
structure: edit the source, re-mirror, then re-run with `--mirror`.

The three specialised skills reference these by the same relative paths. If the
skill is relocated, resolve them relative to this SKILL.md rather than assuming
an absolute prefix.

## Stop conditions

**Stop implementing and produce a decision artifact instead** when any of these
holds:

| Stop when | Because |
|---|---|
| Authority is ambiguous — two components could decide this | implementing picks a winner by accident |
| Precedence between inputs is undefined | the behaviour is then a race, not a rule |
| Replay or persistence semantics are undefined where a durable decision is required | restart produces a different answer than the live run |
| Two detectors/classifiers/guards would exist for one concept | the defect this whole skill exists to prevent |
| A new authority would compete with an existing one | same |
| The change needs a protected authority altered, and no decision authorises it | silently widening a boundary is how invariants die |
| Source materially contradicts the plan or the contract | the plan is a hypothesis, not evidence |
| Behaviour would be unbounded | retries, replans, recursion, fanout, polling — all need a bound |
| Model or user input would become authoritative over permissions, completion, routing, retry counts, escalation or policy | that authority must be granted explicitly, never inherited |
| A safety or authorization boundary would be bypassed | never negotiate this in an implementation phase |
| Global mutable state is being introduced | per-call, per-turn or per-run state is almost always available |

When stopping, say exactly what is unresolved, which decision would resolve it,
and what evidence would settle it. **Do not guess, and do not implement around
the problem.**

## Scope discipline

Separate two lists, always:

```text
REQUIRED to land this phase        ->  do it
INTERESTING but unrelated          ->  record, classify, defer
```

Discovered problems that do not block the phase get **recorded with a reason and
a tripwire**, not fixed. Opportunistic repair in a structural change makes the
change unreviewable and the regression unattributable. If a discovered problem
*does* block the phase, say so and re-scope explicitly rather than absorbing it
quietly.

## The questions to keep asking

Run these at every stage. They are the review an experienced engineer would give:

```text
What actually happens?            (not what the doc says)
Who owns this decision?
What happens if two mechanisms disagree?
What survives a restart?
What is bounded?
What happens when the feature is OFF?
Can the model or the user bypass the host's authority?
Can this create a second authority?
What evidence proves this?
Could this probe have returned its answer for the wrong reason?
What remains unknown?
What must NOT be changed?
```

## Deliverable

Every session ends with an evidence artifact, even a session that only
investigated:

- **Recon or decision stage** — an inventory or a decision record, stating what
  remains unresolved.
- **Implementation stage** — the phase report, the honest limits, the tripwires,
  and the regression set (never "the suite passes").
- **Always** — the next seam, or an explicit *no seam remains*.

Use `assets/templates/` for the shapes, and work
`references/checklists/phase-completion.md` before declaring anything complete.

**Writing the record is itself a source edit.** A decision record is not inert
documentation — it is an input to whatever reads it. Appending to an ADR log, a
changelog or a register can stale every derived artifact that parses it: a
generated index, a range claim in a header, a reproducibility guard. The guards
then go red before you have changed any behaviour, and it looks like the record
broke something. It did.

So before appending, find what derives from the file you are about to write, and
say which guard you expect to break and why. **A red guard you predicted is
evidence; a red guard you discovered is an incident.** Two sequencing rules
follow, and the repo usually states the first one outright:

- **Where a derived page records the commit it was generated at, it is
  regenerated *after* its input is committed, never before** — so the correct
  sequence deliberately leaves a guard red for exactly one step. Do not
  "fix" that by regenerating early; you will bake in the wrong commit.
- **Do not absorb a stale claim you merely passed.** If appending your record
  makes a *pre-existing* range claim staler, that claim was already wrong and is
  its own finding — record it and defer it, unless it is load-bearing for your
  phase. Opportunistic repair here is what makes a decision unreviewable.

**Regenerate a derived artifact when its INPUT moves — not when the repo does.**
A repository with derived pages, registers or generated indexes usually ships
generators you can run in one command, and the temptation is to run all of them
after any commit. Resist it. Most generators stamp the commit they ran at, so
running them all rewrites every page's banner and produces a diff of pure churn
that says nothing about what changed. Worse, it hides the one page that genuinely
moved inside four that did not.

Two rules, and they are different questions:

- **Which pages does this change actually invalidate?** Ask what each page *reads*.
  A page whose header is computed from a log is invalidated when the log grows. A
  page that cites the tree by `path:line` is invalidated by **any insertion above a
  citation** — including in files you never opened, which is why a source edit
  stales registers you were not editing. Run those two, and only those.
- **Regenerate after committing the input, never before**, when the page records
  the commit it was generated at. Doing it early bakes in the wrong commit. The
  correct sequence deliberately leaves a guard red for exactly one step; that red
  is the design, not a fault.

If you do run a generator that turns out to have nothing to say, **revert it** —
and say in the commit message that you did, and why. A reviewer who sees five
regenerated files cannot tell which one carries the change.

## Failure modes this skill exists to prevent

| Symptom | Cause |
|---|---|
| A subsystem "built" that nothing calls | reachability treated as an intention |
| A phase report that reads better than the code | limits not stated |
| Two components disagreeing about one input | no authority was ever named |
| A decision re-litigated every session | no decision record, or no reversal condition |
| Behaviour that changes across a restart | transient state mistaken for durable input |
| A retry loop that never ends | no bound was defined |
| The same deferral re-deferred forever | no tripwire, no owner |
| A change that "also fixed" five unrelated things | scope not separated |
| A probe that reports a clean result, where a clean result was impossible | the probe's own plumbing changed what it measured |

**The instrument is part of the system under test.** Two ways this bites, both
looking like data: a suppressed error stream (`2>/dev/null`) turns a *tool
failure* into an apparent "nothing there", and a command substitution inside the
measurement (`$([ -t 1 ] && …)`) redirects the very stream the test reads, so the
predicate can never be true. **A probe that cannot fail is not a probe.** Before
believing a result, ask what would have had to be true for the probe to produce
the *opposite* one — and if the answer is "nothing", you have learned nothing.
