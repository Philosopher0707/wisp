# The harness: how wisp behaves by default, and how to check it

Verified 2026-10-05. Everything here can be re-checked with `wisp doctor`. Items marked **(PR #n)** were open when this
was written: confirm with `gh pr list` before relying on them.

## Check the harness

```bash
wisp doctor [--json]          # 9 hermetic probes; exit 1 on any FAIL, warnings are advice
wisp /doctor harness          # same, through the one-shot slash path
/doctor harness               # inside the REPL
/doctor                       # the 7-check boot preflight (100 ms budget)
/doctor deep                  # a different, slower check: the Docker sandbox image
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

`~/.local/bin/wisp` is a four-line wrapper: it runs the clean `main` worktree `~/dev/wisp-main` with the shared
virtualenv's Python (`PYTHONPATH` points at the worktree). Update it with `git -C ~/dev/wisp-main pull --ff-only`. The
maintainer's own `~/dev/wisp` checkout is separate and is never used for merges. Start wisp from the **project**
directory: the workspace is whatever directory you launch from.

## Tool surface

- The parent agent is offered 11 core tools: `read_file`, `write_file`, `edit_file`, `edit_file_multi`, `run_bash`,
  `run_tests`, `list_files`, `grep`, `glob`, `search_symbols`, `rewind`. `WISP_TOOL_PROFILE=full` offers every
  built-in. Hidden tools stay callable; explicit role tool lists and extension (MCP, skill) tools are untouched.
  Measured per provider call: parent 28,650 to 18,307 tokens; an unrestricted subagent 14,979 to 4,636.
- **Subagents in `auto_edit` never get** `run_bash`, `spawn`, `fanout` or the git/gh writes. They need a human's yes and
  a child has nobody to ask, so they are not advertised. The researcher role has `grep`, `glob`, `web_fetch` and
  `web_search` instead of a shell, deliberately: it reads untrusted web pages. In `full` mode a researcher still gets
  `run_bash`. **(PR: roles get grep/glob)**

## Approval: no approver is not an approval

A caller with no human (a subagent, a background agent, `wisp bench`, ACP) that needs a gated tool is refused with
`NO_APPROVER` (ADR-0055 section 3). That is correct behaviour. Ways to authorise, narrowest first:

1. `WISP_UNATTENDED_AUTO_APPROVE_TOOLS=web_fetch,web_search` **(PR #78)**: the user names tools an approver-less caller
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
  **(PR #77)**
- **`/swarm`** reports complete, partial or failed. A total failure skips synthesis and states each distinct reason
  once, with the roles it hit. A failure that will hit every agent (billing 402, rejected key 401) stops queued agents
  from starting. **(PR #76)**
- **An unknown model name** is auto-corrected only to a real spelling of it (equal, prefix or substring). When
  look-alikes exist but none is a spelling, the configured model is served and the close names are listed; when nothing
  resembles it, the first listed model is used (so a stale name from another provider is not a 404). **(PR #76)**
- An open REPL session runs the code it started with; quit and relaunch to pick up a fix.

## Working rules that keep this honest

- Reproduce a reported failure exactly (a local HTTP stub is enough) before fixing it; keep the reproduction as the
  regression test, and mutation-check the fix.
- Tests must not read the developer's machine: run them with an empty `HOME`. CI is the full-suite arbiter.
- Before any merge, tag the pre-merge state (`checkpoint/<date>/...`); never delete a remote branch.
- Pin tables (`scripts/derive_*.py`) hold line numbers: after adding lines, re-anchor, regenerate, run the pin tests. A
  new exception class needs a row in `scripts/derive_register.py`.
