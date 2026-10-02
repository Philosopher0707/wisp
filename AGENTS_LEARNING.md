# AGENTS_LEARNING.md — what the 2026-09-28/29 session did, and what it taught

Lessons an agent (or a person) should have before touching this repo. Written to be searched with BM25:
one lesson per `##` section, the search words in the heading and first line, evidence named. **Update it
after every milestone or phase** (see `CLAUDE.md`, "Knowledge base"). Prefer editing a lesson to adding a
duplicate. Session state and open work live in `docs/sessions/2026-09-29-network-agent/CONTEXT.md`.

## State: what was built and merged, and the live evaluation score (2026-09-29)

Search: state, PRs merged, what did the model score, live evaluation, baseline, 6/6, 0/6, capable model, results.

wisp became the host of an autonomous **network agent** (`wisp_net/`, blueprint NET-AGENT-ARCH-v2.4), reached
over MCP, on a **simulated** lab. PRs #45–#59 are merged into `origin/main`; only #39 (older) is open.

| PR | What |
|---|---|
| #45 | Agent bash always runs through the sandbox router (the disk sink had bypassed it) |
| #46 | A workspace `.wisp/mcp.json` runs nothing without an explicit trust entry (RCE) |
| #47 | MCP tools reach the model as `mcp__server__tool`; `tool_risk` in `mcp.json`; approval for external calls |
| #48, #49, #51, #52, #53 | wisp_net: sense/state, safety (what-if, policy), actuation + governance, reasoning + skills, closed-loop watcher |
| #50, #54 | Test de-flaking (speculative search, typeahead) |
| #55 | The agent cannot write `.git/hooks` or `.git/config`; the Docker sandbox mounts them read-only |
| #56 | Ten typed GitHub tools instead of bash (reads free, writes asked each time) |
| #57 | `python -m wisp_net eval`: a real model scored against ground truth; `mcp --warmup` |
| #58 | `wisp --print` honours `--provider`/`--workspace`/`WISP_PERMISSION_MODE`; a `tool_risk: read` MCP tool works in `read_only` |
| #59 | The test suite no longer writes into the developer's real trust file |

Live evaluation: local `llama3.2:3b` 0/6 (never emits a structured tool call); OpenRouter model in the
operator's `.env` 6/6 (n=1 per scenario, one unlisted model).

## Lesson: test a safety claim through the production path, not through one gate

Search: production path, gate, authorize, executor, end to end, five places.
A claim like "a read-only session can use declared reads" is true only if **every** layer that enforces it
agrees. #47 tested `authorize()` and was green; on the real path the policy rule, `policy_hard_deny`, the
executor's MCP block, the subagent filter and the schema filter each still refused. The same shape: the
evaluation harness said "read_only" while `wisp --print` ignored the mode and ran `full`.
**How to apply:** drive `ToolExecutor.execute` (what every transport uses), replace only the tool body, and
assert on the refusal a model would see. When a fix "should" work but a test still fails, the next gate is
the answer: print the event and find which layer produced the message.

## Lesson: every check needs a positive control and a mutation probe

Search: RED first, mutation probe, positive control, equivalent mutant, circular test, byte-identical restore.
- Write the test, watch it fail for the reason you expect, then implement.
- Break the code on purpose (one edit, run, **restore byte-identical**, assert equality) and confirm a test
  notices. A mutation that is not caught is either an equivalent mutant or a missing test; two of ours were
  missing tests (a pending check with an empty conclusion is blocked by another branch, so the status guard
  needed an inconsistent-data case).
- Two surfaces that now call the same function cannot be checked by comparing them to each other; state the
  expected answers independently (`approval_needed` needed explicit auto_edit/ask_all/full tables).
- A negative result needs a positive control: the local model scored 0/6, which means nothing until a scripted
  model that does call tools passes the same pipeline (`tests/net/fake_ollama.py`).

## Lesson: compare against the known-good baseline before deciding whose bug it is

Search: baseline, differential, origin/main, bisect, first bad commit, hang, hung, stuck test, which test hangs, timeout, collect-only.
15 tests failed and one hung on the user's local `main`. All 15 passed on `origin/main` (run the exact failing
ids there first); a bisect found the cause. **Pin the bisect predicate to the exact failure**: my first bisect
counted any failure as bad and blamed an older, unrelated failing test. A hang is a failure mode: run with a
hard timeout, and find the stuck test by counting the log's progress characters against `--collect-only` order.

## Lesson: a test that shells out must build its argv the way the shell would

