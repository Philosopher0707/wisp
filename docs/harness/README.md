# The harness: how wisp behaves by default, and how to check it

Verified 2026-10-05. Everything here can be re-checked with `wisp doctor` (behaviour) and `wisp tools` (what an agent is offered).

## Check the harness

```bash
wisp doctor [--json]          # 9 hermetic probes; exit 1 on any FAIL, warnings are advice
wisp /doctor harness          # same, through the one-shot slash path
/doctor harness               # inside the REPL
/doctor                       # the 7-check boot preflight (100 ms budget)
/doctor deep                  # a different, slower check: the Docker sandbox image
wisp tools --role researcher --mode auto_edit   # what an agent is offered, and why a tool is missing (read-only)
```

| probe | goes red when |
|---|---|
| `tool_profile` | a core tool has no implementation or schema (warns if the profile is `full`) |
| `subagent_surface` | `skill__*` tools reach subagents |
| `spend_accounting` | streamed text is not metered, or short deltas count as zero |
| `context_budget` | the system-prompt fit overshoots its budget or drops a section without naming it |
| `global_skills` | a global skill directory other than `~/.agents/skills` is loaded (warns above 30 skills) |
| `skill_capture` | a captured skill is not written or not loaded back |
| `audit_chain` | two writers fork the hash chain (the live log is read-only; a historical break is a warning, never repaired) |
| `repl_input` | a typed `v` or leading space is swallowed, or readline rewrites history prompt_toolkit owns |
| `fleet` | the manifest is invalid or a declared repo is gone (drift is a warning) |

Every probe has a mutation test that breaks the invariant in the code under test and expects red. The boot preflight
stays small on purpose: a probe that cannot finish in 100 ms shows up as a startup warning on every launch.

## The `wisp` command

`~/.local/bin/wisp` is a small wrapper: it runs a **dedicated** worktree, `~/dev/wisp-global` (the local branch `global-main`,
fast-forwarded from `origin/main`), with the shared virtualenv's Python (`PYTHONPATH` points at the worktree). Update it with
`git -C ~/dev/wisp-global pull --ff-only`. Nobody develops in that worktree and no other branch should be checked out in it: it
is what `wisp` runs, and a shared path was once taken over by another session, silently changing the global command. The
maintainer's own `~/dev/wisp` checkout is separate and is never used for merges. Start wisp from the **project** directory: the
workspace is whatever directory you launch from. A running REPL keeps the code it started with; relaunch to pick up an update.

## Tool surface

- The parent agent is offered 11 core tools: `read_file`, `write_file`, `edit_file`, `edit_file_multi`, `run_bash`,
  `run_tests`, `list_files`, `grep`, `glob`, `search_symbols`, `rewind`. `WISP_TOOL_PROFILE=full` offers every
  built-in. Hidden tools stay callable; explicit role tool lists and extension (MCP, skill) tools are untouched.
  Measured per provider call: parent 28,650 to 18,307 tokens; an unrestricted subagent 14,979 to 4,636.
- **Subagents in `auto_edit` never get** `run_bash`, `spawn`, `fanout` or the git/gh writes. They need a human's yes and
  a child has nobody to ask, so they are not advertised. The researcher role has `grep`, `glob`, `web_fetch` and
  `web_search` instead of a shell, deliberately: it reads untrusted web pages. In `full` mode a researcher still gets
  `run_bash`.

## Approval: no approver is not an approval

A caller with no human (a subagent, a background agent, `wisp bench`, ACP) that needs a gated tool is refused with
`NO_APPROVER` (ADR-0055 section 3). That is correct behaviour. Ways to authorise, narrowest first:

1. `WISP_UNATTENDED_AUTO_APPROVE_TOOLS=web_fetch,web_search`: the user names tools an approver-less caller
   may run. Only READ- and NETWORK-class tools count (a write, shell or MCP entry is ignored with a warning); it never
   applies with an approver present and never overrides a policy bundle's forced approval, `read_only` mode or the
   dangerous-command guard; each use is audited as layer `standing-grant`. Default empty.
2. `auto_approve` on one spawn, by the user.
3. `auto_approve` / `permission_mode=full` globally: approves writes and shell too. The user's decision, never the
   model's.

A policy bundle cannot widen anything (it can only deny or force approval).

