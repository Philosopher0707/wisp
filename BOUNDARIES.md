# BOUNDARIES.md — the working boundaries for this repository

**What this file is.** The one place the *rules of engagement* are stated: what is in scope, what an agent
may decide alone, whose files are whose, and which files are load-bearing. It states **rules, not state** —
for current state read the derived registers (§6), which are regenerated from the tree.

Compiled 2026-09-30 from `CLAUDE.md`, `AGENTS_LEARNING.md`, the session handoff in
`docs/sessions/2026-09-29-network-agent/CONTEXT.md`, and the assistant's own `boundaries.md`. Where those
disagree, this page is the summary and the originals are the record.

---

## 1. Scope — simulated-first research

The **simulated lab is the boundary.** Real adapters (gNMI/Containerlab, Kafka, Batfish, OPA, SSO) stay out
of scope until a host with Docker and disk exists. This machine has ~2.7 GB free and no Docker daemon.

**How to apply:** do not plan or build real-network paths. Keep new work behind the existing interfaces.

## 2. What an agent decides alone, and what it must surface

| Decide and proceed | State the trade-off and ask |
|---|---|
| bug fixes, test repairs, refactors that do not change a published rule | widening a permission mode |
| new commits on top of a red branch (**no rewriting**) | changing a **pinned policy** or a published taxonomy |
| regenerating a derived register after committing its input | spending money on a paid provider |
| opening a PR | **merging** a PR |
| | editing your uncommitted files |

**Never, without being asked:** push your local-only commits; delete remote branches; drop a stash; print a
secret; let the network agent approve its own change.

## 3. GitHub autonomy

**Open PRs; you merge.** `gh pr create`, wait for green, report — never `gh pr merge`.

## 4. Your files — do not commit, do not delete

The list lives in `CONTEXT.md` §8, but **§8 is stale on two entries** (audited 2026-09-30) and cannot be
edited casually (§5), so the current list is here:

| Path | State today |
|---|---|
| `wisp/core/graph/__init__.py` | **modified** — a docstring. The only unstaged *tracked* change. |
| `CLAUDE.md` | **modified** — the session's "read these first" + standing-boundaries edit. |
| `wisp/multi_agent/_circuit_breaker.py` | **tracked and clean.** §8 says untracked — stale. |
| `wisp/capability_filter.py` | **tracked and clean.** §8 says untracked — stale. |
| `PHASE13_*` (6), `phase13_*` (6), `FANOUT_*` (1) | untracked — phase-13 work product and its metrics |
| `tests/reliability/test_13i1_*`, `test_13i2_*` (2) | untracked. **These two now FAIL**: they pin the pre-#56 surface (`len == 14` read-only names, `len == 42` tools) and PR #56 moved it to 20 and 52. |
| `tests/test_auto_delegate_defense.py`, `test_delegation_research_only.py`, `test_input_and_interrupts.py` | untracked, and **they abort collection** — they import `wisp.multi_agent.delegation` and `_has_unclosed_brackets`, neither of which exists. Run the suite with `--ignore=` for these three, or it stops at 3 errors. |
| `tests/test_subagent_enterprise.py` | untracked, collects |
| `_*.txt` (18 files, ~464 KB) | untracked — scratch |
| `AGENTS_LEARNING.md`, `workflow.md`, `docs/sessions/`, `.agents/skills/verified-change-workflow/` | untracked — the 2026-09-28/29 session's records |
| `.agents/`, `.aionrs/` | untracked agent workspaces; `.workbuddy-ai/` is gitignored |

**`register.md` and `scripts/derive_register.py` are now COMMITTED** (2026-09-30), with the guard R8
required (`tests/reliability/test_register_pins.py`). They were the fifth derived register and were
outside version control, which made a *tracked* test fail.

**Stashes are yours too.** `git stash list` holds unmerged work (19 files in `stash@{0}`). No cleanup here
drops one.

## 5. Load-bearing files — read before editing

| File | Why it is load-bearing |
|---|---|
| `CONTEXT.md` | ~253 KB, and the register generators pin **line numbers** into it — **35** distinct line references in `scripts/derive_current_open_items.py` and **4** in `derive_current_findings.py` (the other two generators pin none). The pins sit at **line 2459+**; §11's canonical block starts at **line 2284**, so **inserting a line anywhere above 2459 shifts them and breaks the derivation.** Editing it breaks the register tests. |
| `CURRENT_FINDINGS.md`, `CURRENT_OPEN_ITEMS.md`, `CURRENT_FLAGS.md`, `CURRENT_AUTHORITIES.md`, `register.md` | **DERIVED. Regenerate, never hand-edit.** Each has a committed generator and a reproducibility guard. The four `CURRENT_*` headers name the commit they were generated at; `register.md`'s banner does too. **A register must be reproducible from the COMMITTED tree** — `register.md` now derives from `git ls-files`, not the working directory, because a page derived from untracked files cannot be reproduced from a clone (and fails its guard in CI). |
| `scripts/derive_register.py` | The one generator that is **stdlib-only** — `python3 scripts/derive_register.py`, no venv and no `env -u PYTHONPATH`. It shells out to `git ls-files` to stay tree-independent. |
| `WISP_ARCHITECTURE_DECISIONS.md` | Append-only ADR log. A new ADR is a new section plus an index row; resolve any "row N" **by content**. |
| `tests/test_doc_drift.py` | Guards `AGENTS.md` / `ARCHITECTURE.md` / `CLAUDE.md` against deleted symbols and stale counts. |

## 6. Where the live state lives

**State is derived, not written.** Five registers, five generators:

```bash
env -u PYTHONPATH .venv/bin/python scripts/derive_current_findings.py     # CURRENT_FINDINGS.md
env -u PYTHONPATH .venv/bin/python scripts/derive_current_open_items.py   # CURRENT_OPEN_ITEMS.md
env -u PYTHONPATH .venv/bin/python scripts/derive_current_flags.py        # CURRENT_FLAGS.md
env -u PYTHONPATH .venv/bin/python scripts/derive_current_authorities.py  # CURRENT_AUTHORITIES.md
python3 scripts/derive_register.py                                        # register.md
```

**Regenerate AFTER committing the input**, not before: a `CURRENT_*` header records `HEAD`, so it always
names its own parent commit — the only commit a page can name. `register.md` records `HEAD` the same way.

**Running the suite in this checkout:** three untracked test files abort collection, so the full run needs

```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -m "not live and not network" \
  --ignore=tests/test_auto_delegate_defense.py \
  --ignore=tests/test_delegation_research_only.py \
  --ignore=tests/test_input_and_interrupts.py
```

CI does not need those ignores — the files are untracked, so CI never sees them.

## 7. The method

`.agents/skills/verified-change-workflow/SKILL.md` — measure, decide, apply; **RED first**; test through the
**production path**, not one gate; a **positive control** for every negative result; a **mutation probe** on
every load-bearing line; compare against a **known-good baseline**; open a PR for the human to merge.

Its one-line summary: **the failures worth preventing were all one shape — a claim tested at one layer while
the real path had several.**