Search: shlex.split, shlex.join, sys.executable, space in path, FileNotFoundError, wisp-cmd, agent-cmd, iCloud Drive (Archive).
`--wisp-cmd` is one string that the CLI splits with `shlex.split`, so a test that writes
`f"{sys.executable} {stub}"` passes only while neither path has a space. This checkout lives under
`/Users/philosopher/iCloud Drive (Archive)/...`, so `sys.executable` alone broke it — shlex cut the interpreter
path at the first space and all six stub runs died with FileNotFoundError before launching. On a CI checkout
without a space in the path this is invisible, so the suite is green there and red here.
Build the string with `shlex.join(...)` in one helper rather than quoting at each call site, and remember the
space is in the *checkout* path, not the path under test. Same trap applies to every `--*-cmd` option.
Evidence: commit `6d04edd`; reverting the helper to `" ".join` re-killed exactly those 6 tests.

## Lesson: a red full-suite result is not this change's result until the failure sets are diffed

Search: full suite, baseline comparison, failure set, diff, stash, blame, whose bug.
After fixing 6 tests in `tests/net`, the full suite still reported 21 failures. Counting matched (21 vs 21) but
that is not proof: sort the two `FAILED` lists and `diff` them so the *sets* are compared, not the totals. Here
they were byte-identical with and without the change, which is what moves a failure from "possibly mine" to
"pre-existing". Compare exact test ids, and run the baseline on the same files rather than the whole suite twice.

## Lesson: anything the host executes must be unwritable by the agent, at the lowest layer

Search: sandbox, escape, hooks, git config, mount, protected path, host-run, typed tools.
The sandbox is only as strong as the files the **host** later runs. `git_commit`/`git_push` run on the host and
git executes `.git/hooks/*` and whatever `.git/config` names, so one auto-approved `write_file` plus one
`git_commit` was host execution. The string scan on bash commands is a heuristic (`cd .git && cd hooks` walks
past it); the real boundary is a read-only mount (`docker_run_args`) plus the protected-path predicate
(`wisp/pathsec.py`, checks every occurrence). Give an agent **typed, argument-list tools** for GitHub work, with
no repo parameter and no shell, rather than a token in a container.

## Lesson: duplicated policy lists drift; make it one predicate or add an agreement test

Search: drift, duplicate lists, safe read set, three copies, parity, approval_needed.
The safe-read set exists in three copies (`security.py`, `policy_engine.py`, `capability_filter.py`) pinned equal
by `tests/reliability/test_permission_sets_agree.py`; adding a read tool means all three. Approval lists live in
about ten places. REST and the agent path disagreed on `auto_edit` writes until both asked
`tool_executor.approval_needed`. Prefer one function; failing that, one test that fails when they disagree.

## Lesson: a new status must be classified as final, or the retry machinery may retry it

Search: denial taxonomy, NO_APPROVER, BUDGET_EXCEEDED, TERMINAL_OUTCOME_CLASSES, unknown.
`classify_result` returned `unknown` for both statuses; `unknown` is not terminal ("must never be auto-retried").
The published taxonomy is pinned (ADR-0052); it now changes only by a recorded decision (ADR-0074) and a test
requires every denial status to be classified and terminal.

## Lesson: how to evaluate a model honestly

Search: eval, hermetic, scorer, refused attempt, benign refusal, n=1, re-score.
Hermetic run: temp HOME and workspace, scrubbed environment (credentials only by `--pass-env`), only read tools
declared `read`, no `--lab-control`. A run passes only with tool evidence, every ground-truth fact, and no
attempt to change anything. A refused call is **not** in `tool_calls`; it survives only in `errors`
("Blocked: READ_ONLY mode blocks X"). Refused skill loads and hand-offs to other agents are recorded, not
failed (the orchestrator skill tells the model to do both); refused writes, commands and actuating tools fail.
Change the scorer, then **re-score the saved raw output**; do not re-run the model. One sample per scenario shows
the loop works, not how often. Never print a key: load `.env` into the process environment and pass names.

## Lesson: CI flakes, and what to do with each

