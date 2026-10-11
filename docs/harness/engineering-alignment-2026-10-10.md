# Wisp against the twelve capabilities of an engineering agent (2026-10-10)

The owner gave a checklist of twelve things a capable coding agent needs (repository intelligence, fundamentals, navigation, debugging, editing,
verification, Git/GitHub, planning, context and memory, security, runtime state and recovery, self-evaluation) and asked that Wisp be asked questions
and aligned with it. Each row below is a question put to the code, the evidence, and what it means. **Observed** = read in the source or run on 2026-10-10
at `f2368d1`; **not verified** = not checked, do not rely on it. Nothing here was measured on real sessions: that needs the dashboard (PR #107).

| # | Capability | Question put to the code | Observed | Verdict |
|---|---|---|---|---|
| 1 | Repository intelligence | Does the agent get a map of the project before it acts? | `wisp/repo_map.py` builds a dependency-ranked map (tree-sitter, regex fallback) injected into the system prompt; imported by `core/stateless.py`, `core/context/boot.py`, `coding.py`; `search_codebase` tool | present. Whether it is on by default: not verified |
| 2 | Engineering fundamentals | Is this a harness property? | No: it is the model's. The harness can only test outcomes | out of scope for code |
| 3 | Navigation | Symbol and reference lookup, call hierarchy? | `lsp_definition/references/hover/symbols/diagnostics`, `search_symbols`, `grep`, `glob`. No call-hierarchy tool. The default 11-tool profile offers `search_symbols`, `grep`, `glob` but **no `lsp_*`** | partial: LSP exists, is off in the default profile |
| 4 | Debugging | Is diagnosis separate from repair, and is a failure classified (new, existing, environment, flaky)? | `diagnose` tool is an error-text analyser (full profile only). `core/convergence.py` has `FailureClass`/`classify_failure` and a baseline comparison, reached through `turn_criteria` and `wisp converge`; `config.acceptance_gate` defaults **False** | partial: the pieces exist, the default turn does not use them |
| 5 | Editing | Minimal patches, rollback, user's changes preserved? | `edit_file`, `edit_file_multi`, `rewind`; path gates P1-P5; gate C refuses `git reset --hard`, `checkout --`, `restore`, `stash drop`. `core/context/boot.py` records dirty or clean at start. **No check that the agent is about to edit a file that was already modified before the session** | partial: destructive commands are blocked, silent overwriting of uncommitted edits is not |
| 6 | Verification | Can a turn finish on a command that proves nothing? | Gate 5 (`core/gates/verify.py`) accepts only a recognised test/lint/type/build run whose exit status decides the result; `VerificationFloorGuard`; #104 adds the UNVERIFIED line | strong |
| 7 | Git and GitHub | Is analysis separated from authority to act? | `git_*` and `gh_*` tools (including `gh_pr_merge`) are in the gated lists of `infra/policy_engine.py` and `infra/security.py`. A PR-triage workflow: not found | present; triage not verified |
| 8 | Planning | Can a plan be made and updated from evidence? | `plan_task`, `update_plan`, `mark_step_done` (full profile only) | present, off in the default profile |
| 9 | Context and memory | What survives a long session? | `AgentRuntime.maybe_compact` runs before every turn above 50 messages (`core/runtime.py`). `compaction_model` defaults to empty, so the summarizer never runs unless configured, and the result was the bare text `[Compacted N messages]`: **everything the agent had done was erased**. The fact store failed 52 of 82 `remember` calls (field log O-12/O-13) | **defect, fixed by this change** (below); the fact store is still broken |
| 10 | Security | Is the boundary a control or a request to the model? | Five deterministic gates, secrets scrubbed before the model sees tool results. The injection guard handles nested events but the engine yields flat ones (PR #99, open) | strong, with one known hole |
| 11 | Runtime and recovery | Explicit state, no duplicate execution, resumable? | A `Phase` enum exists only on the graph path (`core/graph/phases.py`); the default turn has none. Duplicate `tool_call_id` is refused (`core/stateless.py`); `_recover_unfinished_turn` exists; Ctrl-C in the graph path is PR #106 (open) | partial |
| 12 | Self-evaluation | Is success measured? | `wisp bench`, `wisp judge`, `core/telemetry`; the dashboard that shows the numbers is PR #107 (open, conflicts with `main`) | partial: instruments exist, no valid number yet |

## What this change does (row 9)

`wisp/core/compaction_record.py` reads the messages being dropped and writes, without a model: what the user asked (last five, trimmed), the files written or
edited, and the verification commands that count as evidence with their result, each marked *edited after this run* when a later edit made it stale. It ends
with "run it again on the current code". `maybe_compact` appends it to a model summary and uses it alone, under an honest header (`No summarizer was
available, so what was said is gone`), when there is none. The classification of "does this command count" and "did it pass" is the completion gate's own
(`gates/verify.classify`, `verification._verify_result_is_success`), so there is one reading of both.

Limits: the record lists calls, not confirmed outcomes of edits; it keeps only the last twelve verification runs and five requests; each compaction adds
one system message and old ones are not merged (the same growth as before this change, now larger by at most a few kilobytes);
`wisp/infra/session_dto.py` has a second, older compaction path that still writes `[Compacted N messages]` and is untouched (not on the auto-compact
path: not verified which surfaces call it).

## Ranked next steps (not done here)

1. **Uncommitted-edit guard (row 5):** record the dirty set at session start; when a write targets a file in it, say so in the tool result (a note, not a block).
2. **R5 (row 11 and the "stuck agent" complaint):** "same observation, nothing changed" detection, designed in `~/dev/_scratch/wisp-r5`, two of its tests fail.
3. **Fact store (row 9):** find why `remember` failed 52 of 82 times.
4. **Default profile (rows 3, 4, 8):** decide whether `lsp_*`, `diagnose` and the plan tools belong in the 11 (each costs schema tokens on every call).
5. **Make the failure classification reachable (row 4)** from the default turn.