## Behaviours worth knowing

- **Affected-test lookup after every write** is bounded: virtualenvs, dependency and build directories and `Library` are
  skipped, a workspace with more than 5,000 Python files (or 5 s of reading) is refused rather than truncated, `$HOME`
  itself is never analysed, and a "too large" verdict is remembered for 10 minutes. From `$HOME` this took 581 s before.
- **`/swarm`** reports complete, partial or failed. A total failure skips synthesis and states each distinct reason
  once, with the roles it hit. A failure that will hit every agent (billing 402, rejected key 401) stops queued agents
  from starting.
- **An unknown model name** is auto-corrected only to a real spelling of it (equal, prefix or substring). When
  look-alikes exist but none is a spelling, the configured model is served and the close names are listed; when nothing
  resembles it, the first listed model is used (so a stale name from another provider is not a 404).
- An open REPL session runs the code it started with; quit and relaunch to pick up a fix.

## Working rules that keep this honest

- Reproduce a reported failure exactly (a local HTTP stub is enough) before fixing it; keep the reproduction as the
  regression test, and mutation-check the fix.
- Tests must not read the developer's machine: run them with an empty `HOME`. CI is the full-suite arbiter.
- Before any merge, tag the pre-merge state (`checkpoint/<date>/...`); never delete a remote branch.
- Pin tables (`scripts/derive_*.py`) hold line numbers: after adding lines, re-anchor, regenerate, run the pin tests. A
  new exception class needs a row in `scripts/derive_register.py`.

## Where the shared pieces live (core)

Logic used by more than one surface lives in `wisp/core/`, once, and the layer test keeps core from importing the CLI:

- `core/workspace_walk.py`: one bounded, pruned, deterministic directory walk (file/time budget with a `stop` or `raise` policy) and
  `is_home_directory`. The import graph and `grep`/`glob` use it; the other walkers migrate one at a time.
- `core/approval_policy.py`: which tools need an approver, and the standing grant. `tool_executor` and the REST gate both use it.
- `core/tool_surface.py` (`wisp tools`): composes the real role, child-mode, profile and capability filters and says why a tool is
  absent. A differential test holds it equal to what the real pipeline sends a provider.
- `core/spend.py`: what a transcript cost, per provider call. The parent's telemetry (which charges the cost meter) and a child's
  budget both use it; the parent used to be charged only the size of the user's prompt.
- `core/recovery.py`: the failure markers and `describe_failure`; `multi_agent/verdict.py`: the run verdict shared by `/swarm` and `fanout`.

Known, documented behaviour: a generalist child is never offered extension (MCP, skill) tools, because the runner expands its
`"all"` into the built-in registry (`wisp tools --role generalist`).

## Merging

The generated pages (`register.md`, `CURRENT_FLAGS.md`, `CURRENT_AUTHORITIES.md`) stamp the commit they were generated at, and a
test requires the stamp to be an ancestor of `HEAD`. A **squash** merge replaces the branch commit and turns `main` CI red until the
pages are re-pinned (this happened on #85). Prefer merge commits, or re-pin right after a squash.

## Invariant gates

Five deterministic layers sit between the model and the machine: path confinement, command interception, secret scrubbing, a dependency
lock, and a completion verifier. They run once per tool call **before** the approval prompt and once per tool result, are pure functions
of their input (no clock, randomness, network or environment), and fail closed. Full design, the invariant table (every row names a witness
test, and a test checks the witness exists) and the stated limits: [`invariant-gates.md`](invariant-gates.md).

| setting | env | default | meaning |
|---|---|---|---|
| `invariant_gates` | `WISP_INVARIANT_GATES` | `enforce` | `enforce` blocks; `observe` logs what it would block; `off` disables all layers. An unknown value means `enforce` |
| `dependency_lock` | `WISP_DEPENDENCY_LOCK` | `locked` | only the exact word `unlocked` opens it |
| `gate_write_roots` | `WISP_GATE_WRITE_ROOTS` | empty | extra writable directories |

Verified 2026-10-07: `pytest tests/gates` (873 tests) passes; a 26-mutation probe breaks each layer on purpose and the suite catches 26 of 26
(the one survivor on the first run exposed a real fixed-point bug in the scrubber, now fixed and pinned).