Search: flaky, re-run, timing, stdin, worktree race, cancelled turn.
Seen: `test_approval_stdin_exclusivity` (fixed by joining reader threads, #54), a concurrent `git worktree add`
race in `test_multi_agent_worktree`, `test_r4_cancelled_turn_replays_then_completes` (10 ms polling). Rule: check
the failure is unrelated (passes locally and on `main`), re-run only the failed job, and fix it if it recurs.

## Lesson: registers, ADRs and pinned line numbers

Search: ADR, decision index, CURRENT_AUTHORITIES, CURRENT_FLAGS, derive script, re-anchor, pins, pinned line numbers, CONTEXT.md, which file not to edit.
An ADR is appended **before** `## Decision index`, plus one index row at the end of the file; then regenerate
`CURRENT_AUTHORITIES.md` in a separate commit. `CONTEXT.md` (253 KB) and the source files the flags table names
are pinned by line number: editing them shifts the pins. The generators refuse an unsound table and say where
the words moved; re-anchor, commit the script fix **first**, then regenerate (the header records HEAD).
Do not add text to `CONTEXT.md` casually; put session records under `docs/sessions/`.

## Lesson: shell and tooling gotchas that cost time

Search: heredoc, rtk, zsh, timeout, pyt, PYTHONPATH, background, worktree.
- An **unquoted heredoc runs backticked text as commands**; write PR bodies from `<<'EOF'`.
- `rtk` rewrites `git log` and `grep` and can print the wrong thing; use `rtk proxy git ...` for the truth.
- zsh does not word-split `$VAR`; macOS has no `timeout(1)`; `git merge -F -` does not read stdin.
- The `pyt` wrapper unsets `PYTHONPATH`; put the repo on `sys.path` inside the script.
- Never delete a log a background job is writing; never run two suites in one worktree; do not send signals to a
  test process to "see a stack" (wisp registers handlers).
- Verify a number before writing it into a commit or PR (I wrote 203 where 191 was right).

## Lesson: what I may decide alone, and what I must surface

Search: boundaries, autonomy, permission decision, read_only, policy.
Boundaries (2026-09-29): simulated-first research scope; I open PRs and the user merges; `read_only` allows
skill loading and delegation with tests; I may repair local `main` as new commits. Never push the user's
local-only commits, spend money, delete remote branches, or edit their uncommitted files. Widening a permission
mode or changing a pinned policy is the user's decision; state the trade-off and ask.

## Known gaps (not verified)

Search: unverified, gaps, limits.
- The Docker read-only mounts were verified at the argument level only (no Docker daemon on the dev host).
- The success paths of `gh_pr_comment`, `gh_pr_close`, `gh_pr_merge` were never run against real GitHub.
- The `act` tier of the network agent has not been driven by a real model.
- The prompt's "DENIALS ARE FINAL" list does not name `NO_APPROVER`/`BUDGET_EXCEEDED` (ADR-0074, known limit).
- Real adapters (gNMI, Kafka, Batfish, OPA, SSO) are out of scope until a host with Docker and disk exists.

## Lesson: what actually enters wisp's context, and why a local model flounders (2026-10-02)

Search: context window, num_ctx, truncation, 2048, tool overload, skills menu, boot seed, AGENTS.md, CLAUDE.md, invariant hunting, verification guard, memory pollution, cannot fix.
Measured on the wisp repo as workspace (read-only probe; nothing modified):
- **Payload per request ~22K tokens**: system prompt ~4.7K, turn-0 seed ~2.3K (first 8,000 chars of the FIRST of `AGENTS.md`, `CLAUDE.md`, `.wisp/instructions.md`; so wisp never loads `CLAUDE.md` when `AGENTS.md` exists), and **104 tool schemas ~15.5K** (52 built-in + 52 `skill__*`).
- **Ollama silently keeps ~2K of it.** `ollama_client` sends only `temperature` and `num_predict`, never `num_ctx`, so Ollama applies its default window (2,048 here, v0.35): `prompt_eval_count` was 2,050 of ~22K. wisp assumes 256,000 (`DEFAULT_MAX_CONTEXT_TOKENS`) and asks `/api/show` for the model's *maximum*, so it never compacts. With `num_ctx=32768` the same payload evaluated 19,509 tokens and the model acted on the tool list. Not applicable to OpenRouter.
- The base prompt and the AGENTS.md seed are **not** invariant-obsessed (the word appears 0 times in the seed). Skills do not auto-activate (`match_skills` has no callers); 20 of 52 advertised skills are about invariants/architecture/audit, offered as tools.
- The one completion gate in the live path is `VerificationFloorGuard` (flag `verification_loop`, ON): after a code edit it rejects a finish with no passing verify run; after `min_turns` (5) and its nudges it allows an UNVERIFIED finish. The suggested verify command is the whole suite with `-x`, which is slow and was red/hanging on local main. The heavier machinery (`acceptance_gate`, `stagnation_gate`, `recovery_ladder`, `goal_state`) is OFF.
- Real REPL history (`~/.wisp/wisp.db`, audit log): reads:edits ~889:29 all time, 60:12 since 2026-09-27; all 343 sessions record workspace `/Users/philosopher` (the home directory), so check where the REPL is started.
- The configured model `qwen2.5-coder` exists neither in Ollama nor as an OpenRouter id (`qwen/qwen-2.5-coder-32b-instruct` is the real one).
- Memory pollution: 16 of 45 workspace facts come from pytest tmp workspaces, 2 global facts are fixtures ("stale important", "newest fact"), 82 of 101 session summaries are fixtures. The tests write into the real `~/.config/wisp/` (same class as the trust-file leak).
**How to apply:** when a local model "does nothing useful", read `prompt_eval_count` before blaming the model; measure the payload before trimming it; check what the harness assumes about the window.

## Lesson: the eval harness's hermetic HOME hid the interpreter's packages, and one run per scenario is not a rate (2026-10-02)

Search: eval harness, wisp_net eval, hermetic_env, HOME, PYTHONUSERBASE, user site-packages, pip install --user, ModuleNotFoundError requests, wisp exited 1 without JSON, positive control failing, samples, jobs, pass rate, rtk pytest.
- **Symptom:** `tests/net/test_net_eval_pipeline.py` (the positive control that makes the 0/6 mean something) failed 4 of 4 on a raw run: `wisp exited 1 without JSON` — a harness failure that reads like a model failure. **Cause:** `hermetic_env` sets `HOME` to a temp dir, which moves `~/.local`, so `requests` (installed with `--user` in `~/.local/lib/python3.11/site-packages`) was not importable in the child. **Fix:** pin `PYTHONUSERBASE` to the real user base (package location, not credentials; an explicit `PYTHONUSERBASE` in the source environment wins). Tests: the child interpreter sees the same `site.getusersitepackages()`; the environment still carries no credentials. Raw `tests/net`: 4 failed -> 218 passed.
- **How it hid:** commands run through the `rtk` hook printed `Pytest: N passed` and passed; the identical command under `rtk proxy` (raw) failed. Compare baseline and change with the **same invocation** — my first "baseline passes" was a different environment, not a different build. Use `rtk proxy python -m pytest ...` when a result looks environment-dependent.
- **Depth:** `eval` now takes `--samples N` (pass rate per scenario) and `--jobs J` (runs share nothing, so they overlap safely; mind provider rate limits). Kept files get `.s<N>` in the name only when samples > 1.
**How to apply:** when an end-to-end harness fails with no model output, read the child's stderr before touching the scorer; a scrubbed environment must still let the interpreter import what it ships with.
- **Provider and key (2026-10-02, follow-up):** `eval` had `--provider` default `ollama` and read keys only from the shell, so an unset `--pass-env WISP_API_KEY` was silently skipped and the run 401'd. Now: no built-in provider (flag > `WISP_PROVIDER`/`WISP_MODEL` from env > `~/.config/wisp/.env` > refuse, exit 2), `eval` calls `load_user_env()` (env wins; `tests/test_dotenv_is_read.py` pins `wisp/` only, so this second entry point is allowed), and an unset `--pass-env` name is refused up front, names only. The child still gets a scrubbed env and a temp HOME. In-process CLI tests need HOME and `os.environ` isolated, or they load the developer's real `.env` into pytest.
- **Pre-existing, same class, not fixed here:** `tests/test_dotenv_is_read.py` fails 10/11 on this machine (identical on baseline) because its private HOME also hides `~/.local` user-site `requests`; it needs `PYTHONUSERBASE` in its child env.

## Lesson: what guides the model in the eval, the 200-character skill cut, and a plugin system nothing calls (2026-10-02)

Search: skills block, instructions[:200], inline-instructions, skill__ loader, read_only, system prompt, tool descriptions, plugin.json, PluginManifest, discover_plugins, PluginExtension, mcp_servers, unwired, eval guidance.
- **Measured payload** (capture the first request through the real `wisp --print` with `tests/net/fake_ollama.py`): user prompt 1 sentence; system prompt ~18K chars of the generic coding agent; **skills shown as description + `instructions[:200]`**; 90 tools offered (32 net, 5 `skill__`, ~50 coding tools refused by `read_only`, not hidden). In every live run the model's `skill__net-orchestrator` call was refused, so it never saw the orchestrator loop or its escalation rules; what steered it was the tool descriptions (`net_status`: "Start here") and the `net_knowledge` runbook it chose to read.
- **Fix:** per-skill opt-in `inline-instructions: true` (strict bool); the five net skills opt in. Global un-truncation rejected: the user's `~/.agents/skills` is 362 KB (~90K tokens) and the context-overload lesson above applies. Surface note: a workspace skill can now opt in too, but its unbounded `description` was already in the same block.
- **Plugin system is unwired:** `discover_plugins` has no production caller; `PluginExtension.tools()` looks for `plugin.tools` which `PluginManifest` does not have, so it returns `[]`; nothing reads a manifest's `skills` or `mcp_servers`. A `plugin.json` for wisp_net would validate and install and change nothing. Not shipped. Wiring it means plugin-declared `tool_risk: read` would feed the permission model (`wisp/core/contracts.py` `is_declared_read`), a permission decision for the operator.
- **Pre-existing, unrelated:** `tests/test_cli_surface_e2e.py` Group3 headless (3) and `test_repl_help_then_exit` fail identically on baseline (3 min each).
**How to apply:** before building on a subsystem, find its production caller; a manifest nothing reads is documentation. When a prompt "teaches" a model something, read the captured request, not the skill file.
