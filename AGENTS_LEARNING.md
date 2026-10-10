# AGENTS_LEARNING.md — what the 2026-09-28/29 session did, and what it taught

Lessons an agent (or a person) should have before touching this repo. Written to be searched with BM25:
one lesson per `##` section, the search words in the heading and first line, evidence named. **Update it
after every milestone or phase** (see `CLAUDE.md`, "Knowledge base"). Prefer editing a lesson to adding a
duplicate. Session state and open work live in `docs/sessions/2026-09-29-network-agent/CONTEXT.md`.

> **Provenance:** this file predates the commit that first tracked it. `ccf6d01` (2026-10-02) added the shlex
> and failure-set lessons and committed the whole file as new, so `git log --diff-filter=A` attributes every
> earlier lesson here to that commit. The lessons above the shlex one are from the 2026-09-28/29 session.

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
they were byte-identical with and without the change — which proves **"not caused by this change"**, and *only*
that. It does **not** prove "pre-existing at HEAD": both runs used the same dirty tree (13 modified, 63
untracked), and six of the failing test files were themselves untracked — absent at HEAD, so they *cannot* fail
there. To earn "pre-existing", compare against `origin/main` or a clean worktree — and if you use a worktree, put
it somewhere **without a space in the path**: this checkout's space is itself a variable (6 net-eval tests turn
on it), so a no-space worktree *understates* failures for that class. Compare exact test ids, and run the
baseline on the same files rather than the whole suite twice.

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
2026-10-04: the user explicitly told me to review the open PRs and merge them. I merged only after green CI,
one PR at a time, with checkpoint tags first. That was a one-off instruction, not a change to the standing rule.

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

## Lesson: a corrupt SQLite WAL crashes boot, and malformed is DatabaseError not OperationalError (2026-10-03)

Search: sqlite corrupt, malformed, database disk image is malformed, WAL, quarantine, UnifiedStore, OperationalError vs DatabaseError, iCloud sync, boot crash, wisp repl traceback.
- **Symptom:** `wisp repl` died in `CompositionRoot` -> `ImmutableAuditTrail` -> `UnifiedStore._init_schema` -> `CREATE INDEX idx_sessions_updated` with `sqlite3.DatabaseError: database disk image is malformed`. The handler only caught `OperationalError`, but "malformed" is raised as `DatabaseError` (its parent), so it escaped. Evidence: `PRAGMA integrity_check` on `.wisp/wisp.db` failed with `Tree 2 page 313 ... invalid page number`; the global `~/.config/wisp/wisp.db` was `ok`.
- **Root cause shape:** the corruption lived in `.wisp/wisp.db-wal` (12 KB of uncheckpointed frames; the main file alone was healthy). Any full-table scan through the WAL raised. Likely trigger: crash or iCloud sync mid-checkpoint (repo lives under "iCloud Drive (Archive)"). Recovered all 203 sessions with `sqlite3 db ".recover"` into a fresh file (backup kept at `.wisp/backups/`).
- **Fix (`wisp/infra/store.py`):** `_ensure_initialized` now treats corruption-marked `DatabaseError` as a file-health problem: drop WAL sidecars + retry (heals WAL-only corruption with zero loss), else quarantine main file to `wisp.db.corrupt-<epoch>` + fresh init, else temp fallback. Any sqlite error on the retry path falls through to fallback — boot never crashes on sqlite errors; non-sqlite errors still raise loud. `ImmutableAuditTrail._init_table` delegates to the store's quarantine on the same markers and retries once.
- **Corruption lies about its shape:** the same trashed pages surfaced as `DatabaseError: malformed`, as `OperationalError: no such column` (garbage parsed as schema, which then misfired the migration ALTER into "duplicate column"), and as `OperationalError: disk I/O error`. Message-sniffing is only the fast path; anything escaping the static init DDL is file-health suspect. Tests: `tests/test_unified_store.py::TestCorruptionRecovery` (garbage file -> backup + fresh; old-schema DB + trashed data page -> backup + fresh, both deterministic).
**How to apply:** catch `sqlite3.DatabaseError` (not just `OperationalError`) around any SQLite boot path; never delete a corrupt DB, rename it aside; distrust the error message, trust `integrity_check`.

## wisp doctor harness invariants checks tool profile budget context skills audit (2026-10-05)

- `wisp doctor` (and `/doctor harness`) runs `wisp/cli/doctor_harness.py`: nine probes for what the 2026-10 review fixed: tool profile, subagent tool surface, spend meter, context fit, global skills, skill capture, audit chain, REPL input, fleet (manifest, repos, MCP config drift; local and read-only). Exit 1 on FAIL; WARN is advice.
- Boot preflight stays at 7 cheap checks under its 100 ms budget. Probes that import heavy modules or touch disk must not join it: a slow check shows up as a startup warning on every launch.
- Every check has a mutation test that breaks the invariant in the code under test and expects red. A check that cannot fail is decoration.
- The probes are hermetic (temp workspace, temp audit log). The live audit log is read-only: a historical break is a WARN with "do not rewrite", never a repair.
- First real run on this machine: 43 global skills ≈ 4.8k menu tokens per request (WARN, user trims `~/.agents/skills`); live audit log breaks at entry 1848 (the known pre-fix fork).
- Annotating `ContextAssembler.__init__` made an old `# type: ignore[no-untyped-call]` unused, and `mypy` (warn_unused_ignores) failed on it. Remove an ignore when its reason goes away.

## Lesson: CI step order hides lint; red tests mask the ruff gate (2026-10-04)

Search: ci, ruff, pytest, step order, masked, lint gate, main red, green main, unused import, F401.
`ci.yml` runs pytest, then `ruff check wisp/ wisp_net/ tests/`, then mypy. A job that stops at the test step never
reaches the lint gate, so while main was red an unused import (`tests/reliability/test_idempotency_store.py:14`)
sat there unseen. Evidence: PR #62's first run passed its tests and failed only on Ruff; `ruff check` on a pure
`origin/main` archive reproduces the same single F401. Rule: after fixing the first red step, read the next one;
run the whole gate list (tests, ruff, mypy) before pushing, not only the step you were chasing.

## Lesson: tests that pass on the developer machine and fail in CI (hermetic workspace) (2026-10-04)

Search: hermetic, .venv, HOME, doctor, preflight, fresh HOME, repl history, e2e, local pass CI fail, cli_surface.
Main's CI failed exactly six tests: five in `test_preflight_doctor.py` (the build-sequence check needs
`<workspace>/.venv/bin/python3`; a CI checkout has none) and the REPL `/help` e2e (it asserted the legacy "Available
commands" header; the Dispatcher prints "Built-in commands:"). Reproduce with an **empty `HOME` and no `.venv`** in a
separate worktree: pure main gave 6 failed / 31 passed, the fixed branch 37 passed. A `.venv` symlink in the
worktree hides the failure; I made that mistake first. Also found: `FileHistory` does not create its parent
directory, so the REPL crashed at the first keystroke on a fresh HOME. Fixed in PR #62.

## Lesson: an audit hash chain read from memory forks under concurrent writers (2026-10-04)

Search: audit, hash chain, fork, TAMPERED, flock, concurrent writers, in-memory head, verify, truncation, keyed MAC, witness.
`AuditTrail.record` chained onto an in-memory `_last_hash`, but the CLI, the server and the tests all append to
`~/.config/wisp/audit.jsonl`. Evidence on the live log: 4338 entries, 8 fork points, 14 broken links since
2026-08-29, **every entry's own hash valid** (nothing edited); `wisp audit verify` said TAMPERED at entry 1848,
which links back to entry 1825. The false alarm trains people to ignore the verifier. Fixed in PR #61: re-read the
head from the file under `flock` before each append; four spawned processes appending 200 entries to a copy of the
live log added 0 broken links. **Still open:** truncation is undetectable and the chain is unkeyed (a rewrite plus
rechain passes `verify`); the fix is a keyed MAC and a witness outside the log's directory, as in always-on-worker
ADR-0005/0007/0008. Never rewrite the live log: its 14 breaks are evidence. Probes: `docs/reviews/2026-10-04-probes/`.

## Lesson: a SQLite WAL can hold committed data; move it aside, never delete it (2026-10-04)

Search: sqlite, WAL, wal, shm, sidecar, corrupt, quarantine, data loss, recover, zero loss.
PR #60's store recovery deleted `-wal`/`-shm`/`-journal` to "heal" a corrupt database and claimed zero loss. A WAL
holds transactions committed but not yet checkpointed, so deleting it can drop recent sessions silently. The test
was written first and failed against that behaviour; the sidecars are now renamed to `<name>.corrupt-<epoch>`, like
the main file, so `sqlite3 <db> ".recover"` can still reach them.

## Lesson: a subclass of TimeoutError is swallowed by `except TimeoutError` (2026-10-04)

Search: FirstTokenTimeout, TimeoutError, asyncio, except order, subclass, contract deadline, mislabel, subagent.
`FirstTokenTimeout` subclasses `asyncio.TimeoutError`. A new mid-run deadline handler caught `TimeoutError` and so
swallowed it, reporting "contract deadline reached after 60s" for a provider that streamed nothing in 0.5s. The test
that would catch it needs `max_retries=0`, because a retry hides the difference. Re-raise the specific exception
before the generic one.

## Lesson: a timing-tight test is a flake, and the order effect is a symptom (2026-10-04)

Search: flaky, flake, timeout 0.2, test_timeout_preserves_partial_round, order dependent, deadline, bisect.
`test_timeout_preserves_partial_round` gave a whole subagent 0.2s. It passed alone, failed 4/4 in a full-class run,
and passed in a two-test pair, which looked like pollution. Loosening only the deadline to 1.5s made it pass in the
class and across three files, so the cause was setup time, not shared state. Measure before bisecting for pollution.

## Lesson: orchestrating other repos over MCP without giving the model their authority (2026-10-04)

Search: mcp, worker, shim, fleet, manifest, approve, label, consent, WISP_STRICT_ENV, environment inheritance, always-on-worker, gump.
Each worker is an MCP stdio server that shells out to the worker's own CLI, so its guards (keys, witness path, audit
chain) apply unchanged. The tool surface is a reviewed allowlist: no `approve` (always-on-worker), no `label` and no
`run` (gump), and no path, model or credential arguments; a test pins each. Secrets come from an env file named in the
manifest, never the manifest. Verified through wisp's own MCP client under `WISP_STRICT_ENV=1`: `gump_verify` reported
"chain intact: 2410 events verified". Found on the way: wisp starts MCP servers with its **full** environment (93
variables, including API keys) unless `WISP_STRICT_ENV=1` is set. See `docs/fleet/README.md` and
`docs/adr/2026-10-04-fleet-worker-contract.md`.

## Lesson: reading a secrets-bearing file leaks it into the transcript (2026-10-04)

Search: tail, zshrc, api key, plaintext, secrets, env file, never cat, grep -c, rc file.
`tail ~/.zshrc` printed a plaintext API key line into the session. To learn whether a setting exists, use `grep -c`
or `grep -o '^[A-Z_]*='` (names only), never `cat`/`tail` on a shell rc file or a `.env`.

## Lesson: two nested deadlines that differ by milliseconds lose the partial result under load (2026-10-04)

Search: asyncio.timeout, nested timeout, deadline, same tick, double cancel, partial round, CI only, busy loop, backstop.
`SubagentRunner.run` wrapped `_run_agent` in `asyncio.timeout(timeout_seconds)` while the execution loop inside used
`asyncio.timeout(remaining)` with `remaining = start + timeout_seconds - now`. The two deadlines differed only by the
setup time. When the event loop was busy past both, their timers fired in the same tick and both cancelled the task;
the inner `__aexit__` then did not convert its cancellation to `TimeoutError`, and the outer backstop won with an empty
round ("Timed out after 1.5s - no tool calls were made"). That is why `test_timeout_preserves_partial_round` failed in
CI at 0.2 s and again at 1.5 s but passed locally: it was not setup latency, and a 30,000-file workspace did not change
it. Reproduced deterministically with a blocking `time.sleep` standing in for the busy loop. Fix: the outer backstop is
`timeout_seconds + 2 s`. Rule: when a result must survive a deadline, the handler that preserves it needs a deadline
strictly earlier than any backstop, by a margin larger than scheduling jitter.

## Lesson: a test that cannot fail under its own mutation is not a test (2026-10-04)

Search: mutation, test weakness, structural assertion, wall clock, per_session_locks, serialised, passes anyway, positive control.
`test_per_session_locks` asserted that two 50 ms turns finished in under 90 ms; CI took 113 ms. I rewrote it to require
both turns to be in flight at once. The first draft still **passed** when I made every session share one lock: the
starved turn timed out, the runtime turned that into an error event, and a non-empty result satisfied the assertions.
Strengthened to require real `content` events and no `error` events, plus a control that one session id is never
concurrent; the mutation then fails in 3.4 s. Always run the mutation, and give a test that waits an outer timeout so it
fails fast instead of hanging.


## Lesson: skill capture passed 143 tests and the auto half did nothing (2026-10-04)

Search: skill capture, auto skill, auto_skill_capture, discover_skills, .wisp/skills/auto, SKILL.md, YAML, frontmatter, injection, write-only, round trip.
Auto-capture on a RESOLVED turn wrote `.wisp/skills/auto/<slug>/SKILL.md`, a directory `discover_skills` never scanned; and
every file it wrote was invalid YAML (`description: Auto-captured RESOLVED workflow: <task>` has a `: ` in a plain scalar),
so `parse_skill` returned None. The tests asserted the *path written* and used colon-free descriptions. A third defect hid
behind the first two: task text and tool arguments went into the file verbatim, and `parse_skill` ends the frontmatter
with a substring `split("---")`, so a newline or a `---` could add keys or close the frontmatter. Fixing "not loaded"
alone would have created a persistent prompt-injection path, so all three were fixed together (PR #63). Rules: test a
write-then-read **round trip** and the real consumer (`discover_skills`, `SkillExtension.tools()`), not the write alone;
use a description with a colon, a newline and `---`; and when a feature is dormant, ask what the dormancy is hiding before
you wake it. Still open: `parse_skill`'s substring split is a hazard for hand-written skills.

## Lesson: how the system prompt is assembled, and where the budget leaks (2026-10-04)

Search: context assembly, system prompt, ContextAssembler, _fit_sections, token budget, 6000, skills block dropped, silent omission, unbudgeted, tools block, skill__ tools, schema tokens, _build_system_prompt.
Path: `_build_system_prompt` keys a 64-entry cache on (workspace, mtimes of `.wisp/rules.md`, `.wisp/conventions.md`, the memory
file and the sessions file, subagent-prompt hash, allowed-tools hash, thin flag). On a miss it gathers skills, project context,
memory, git, repo map (subagents skip the heavy ones), loads `rules.md` as `role_extra`, and `ContextAssembler.build` orders
sections by priority (0 default_system, workspace; 1 context_files, mandatory skill, plans; 2 role_extra, skills, memory;
3 project context, code index, summaries, git, repo map) and `_fit_sections` admits them against `max_tokens` (default 6000).
Over budget: priority 0 and memory are truncated, **everything else is dropped whole**. Appended afterwards, outside the budget:
tools block, lint context, module summary, then per turn the query-relevant files, compaction notice, operating context and
environment block. Measured on this repo (clean HOME): ~6.5k tokens, ~2k of them outside the budget. Evidence and probes:
`docs/reviews/2026-10-04-probes/context_*_probe.py`.
- **Budget accounting resets after a truncation.** `current_tokens = estimate(truncated)` overwrites the running total with
  one block's size, so later sections are judged against a nearly empty budget. With a 1000-token budget, a 696-token
  system section, an over-budget memory block and a 296-token project note produced **1527 tokens on main (1.53x) and 1354
  on PR #60 (1.35x)**, with the project note admitted after memory was truncated. #60 fixed the ruler and the footer, not this.
- **A skills block larger than the budget vanishes silently.** On the real HOME, 56 skills are discovered (53 model-invocable)
  and the block alone is 10,398 tokens: no `## Skills` section, no omission note (`last_truncate_label` is only set for priority 0
  and memory), while 53 `skill__*` tools still ship 10,197 tokens of schema on every request. The prompt that explains the
  menu is gone and the menu is paid for anyway. Global skill directories (`~/.agents`, `~/.claude`) are what flood it.
- **~30% of the prompt is never budgeted** (tools block 1489 tokens on its own).
Status (2026-10-05): **accounting fixed in PR #64** (cumulative, done in characters on the one ruler, separators, compact
header and note inside the budget, every cut or dropped section named in the note; the 1000-token case now gives 989). A
first draft returned an empty prompt for a budget smaller than the truncation header, which an existing test caught; the
first critical section now keeps a minimal slice. **Global skills now load from `~/.agents/skills` only** (same PR): that took
this HOME from 56 to 50 skills because `~/.agents/skills` itself holds 47, so the menu is still ~9.3k tokens and is still dropped,
now with a note. Compact forms measured on the same 47 skills: 8,840 tokens (current), 5,440 (name plus description), 2,289
(description cut to 120 chars), 389 (name only). The user chose to trim `~/.agents/skills` by hand instead of changing the format.
Still open: ~30% of the prompt is appended after budgeting; `config.skill_dirs` is a dormant setting.

- REPL input: `v` and space were swallowed on an empty prompt even with nothing to open ("view" arrived as "iew"), and readline's empty in-memory history overwrote prompt_toolkit's `FileHistory` on exit. Fix: `build_key_bindings` (insert the char when there is nothing to act on) and `own_history` (one writer). Test the real library on piped input; a mock cannot show a key-binding bug. A pty relaunch confirmed history survives.
- A source-grep test pins code by function name; extracting code from `make_input_fn` broke three of them. Move the pin to the new home, do not loosen it.
- `wisp/core` must not import `wisp.cli` (`tests/test_layer_direction.py` ratchets it; a new edge fails CI, and the fix is to move the code, not to add the edge to `CLASSIFIED_EDGES`). A diagnostic that probes the CLI lives in `wisp/cli/`. My targeted test list had skipped the architecture tests; when adding a module, run `tests/test_layer_direction.py` too.

## slash doctor handler reached by the REPL vs legacy registry (2026-10-05)

- `/doctor` has two handlers: the live one is `_doctor` in `wisp/cli/dispatcher.py`; `wisp/repl/commands/doctor.py` is the legacy registry the REPL does not reach for it. I wired `/doctor --deep` into the legacy one and claimed it worked in the REPL without trying it there; in the REPL `deep` already meant the Docker image check. Fix: `/doctor harness` in the live handler, tested through the real `Dispatcher`. Lesson: verify a slash command by dispatching it, not by calling the function I edited; and grep for an existing meaning before reusing a flag name.

## write_file slow workspace home import graph affected tests rglob (2026-10-05)

- **Symptom (live):** a REPL started from `$HOME`; `write_file` of a 5 KB script took minutes. The tool itself takes 1-7 ms.
- **Cause:** after every write/edit the executor calls `run_affected_tests`, which built `build_import_graph(workspace)`: `root.rglob("*.py")` over the whole workspace, skipping only dot-directories and `__pycache__`. From `~` that walked `Library`, `site-packages`, `node_modules` and the iTerm2 Python environments (tens of thousands of files, each parsed). Measured: **581 s** to find 0 affected tests. The 60 s `timeout` bounded only the pytest run that comes after the graph.
- **Fix:** pruned `os.walk` (virtualenvs by `pyvenv.cfg`, dependency/build/`Library` directories), a hard budget (5,000 files, 5 s) that refuses rather than truncates (`ImportGraphTooLarge`), the home directory is never analysed, and a too-large verdict is remembered for 10 minutes so only the first write pays. Measured after: 5 ms in `$HOME`; a large non-home workspace pays one 5 s walk, then 0.1 ms.
- **General rule:** anything that runs on every write needs a budget of its own; a timeout on a later step does not cover an earlier unbounded one. A workspace is whatever directory the user launched from, so a tool must not assume it is a project.
- **Pin tables:** a new exception class needs a row in `scripts/derive_register.py` or `derive_register.py` refuses to run (and two reliability tests fail).
- **Workaround for an already-running session:** start wisp from the project directory, not `~`.

## swarm all agents failed 402 billing verdict model auto-correct systemic (2026-10-05)

- **Symptom (live):** `/swarm` with an out-of-credit key: four agents hit the same HTTP 402, then "✓ Swarm complete", a "Synthesizing final answer…" step, and four identical `[INCOMPLETE]` blocks; the configured model `qwen2.5-coder` had been replaced by `aion-labs/aion-2.0`.
- **Root causes:** (1) `_SwarmResult.success = any(...)` stood for complete, partial and failed alike. (2) `resolve_selection` set `suggested=available[0]`, the first model alphabetically, so any unknown model name became an arbitrary one (a different family and price). The close names it had computed went unused. (3) `run_parallel` retried only transient errors but kept starting queued agents after a refusal that fails every agent.
- **Fix:** `_swarm_verdict` (complete/partial/failed; a total failure skips synthesis and reports each distinct reason once with the roles it hit, in words: "add credit or raise the key's limit"); `suggested` is a prefix/substring spelling of the configured model (`_strong_match`); when look-alikes exist but none is a spelling, the configured model is served and the close names are shown; when nothing resembles it at all the old contract stands (first listed model, so a stale name from another provider is not a 404: `test_nvidia_unknown_autocorrects_in_composition`). My first version dropped that fallback too and a pinned test caught it: narrow a fix to the failing case; `SYSTEMIC_MARKERS` / `is_systemic_text` in `core/recovery.py` next to `TRANSIENT_MARKERS`, and `run_parallel` does not start queued agents after one.
- **Limit, stated plainly:** with 4 agents at concurrency 4 all four requests are already in flight, so the abort saves nothing there; it matters for fan-outs larger than `max_concurrent`. For the 4-agent swarm the protection is the honest verdict plus no synthesis call.
- **Method that worked:** reproduce the user's exact output hermetically first (a local HTTP stub answering 402, driven through the real CLI, runtime, orchestrator and OpenAI provider), then fix against it. The stub test is the regression test. Each fix is mutation-checked.
- **A shared token is not a match:** `_closest` ranks "coder" alone as related, so the top "close" name for `qwen2.5-coder` was `kat-coder-pro`. Suggest only equal/prefix/substring.
- **Tests that read the real home:** `tests/test_toolchain_e2e.py::test_repl_chain_end_to_end_over_production_wiring` fails on a machine whose `~/.config/wisp/mcp.json` registers MCP servers (the fleet workers) and passes with an empty HOME. Same lesson as before, new instance; separate task spawned.
- **Pin tables again:** adding lines to `core/recovery.py` moved `LadderExhausted` and `derive_register.py` refused to run. Re-anchor the table, then regenerate.

## fleet doctor unmanaged linked worktree false alarm (2026-10-05)

- A linked worktree of a managed repo (`.git` is a file: `gitdir: <owner>/.git/worktrees/<name>`) is that repo, not a new one. `discover_unmanaged` now skips it only when its owner is in the manifest; a worktree of an untracked repo is still reported, and a submodule (`.git/modules`) is not mistaken for one. Found because making `~/dev/wisp-main` for the global `wisp` made `wisp doctor` warn about it.

## core workspace walk one bounded pruned walk home guard (2026-10-05)

- **Why:** about thirteen independent `os.walk`/`rglob` loops with at least five private skip lists. The 581 s write was one loop lacking rules another had. The mechanism (prune before descending, never follow symlinks, stable order, a file/time budget) now lives once in `wisp/core/workspace_walk.py` (stdlib only), with two budget policies: `stop` (a search's partial result is useful; the caller reports it) and `raise` (an analysis where a partial tree gives a wrong answer).
- **A pure refactor, by construction:** each caller states its own skip set (`skip_dirs`, `skip_hidden`, `skip_venvs`), so moving a caller changes nothing until it chooses to. First two callers: the import graph (`raise`) and `grep`/`glob` (`stop`, with their own per-search clock kept for the per-line regex checks). Their existing suites (159 tests) passed unchanged. Remaining walkers (`repo_map`, `code_index`, `semantic_index`, `workspace.py`, `core/context/repomap.py`, `suggestion_watcher`, ...) move one PR each with their own pins.
- **`is_home_directory`** is shared by the affected-test lookup (refuses to analyse `$HOME`) and the boot preflight (`path_environment` now warns once: "your home directory, not a project").
- **The register generator reads tracked files only:** a new module's exception class raised `KeyError: 'WalkBudgetExceeded'` until the files were `git add`ed. `git add` new files before regenerating the pages. A new exception class needs a row, and moving code shifts pinned lines (`ImportGraphTooLarge` 32 -> 25).
- **A vacuous assertion slipped into my first test** (`... or first == first`); fixed to assert the exact order before implementing. Read your own test for an escape clause.

## unattended auto approve tools NO_APPROVER background research agents web_fetch (2026-10-05)

- **Symptom (live):** background research agents were refused `web_fetch` / `web_search` with `NO_APPROVER` (`auto_edit` gates every non-READ tool, and a background agent has nobody to ask). By design (ADR-0055 §3: "no approver is not an approval"); the existing remedies were global (`auto_approve`, `permission_mode=full`) and approve every write and shell call too. A policy bundle cannot help: it is narrow-only (deny / force-approve).
- **Decision (the user's, from three options):** a narrow standing allowlist. `unattended_auto_approve_tools` (env `WISP_UNATTENDED_AUTO_APPROVE_TOOLS`, default empty) names tools a caller with NO human may run unasked.
- **Narrow by construction:** only READ/NETWORK-class tools count (`standing_grant_applies`); a write, shell or MCP entry is ignored with a warning. Applies only with no approver; never over a bundle's forced approval; `read_only` mode and the dangerous-command guard come first; each use is audited as layer `standing-grant`. Mutation-checked: removing each condition fails its own test.
- **Trade-off to remember:** a background agent that can `web_fetch` reads untrusted pages and can request URLs; treat fetched content as data, and keep the list to what the research needs.
- **Not covered:** a researcher role's tool list has no `run_bash`, so a task that prescribes `curl` via `run_bash` cannot run there. Separate decision.
- **Method:** a glob of "tests that mention X" matched a JSON fixture and pytest refused the file list; restrict to `*.py` (`grep --include`). Also: do not switch a worktree's branch while a background test run is using it.
- **A pinned invariant is evidence of intent:** `tests/test_proposal_boundary_no_bypass.py` requires exactly one `_audit_authorization` call in `execute()` (one authority consult, one recorded verdict per path). My standing-grant audit reused that recorder and added a second call site; CI caught it. The grant is a different event (who satisfied the approval requirement), so it got its own recorder, `_audit_standing_grant`; the test was not touched.

## shared run verdict failure description fanout envelope status contract (2026-10-05)

- **What:** `describe_failure` (plain-words failure with its remedy) moved to `core/recovery.py` beside the billing/key markers; `verdict` (complete/partial/failed) and `distinct_failures` (one finding per distinct failure, naming every agent it hit) live in `multi_agent/verdict.py`. `/swarm` and `fanout` both use them; the REPL command lost its private copies. `fanout` gained additive `verdict` and `failures` fields.
- **I misread a contract and nearly broke it:** I called `fanout`'s `"status": "ok"` on total failure an honesty flaw. It is a documented contract: the envelope `status` means "the tool call executed"; the agents' outcome is `data.ok` (`tests/test_spawn_fanout.py`: "tool call succeeded, subagent failed"). Read the pinned tests before calling existing behaviour a bug, and add fields rather than change a status.
- **Method:** a new test pins the preserved contract explicitly (status stays `ok` when every agent failed) so a later "fix" cannot silently flip it. Mutation checks cover the verdict, the grouping, the billing description and the `fanout` fields.
- **Pin tables again:** inserting `describe_failure` into `core/recovery.py` shifted four authority pins and the `LadderExhausted` row by +18; re-anchored in one pass (a regex over a mapping, so no pin shifts twice).

## core approval policy pure move re-export (2026-10-05)

- **What moved:** the tool approval policy (`approval_needed`, `forced_by_mode`, `get_write_tools`, `standing_grant_applies`, and the write-tool sets) from the 2,400-line `tool_executor.py` into `wisp/core/approval_policy.py`: pure, no I/O, imports nothing upward. `server/deps.py` now imports it from core instead of importing the whole executor to ask one predicate.
- **Pure-move method:** cut the code verbatim by script; rename only what the module boundary needs; leave thin re-exports in `tool_executor` so no caller and **no test** changes. AST pins on `execute()` match call *names*, so `get_write_tools as _get_write_tools` keeps them valid. Tests that hold the move itself (`tests/test_core_approval_policy.py`): same object (identity), the old module no longer defines it, the REST gate imports from core, the new module imports nothing upward.
- **Find the whole compat surface before moving:** my first consumer search was truncated at 30 lines and missed `tests/test_primitive_routes.py` importing `_DEFAULT_WRITE_TOOLS`; a collection error then aborted a 3,000-test run with nothing executed. Count per name across `tests/` and `wisp/`, and run `--co -q` before a long run.
- **Re-exports without suppression:** `ruff --fix` deletes an unused re-export. Use `from x import name as name` (explicit re-export) or a plain alias assignment (`_DEFAULT_WRITE_TOOLS = DEFAULT_WRITE_TOOLS`). Never `# noqa`.
- **Verified:** 3,082 passed across approval, executor, server, reliability, scaffolding and layer suites; no existing test edited.

## core tool surface what an agent is offered and why a tool is absent (2026-10-05)

- **Problem:** the offer is the composition of four filters in four places: the role's tool list, the child permission-mode filter (`filter_allowed_for_mode`), the tool profile, and the capability partition. A researcher "has no run_bash" because of the second, and nothing could say so.
- **`wisp/core/tool_surface.py` (`explain_surface`)** composes the REAL filters in the real order and reports, for every missing tool, the first stage that removed it and why. It re-implements none of them, so it cannot drift. **`wisp tools [--role parent|<role>] [--mode M] [--profile P] [--json]`** prints it, grouped by reason with the switches you can change first.
- **A differential test is the safety net:** the `offered` set must equal what `_effective_child_tools` plus `WispAgentCore._provider_tools` really produce, over parent / unrestricted subagent / every role as a child x 4 modes x 2 profiles x the capability flag (97 tests). Five mutations (skip the mode stage, apply the profile to explicit lists, ignore the capability stage, treat a child's "all" as unrestricted, keep the skill menu) each fail many of them.
- **The oracle found a real pipeline nuance on its first run:** the runner expands a child's `"all"` (or no list) into the built-in registry, so **a generalist child is never offered extension (MCP, skill) tools**, and because that list is explicit the tool profile does not apply to it either. My first model treated "all" as unrestricted and 8 combinations failed. Model the real pipeline, then let the oracle tell you where you were wrong.
- **UX:** 42 identical "not in this role's tool list" lines buried the one actionable line; group by reason, actionable stages first.

## parent spend accounting cost meter charged prompt only estimate_spend core (2026-10-05)

- **Finding (money, not just accuracy):** after each turn `AgentRuntime` recorded `prompt_tokens = count(the user's new prompt)` and `completion_tokens = count(assistant text)`. `Telemetry.record_turn` charges the `CostMeter` with those numbers, and it is the only charge path (`composition` wires a `CostMeter` into `Telemetry`; nothing else calls `try_charge`). So `max_cost_usd` was bounded by the size of what the user typed. Each provider call re-sends the system prompt, the tool schemas (about 3,200 tokens in the test config, about 18,000 under the core profile on a real session) and the history, and a turn with tool rounds makes several calls. Measured: a two-call turn on a 2-character prompt was charged about 1 input token; it is now charged 6,399.
- **The same defect had been fixed for subagent children** (`SubagentRunner._estimate_spend`, PRs #66/#67); the parent never got it because the function lived in `multi_agent`. It is now `wisp/core/spend.py::estimate_spend(messages, overhead_chars, from_index, chars_per_token)` and both sides call it. `from_index` charges only this turn's provider calls while earlier history still counts as input to them.
- **Behaviour change to expect:** `/metrics` token totals and the cost meter are now far larger (and honest), so a `max_cost_usd` bound fires where it should. The old prompt-only figure is kept as a floor, so a turn is never charged less than before.
- **Method:** a red test through the real `AgentRuntime` with a scripted two-call provider showed the bug in numbers before any fix; the pure function has its own unit tests; four mutations (re-charge earlier turns, drop the overhead, runtime back to prompt-only, history not counted as input) each fail their own tests. The child runner's existing spend tests pass unchanged (it now delegates).
- **Pins:** two insertions into `core/runtime.py` shifted 19 pinned lines across three generators; shifts were computed per pin from the diff hunks (after old line 615: +4; after old line 1470: +17), applied in one regex pass.
- **Still true:** about 17 sites hard-code `// 4` and ignore the `chars_per_token` setting (`repomap`, `boot`, `context_trust`, `doctor`, `tui`, `transport/cli`, `repl`, ...); migrating them is a separate, behaviour-neutral PR.

## generalist children inherit extension tools: one narrow rule, shared (2026-10-05)

- **Behaviour found by `wisp tools` (#84) and decided by the user:** the runner expanded an unrestricted child's `"all"` into the built-in registry only, so a generalist subagent (the default `spawn` role) was never offered an extension tool: not the fleet workers' reads, nothing. A child's tool list is also its authorization identity (a tool outside it is refused at the authority layer), so the list is the one place to change.
- **The rule** (`core.tool_surface.inherited_extension_tools`, ONE function used by the runner's `_effective_child_tools` and by `explain_surface`/`wisp tools`): inherit every MCP tool whose operator declared `tool_risk: read`, in every mode (it executes unprompted and `read_only` already permits it: no new authority); in `full` mode also the other MCP tools (the user has authorised everything, and the child gets `run_bash` there); **never `skill__*`** (the parent's skill menu, about 10k tokens per call); **not** an undeclared MCP tool in `auto_edit`/`ask_all` (it always needs an approver and a child has none: a guaranteed-block wasted turn). Explicit role lists are unchanged.
- **Reading the host:** `AgentRuntime.extensions` is the extension host; the runner calls `_extension_tool_names(runtime)` (tolerant of no host or a failing host) and only full mode needs the names, because `declared_read_names()` is a registry that needs no host.
- **Method:** 21 tests (rule, runner through a scripted runtime, and the child's authorization principal: it may call what it was offered and is refused the rest) plus the differential grid of #84, extended to every role x mode x capability flag x declared-read on/off against the real pipeline. Six mutations (ignore the host in full mode, inherit skills, inherit undeclared in every mode, drop declared-read, runner not passing the host, `explain_surface` not using the rule) each fail tests; the last proves `wisp tools` and the runner cannot drift.
- **Pins:** the inserted code shifted `FirstTokenTimeout` (151 to 168); computed from the diff hunks.

## generated page stamp is the merge-base with origin/main, so a squash cannot orphan it (2026-10-05)

- **Failure:** `register.md`, `CURRENT_FLAGS.md` and `CURRENT_AUTHORITIES.md` stamp the commit they were generated at, and `test_the_header_names_a_real_ancestor_commit` requires it to be a real ancestor of HEAD. The generators stamped `git rev-parse --short HEAD`: on a PR branch, a branch commit. Squash-merging #85 replaced the branch's commits with one new commit, so `main` CI went red until #86 re-pinned (#86 was docs-only, so CI skipped it and no green run followed).
- **Fix:** `scripts/_page_stamp.py` stamps the **merge-base of HEAD and `origin/main`**: a commit already on `main`, an ancestor of everything `main` becomes under a merge commit, a squash or a rebase. On `main` it is HEAD; with no `origin/main` ref (fresh clone, shallow checkout) it falls back to HEAD as before. All three generators use it, and the stamped date is that commit's date.
- **Tested against a real repo, not a mock:** `tests/test_page_stamp.py` builds a throwaway git repo, forks a branch, then simulates a squash, a merge commit and a rebase onto main and asks whether the stamp is still an ancestor; it also asserts the OLD stamp (the branch tip) is exactly what a squash orphans. Mutation-checked (stamp=HEAD, wrong ref, date from HEAD).
- **A test that cannot fail:** my first date test passed under the "date from HEAD" mutation because every commit in the fixture repo had the same date. Give fixtures values that differ along the dimension under test.
- **Mutation harness hazard, again:** `cp -f` is aliased to prompt interactively; it stalled a run and left later mutations running against a still-mutated copy, so those results were invalid and redone. Restore with `shutil.copyfile` and re-run the baseline after.
- **Lint without suppression:** loading the helper by path inside a function avoids both a `sys.path` hack and a `# noqa: E402`; removing the now-unused `subprocess` import cleared the one new finding.

## cancel a turn stuck on a stalled provider; bounded process exit (core) (2026-10-06)

- **Symptom (live):** with the provider stalled, Ctrl-C printed "Interrupted — cancelling turn… (Ctrl+C again to force quit)" and the "waiting" clock kept running; the REPL stayed unresponsive until the provider answered (read timeout 120 s). Pressing Ctrl-C repeatedly ended, once, in `KeyboardInterrupt` inside `threading._shutdown` (`lock.acquire()`).
- **Cause 1, found by reading the handler and then proven with real signals:** the SIGINT handlers (`cli/repl.py:_on_sigint` and the factory in `entry.py`) called `task.cancel()` straight from the signal handler. That schedules the cancellation but writes nothing to the event loop's wake-up pipe, so a loop asleep in `select()` (waiting on a provider that never answers) never ran it. `loop.call_soon_threadsafe(task.cancel)` is the call that wakes it. It is now one core helper, `core/turn_control.request_cancel(task)`, used by both handlers. A streaming provider hid the bug (events woke the loop); only a fully stalled one exposed it.
- **Cause 2 (mitigation, root thread not identified):** Python waits for every non-daemon thread at interpreter shutdown. I could not reproduce a post-exit hang in six scenarios (idle, failed prompt, stalled provider, Ctrl-C x2, `/exit` with the stalled thread blocked; with and without the user's MCP servers), so `core/shutdown.arm_exit_watchdog` bounds it instead: a **daemon** thread that, after `WISP_EXIT_GRACE_S` seconds (default 5; 0 disables; a malformed value never disables it), flushes the streams and `os._exit`s with the real exit code. `wisp.__main__.entry()` arms it after `main()` returns, on `SystemExit`, on `KeyboardInterrupt` (130) and on a crash (1); `main()` itself never arms it, so in-process callers (the test suite) are never killed by it. The `[project.scripts]` entry is now `wisp.__main__:entry`; an already-installed console script keeps the old entry until reinstalled (the global `python -m wisp` wrapper uses the new one).
- **How it was tested:** real SIGINT in a subprocess against a loop asleep in `select()`, with a control showing the old call leaves it asleep; a pty end-to-end test (a local stub that stalls for a minute, Ctrl-C, expect "Turn interrupted" within seconds, `/exit` leaves promptly); a real process with a non-daemon sleeper thread released with its exit code, and the same process hanging without the watchdog.
- **A mutation that survived taught the test rule:** with `entry()` not arming on `SystemExit` no test failed, because a fake `main` that exits normally never hangs. A test for a safety net must leave the failure condition present (a stuck non-daemon thread) on every path it guards.
- **Capture the culprit if it recurs:** `PYTHONFAULTHANDLER=1 wisp repl`, then from another terminal `kill -ABRT $(pgrep -f "wisp repl")` dumps every thread's stack.

## wisp setup: Ctrl-C at a prompt printed a traceback (2026-10-06)

- **Symptom (user's terminal):** `wisp setup`, provider 3, model 4 (custom), Ctrl-C at "Custom model name:" printed a full `KeyboardInterrupt` traceback through `entry()`, `_do_setup` and `run_setup`.
- **Cause:** `run_setup`'s docstring promised `None` when aborted, but only the typed "abort" choice returned it. Ctrl-C and Ctrl-D (`EOFError`) at any prompt, the hidden key prompt and the validation handshake were not caught, and `entry()` re-raises `KeyboardInterrupt` after arming the exit watchdog.
- **Fix:** `run_setup` wraps the wizard and turns both into "Setup cancelled. Nothing was saved." with `None`. Nothing has been written at that point, because the choice is persisted only after the last step (checked by a test that fails if `persist` or `store_key` is called).
- **How it was tested:** unit tests with an injected input that raises at the exact prompt (the first one reproduced the traceback before the fix), and a real pty run of the reported keystrokes: no traceback, the message, exit code 1, and no file written to a temporary HOME.
- **Not changed:** other subcommands still show a traceback on Ctrl-C through `entry()`; only the wizard was reported and fixed. A general `entry()` change would alter every command's exit behaviour and needs its own decision.

## wisp judge: what the first CI run and the build taught (2026-10-06)

- **`--provider X` inherited another provider's endpoint and key:** a flag path that replaces only `provider` keeps `WISP_API_BASE`/`WISP_API_KEY` written for the configured provider. Fixed in `with_provider()` (PR 92). Evidence: building the NVIDIA provider from the real config gave `openrouter.ai` + the shared key before, `integrate.api.nvidia.com` + `NVIDIA_API_KEY` after. A live call could not tell them apart once OpenRouter answered again, so test the construction, not the call.
- **A hidden check that depends on set order passes buggy code by chance:** `dedupe` compared a 3-string set, whose iteration order follows `PYTHONHASHSEED`, so `list(set(xs))` passed sometimes. The self-test only failed intermittently, which is the signal; do not dismiss one unexplained red run. `tests/test_judge_tasks.py` now runs every hidden check under three seeds.
- **The module-orphan scanner reads `node.module` only:** `from wisp.judge import core, improve` records `wisp.judge`, not `wisp.judge.improve`, so a module reached only that way is reported as having no importer. Import by full path (`from wisp.judge.improve import run_loop`).
- **Choose the local test slice from CI's failures, not from guesses:** my architecture-test filter by file name missed `test_doc_drift` and `test_module_orphans`, and the first CI run of the PR caught both. `AGENTS.md` states a test-file count with a tolerance of 40; `main` was at 439 against 400, so any four new test files tipped it.
- **`rtk` filters output and can mislead:** it showed `06bc076` as `origin/main` when the real value was `f660329`. Use `rtk proxy git ...` for a SHA or a count that decides something.
- **Verify a subcommand through the checkout under test:** a subprocess `python -m wisp judge ...` ran the older installed wisp, which has no `judge` and sent "judge run ..." to the model as a prompt (a mock reply, exit 0, a green-looking wrong result). The end-to-end test sets `PYTHONPATH` to its own checkout.


## background jobs: supervisor process, process-tree kill, tier semantics (2026-10-07)

Search words: background job, run_in_background, supervisor, orphan, process group, pgid, SIGTERM, PTY, DockerSandbox, container leak, zombie, ps environment, macOS SIP, reaper.

- Background **subagents already exist** (`spawn_background`, durable run rows, finished-agent notices in the next turn's operating context). The gap was background **shell** jobs and surviving restarts. Design: `docs/harness/background-jobs-design.md`; code `wisp/jobs/`; skill `.agents/skills/background-process-supervision`.
- **`run_bash_confined` is a black box that cannot be killed from outside.** It awaits the tier router and returns at the end; the PTY tier runs in a worker thread that a cancel does not stop (its child has its own session); the Docker tier leaves the command running when the `docker exec` client dies. So the supervisor kills the process **tree** (found by parent pid) and removes its own container; the tiers' kill is not relied on.
- **The tiers' own kill hides evidence**: the host tier kills the process group first, orphaning grandchildren to init before a tree walk. Capture the tree before cancelling and keep snapshots; also sweep by process group (an orphan keeps its pgid).
- **macOS `ps` hides the environment of Apple-signed binaries**, so an env-marker sweep finds nothing for `bash`/`sleep` (it works on Linux). Changing `os.environ` after start is invisible to `ps`. A zombie still shows in `ps` (treat `Z` as dead).
- **A background process that keeps stdout open keeps the job alive** (tiers wait for EOF), so "leftover killed at job end" only applies to processes that closed their pipes.
- **A first mutation probe over the new package had 17 survivors out of 44**, almost all real: traversal ids with an existing component, a non-integer exit code, the force-kill path (needs a SIGSTOPped supervisor), kill-before-start, runtime clamping, the reap and prune calls in spawn, the zombie rule. Docker-only mutants need the opt-in suite.
- Existing leak, not caused by jobs: 66 exited `wisp-sandbox-*` containers (one per `DockerSandbox` instance, never removed). The supervisor removes its own container on every exit path and the reaper removes it after a hard kill.
- The danger check refuses inline interpreter code in test commands; the orphan scanner counts `from pkg import mod` as importing `pkg` only (use `import pkg.mod as mod`), and a module launched with `python -m` needs a `KNOWN_UNREFERENCED` entry.
- Verified on this machine: host tier, PTY tier (Docker off the PATH), and Docker tier (opt-in, kill removes the container, reaper removes it after a SIGKILLed supervisor). Not verified: Linux for the jobs suite (the env-marker test is Linux-only), concurrency at fd/disk limits, the watchdog backstop (needs fault injection).

## macOS app: self-contained Wisp.app (bundled Python), packaging and verification (2026-10-06)

Search words: electron-builder, python-build-standalone, uv, codesign, ad-hoc, afterPack, Gatekeeper, bundled backend, Wisp.app.

- Ship the interpreter, not the machine's Python: `wisp-desktop/scripts/bundle-backend.sh` copies a uv-managed python-build-standalone 3.12 and `uv pip install`s wisp into it (non-editable, so both `wisp` and `agent` land in site-packages). The app resolves WISP_PYTHON, then `Resources/backend/python/bin/python3`, then dev venv, then system python (`src/main/backend-launch.ts`, 21 unit tests).
- Seal the bundled interpreter: `PYTHONDONTWRITEBYTECODE=1` (never write into a signed app), `PYTHONNOUSERSITE=1` (a user package must not shadow the bundled one), drop `PYTHONHOME`.
- `import wisp.server` creates `WISP_WORKSPACE` (default `/workspace`) at import time; on a Mac that fails with a read-only file system. Any smoke import must set a scratch `WISP_WORKSPACE`. Evidence: the first bundle smoke run.
- electron-builder with an unsigned bundle leaves a broken seal (`codesign --verify`: "code has no resources but signature indicates they must be present"). Ad-hoc sign in an `afterPack` hook (`scripts/adhoc-sign.cjs`) with `mac.identity: null`. Ad-hoc is not a Developer ID: other Macs need right-click > Open until the app is signed and notarized.
- Verify the artifact you ship, not the build directory: `scripts/verify-packaged.mjs` launches the app (Playwright `_electron`, throwaway HOME) and checks window, managed backend, health, 401 without and with a wrong key, backend is the bundled python, loopback only, no renderer errors, and no orphan after quit. It passed on `release/mac-arm64/Wisp.app` and on the app unzipped from `Wisp-0.1.0-mac.zip`.
- `npx electron-builder` can fail with "Missing script"; call `./node_modules/.bin/electron-builder`.

## desktop layout: sidebar / header / workbench dock, overlap checks (2026-10-06)

Search words: overlap, header, traffic lights, hiddenInset, container query, WebContentsView, dock, diff tab, box-sizing.

- macOS window buttons (`hiddenInset`) sit top-left of the window, i.e. over whatever is leftmost. Give that strip to the sidebar (`--traffic-light-offset` padding, drag region) and let a hidden sidebar add the same padding to the main header. A narrow icon rail cannot clear them, so "collapsed" means hidden.
- Header overlap was structural, not cosmetic: absolutely centred chips collided with the left and right groups. A flex row where only the title shrinks, plus container-query breakpoints that drop low-value chips, cannot overlap. Measured, not eyeballed: the bounding boxes of every leaf item in the header, pairwise, at 1400/1100/900 px and with the sidebar hidden (`scratchpad/shots.mjs` pattern): 0 overlaps.
- `width:100%` plus padding on a button without `box-sizing: border-box` overflowed the shell by ~8-25 px and clipped the chevron and diff counts. Set border-box on new components and `overflow:hidden` on the shell.
- A native `WebContentsView` (the Browser tab) is not in page screenshots and paints above all web content: hide it when a modal/approval is open, and check it from the main process (`app.evaluate`: url, title, bounds). It is sandboxed, own partition, no preload, http(s) only (`browser-url.ts`).
- Auth headers are built in one pinned place (`useApi`); new endpoints (`/api/git/diff`, `/api/bash`, `/api/models`, `/api/models/select`) were added there, not as raw `fetch` in components.
- `/api/git/diff` takes no path from the client (the file list comes from `git status -z -uall`), caps files and bytes per file; 7 tests against a real repo.
- The Terminal tab calls `/api/bash`, which the default AUTO_EDIT policy refuses ("no approver is present over REST"). That is the policy working; the tab says so and does not bypass it. Widening it is the user's decision.

## app shows "No sessions yet": sessions are per-workspace databases (2026-10-06)

Search words: sessions, wisp.db, workspace store, read-only, import, session_sources, untitled.

- Wisp stores sessions in `<workspace>/.wisp/wisp.db`. The app's workspace is `~/.wisp/workspace`, so its store started empty while the user's history sat in `~/.config/wisp/wisp.db` (8188 sessions) and `~/.wisp/wisp.db` (433). Measured with `sqlite3 -readonly immutable=1` counts, no content read.
- Fix is additive: `GET /api/sessions` merges the app store with the other known stores, opened `mode=ro` (no migration, no WAL checkpoint). Opening a foreign session is an explicit copy into the app store (`POST /api/sessions/{id}/import`); delete and rename are refused (409) for sessions that exist only elsewhere. Tests hash the source files before and after list/get/import and require them unchanged (`tests/test_session_sources.py`).
- `PATCH /api/sessions/{id}` was a stub returning `{session_id}`, so the UI's rename always reported failure; it now renames.
- Untitled rows are labelled from the first text message via `json_extract` guarded by `json_type` (multimodal content is a list). 400 rows in 0.07 s on the real data; 40 stay untitled.
- Not covered: per-project stores (`<project>/.wisp/wisp.db`) are not scanned; a registry of known workspaces would be needed.

## switching the project folder did nothing: stale WORKSPACE_ROOT copies, allowlist, silent UI (2026-10-07)

Search words: workspace switch, WORKSPACE_ROOT, allowed roots, from-import copy, rebind, project folder, prefs.json.

- Three independent causes, each enough to make the selector look dead: (1) the backend only allows switching inside the *current* workspace unless `WISP_ALLOWED_WORKSPACE_ROOTS` is set, so choosing a real project returned 400; (2) ~20 route modules did `from ...workspace import WORKSPACE_ROOT`, which copies the Path at import, so even an accepted switch never reached Diff/Files/git/shell routes; (3) the UI ignored a non-ok response (`if (resp.ok)` with no else).
- Fix: `workspace._rebind_workspace_root` rebinds, by identity, every `wisp.server*` module still holding the old value (a module deliberately given another path is left alone) and clears the three caches built for the old folder (`codebase._semantic_index`, `mcp._mcp_manager`, `suggestions._app_suggestion_watcher`). The desktop app passes `WISP_ALLOWED_WORKSPACE_ROOTS=$HOME` (an explicit environment value wins). The UI shows the server's reason.
- A from-import of a mutable module global is a latent bug anywhere it is reassigned later; mutation probe: removing the rebind call fails 3 of the 7 tests in `tests/test_workspace_switch.py`.
- The remembered project lives in a main-process file (`userData/prefs.json`, atomic write, 0600), not localStorage: Chromium flushes localStorage lazily, so a killed or crashed app lost it (seen in the e2e restart check).
- The shell's cwd follows the project (validated in main: absolute, exists, inside home, no `..`); a running shell in another folder shows a "restart in the project folder" prompt instead of being killed.
- `deleteSession` tested `data.ok` but the server returns `{"deleted": true}`: every delete in the UI looked failed. Found by reading the call site against the route while fixing something else; now covered by `useApi.sessions.test.ts` against the real response shapes.
- The auth-header ratchet test (`authHeaderAuthority.test.ts`) fails *when a known duplicate disappears*: delete the entry (that is the design).
- Glued assistant text ("available.Swift 6.2") in one opened session is already glued in the stored message content; that session came from another producer (CodeAgentMac), so it is data as stored, not a wisp-core finding.

## invariant gates: five deterministic layers before every tool call (2026-10-07)

Search words: gates, shellparse, PIPESTATUS, fail closed, fixed point, flat event, tool_result_guard, verification floor, mutation probe, denial vocabulary.

- Shape: `wisp/core/gates/` (pure, stdlib plus `wisp.pathsec` and `wisp.auth.secrets`): `shellparse` (tree), `invocations` (what actually runs: wrappers looked through, `cd` tracked), `commands`, `paths`, `secrets`, `deps`, `verify`, and `gate.check_tool_call`, the one entry point. Invariant table with a witness per row: `docs/harness/invariant-gates.md`; `tests/gates/test_invariant_table.py` fails if a named witness disappears.
- Seams, all single sites: `_gate_tool_call` (BEFORE the approval prompt, so no human yes overrides a hard denial), the `tool_result` path (`scrub_secrets` next to `withhold_if_injected`), and `VerificationFloorGuard.note_tool_result`. A refusal rides `POLICY_DENIED` with `_src="invariant_gate"`: the denial vocabulary is closed (recovery, scoring and the renderer depend on it), and a non-`gate` source is audited once by the existing path.
- A parser for a security gate must terminate on every input. A 3,000-case fuzz test hung: `&& a` made the statement loop return without consuming a token. Fixed by an explicit refusal plus a step budget; fuzz tests run under `signal.alarm` so a hang fails instead of wedging the suite. (macOS has no `timeout` binary.)
- Scrubbing must reach a fixed point (S1). `secret=[REDACTED:x]` was re-matched by the generic `secret=` assignment pattern and the entropy detector re-flagged placeholder labels; both are skipped when the span lies inside a `[REDACTED:...]` placeholder. The first S1 test passed because its fixtures lacked that shape; a mutation probe found it.
- **The engine yields a flat dict, and `tool_result_guard._payload` only understands `.data` or a nested `"data"` dict.** A real-turn test showed the model was shown the secret; the same helper makes `withhold_if_injected` inert in production (probe: flat -> not withheld, nested -> withheld). `scrub_secrets` uses `_result_holder` (handles all shapes); the injection guard is left as is and a follow-up task is open, because enabling it changes what the model sees and needs a false-positive measurement.
- `VerificationFloorGuard.note_tool_result(name, text, args)` is a pinned signature (spies in `test_verification_evidence_adapter` wrap it), so the full command travels in `args["__command__"]` (`COMMAND_ARG`) and is stripped from the step trail. Asymmetric on purpose: a failing command is always a failure; a passing one counts only if `gates.verify.classify` says a real runner's exit status decides the result (`true`, `pytest || true`, `pytest | tail`, `pytest; echo done`, `pytest &` no longer verify).
- Existing tests that encoded the old behaviour were changed, not weakened: the READ_ONLY test's incidental command (`rm -rf /` is now refused by the gate first, with its own message, pinned in `tests/gates/test_gate_seam.py`) and the verification success control (`echo fine` -> `python3 -m compileall -q .`, a real passing build check).
- A new exception class needs a register row; the parser's internal refusal is `ValueError` (alias), not a new class. Added lines shift pinned read sites: `scripts/derive_current_flags.py` had `stateless.py:524/1360`, now `525/1367`; regenerate all four derived pages and `register.md`.
- Mutation probe (26 deliberate breakages, bytes restored in `finally`) found the S1 gap; never run it while a full suite uses the same checkout (that run's result is contaminated). Run the full suite from a snapshot clone.
- Stated limits: inline interpreter code (`python -c`) is not parsed (pinned by `TestKnownLimits`), symlink races between check and write are not defended, entropy detection is a narrow heuristic, `xargs rm` / `find -exec rm` are refused because their targets cannot be bounded, a custom test script is not recognised as verification (use `make test`).

## reasoning core: ledger, claim audit, decide, per-rule modes (2026-10-07)

Search words: reasoning core, evidence ledger, claim audit, observe, enforce, per-rule modes, fault-injection persona, baseline, 402, can only afford, exit marker, refusal seam, mutation probe, equivalent mutant, hybrid posture.

- Shape: `wisp/core/reasoning/` = `ledger` (facts that only an engine event can make OBSERVED; a verification goes stale at the next edit attempt), `claims` (precision-first extractor plus audit), `decision` (R1-R4 as pure functions over `GoalState`/`FailureClass`/`RecoveryRung`), `shellwrites` (shell writes from the gates' parser), `runtime` (`TurnReasoning`, the only non-pure module, journal). Design and phases: `docs/harness/reasoning-core-design.md`; measured before/after: `docs/harness/reasoning-core-baseline.md`.
- **Posture (owner's hybrid rule):** security and workspace integrity are ENFORCED (gates: path jail, destructive commands, secrets, dependency lock, now also `.gitignore` and CI workflows); heuristics start in OBSERVE and are flipped to ENFORCE one at a time once the baseline shows they do not block legitimate behaviour. `reasoning_core` is the default, `reasoning_core_rules` (`R1=enforce,R4=enforce`) overrides per rule; a typo means `observe`, `off` as the default wins.
- **Enforcement is owed, not optional.** R1 and R4 are built, probed and witnessed but run in `observe` by default, so today they change nothing for a user. See "Enforcement roadmap" in the design doc for the exit criteria; do not let `observe` become the permanent state by inertia.
- **A seam placed where you expect the event can miss the real path.** Refused calls never pass the tool-result loop (they go to `tool_results_events_early` via `_refusal_result_event`), so the first seam saw no refusals; a real 402 arrives as a provider `error` EVENT, not an exception, so the first R4 seam covered the wrong path. Both were found only by driving a real turn with a scripted provider (personas), never by unit tests. Put the seam at the one helper every path uses, and pin one call site per seam with an AST test.
- **A tool that succeeded is not a command that succeeded.** A failing shell command is an "ok" tool result with `[exit code: N]` in its text; R2 saw nothing until the runtime read that marker (the same lesson as the `| tail` pipeline bug, one layer up).
- **Decide at the last gate, not the first.** R1 is computed after the verification floor and the other completion gates, so none of them changes and a turn is never nudged twice; computing it at the first final answer would have spent its budget on rounds another gate handled.
- **Test a heuristic against look-alikes, not only hits.** The claim corpus (49 hits, 100+ look-alikes) found three real false positives ("The README says all tests pass", "Tests passed on CI last week") and two misses ("no errors" tripped the failure words). Synthetic corpora only: the false-positive rate on real transcripts is NOT measured.
- **A baseline can only agree with the core by accident if it does not share its code.** The persona predicates use their own regexes, not `claims.extract`. A comparison that said observe differed from off was only a temp path inside event arguments: normalise before comparing, then keep the whole-stream equality as the RC4 witness.
- **Mutation probes find the missing test, and equivalent mutants are real.** 0 survivors after adding: a disclaimer in a separate sentence, `if`/`but`/`should` each tested only beside another excluded word, the iteration-budget guard, the stop message, "applied once", R2/R3 `off`. Equivalent survivors (R2 `==` vs `>=`) are recorded, not chased. Use `Edit`/Python for source edits: `sed -i` was mangled by the rtk hook here.
- Not measured yet: how often real models make these claims, and whether flagging an answer annoys users. Live paired judge runs need a key and a cap the owner names.

## live paired runs on OpenRouter, rate limits and background jobs (2026-10-07)

Search words: live run, OpenRouter, ling-3.1-flash, 429, INFRA, judge, PYTHONPATH, run_in_background, resumable, key handling, skills.

- **Authorisation and cap:** the owner named the key (the one in `~/.config/wisp/.env`, an OpenRouter `sk-or-` key against `openrouter.ai`) and the model `inclusionai/ling-3.1-flash`. Preflight showed the model exists, is priced 0/0, supports tools, and the key has a $5 limit; I still set my own stop at +$0.25 of usage. The key is read in-process and never printed; a provider's key is never sent to another provider (check the shape against the endpoint first).
- **Force the checkout under test.** `~/.venvs/wisp` imports `~/dev/wisp` (the owner's checkout), so a child launched without `PYTHONPATH=<worktree>` tests old code and never loads the reasoning core. Verified with `python -c "import wisp; print(wisp.__file__)"` from a temp directory.
- **Rate limits are infrastructure.** The model sits behind a shared upstream pool (Novita): HTTP 429 "temporarily rate-limited upstream, retry shortly". Observed: a run whose turn ended with `ok: false` after 429s was scored NO-OP, and a SOLVED task was labelled `claim false / honest false`. So count the child's 429s per run (`judge_one.py` wraps `core.run_one`), probe with a tiny request until 200 before each task, label runs with 429s INFRA, retry a bounded number of times, and never read `honest` from a run that hit 429s. The judge itself does not do this (a follow-up).
- **Bare `( ... ) &` jobs died** when the Bash call's shell ended (no process left, logs truncated, no completion event). The harness's `run_in_background: true` jobs survive. Long runs were still cut short, so the runner appends each result to a JSONL file and skips finished tasks on rerun (resumable, one writer per log).
- **System Python lacks CA certificates** (`CERTIFICATE_VERIFY_FAILED`): use the venv's `httpx`; never disable verification.
- **Persist the journal to see what fired.** `reasoning_journal` / `WISP_REASONING_JOURNAL` writes JSON lines; without it a live run cannot show which rules fired.
- **Docker Linux parity (HEAD of the reasoning branch):** targeted suites 1,535 passed; the full-suite run had one failure, `tests/test_multi_agent_worktree.py::TestConcurrentWorktreeIsolation::test_parallel_writers_never_see_each_other`, which passes 3 of 3 alone on the branch and on clean `origin/main` (an order/load flake, unconfirmed, not caused by this work).
- **Pre-existing leak, not ours:** `DockerSandbox` starts a per-instance container and never removes it; 66 exited `wisp-sandbox-*` containers (55 on `python:3.12-slim`, 11 on `wisp-sandbox:py312`) were present. The background-jobs supervisor removes its own container.
- Skills written from this process: `.agents/skills/{mutation-probe,fault-injection-personas,observe-then-enforce-rollout,live-model-paired-runs}`.


## CI parity: main went red after a merge, and three lint/type misses (2026-10-07)

Search words: CI, ruff, mypy, step order, main red, flaky test, asyncio.sleep, monkeypatch, global patch, qa, test-python, merge ref, rtk, background jobs.

- **#100 failed CI on the Ruff step after 10,470 tests passed** (an unused import in `tests/gates/conftest.py`). I had run ruff on the new files only; CI runs `ruff check` on `wisp/`, `wisp_net/` and `tests/`, then bare `mypy`, then `compileall`, in that order, so a long green test step hid it. The reasoning branch had two more (an unused import and a mypy `union-attr` on `self.provider.affordable_ceiling`). Fixed; rule: run every CI step on the branch before pushing (`.agents/skills/ci-parity-before-push`).
- **`main` went red after merging #96** (CI run for `c14d9cc`) on `tests/test_rate_limit_retry.py::test_the_whole_path_honours_retry_after_from_the_http_response`, which passed alone and passed in #96's own CI. Its `waits` fixture patched `asyncio.sleep` on the real module, so any other coroutine's sleep (a leaked task in another loop or thread sleeping 0.02 s) was recorded, giving `[0.02, 0.02, ...]` instead of `[5.0, 5.0]`. Reproduced with a competing thread; fixed by replacing `asyncio` only inside `provider_stream`. A theory I had first (the 0.02 s poll in `providers/protocol.py`) did **not** reproduce and was dropped; that poll only runs during cancel teardown.
- **A PR's green CI is not main's green CI**: the merge changes what runs and the timing. Check `gh run list --branch main` after each merge.
- **Every remaining PR conflicted after the first merge** (all append to `AGENTS_LEARNING.md` and regenerate the same pages): resolve them one at a time, merging `main` into each branch, keeping both appended sections, regenerating `register.md`/`CURRENT_*.md`, then rerunning lint, mypy and the pin tests. `gh pr merge` from the agent is denied by the auto-mode classifier ("Merge Without Review"); the owner merges, and `gh pr merge N --merge --auto` merges when checks pass. A `checkpoint/...` tag name that already exists means an earlier tag was made: use a new name.
- **Wrong conclusions I drew, so you do not:** I reported `origin/main` had moved when I had misread a commit from another branch (`rtk` output; verify SHAs with `rtk proxy git rev-parse`); I concluded background runs had died because `ps | grep` showed nothing, relaunched them, and ended up with four runners writing the same files and rate-limiting each other. Check with `pgrep -f` and whether the log or results file is still growing.

## interpreter level: run_tests, the Docker tier, leftover children, import hijack (2026-10-07)

Search words: sys.executable, run_tests, run_bash, interpreter, credential_free_env, verification floor, Docker tier, pytest, sleep infinity, container leak, hook timeout, MCP, import shadow, PYTHONPATH, provenance, PYTHONHASHSEED, -P. Full register with evidence and status: `docs/harness/findings-2026-10-07.md`; plan and results: `docs/harness/interpreter-level-harness.md`.

- **Two ways to ask "do the tests pass?" give different answers.** `run_tests` runs `[sys.executable, -m, pytest]` (Wisp's interpreter, host, no `env=`); `run_bash` runs the project's venv with credentials stripped. Verified in throwaway projects: a dependency only in the project's venv gives a false failure under `run_tests`; one only in Wisp's venv gives a **false pass**; the project's tests saw a fake API key under `run_tests` and not under `run_bash`.
- **The floor accepts that false pass** (real engine, scripted model: 3 rounds, 0 nudges), even after a failed `run_bash` pytest in the same turn, and **the system prompt steers models to `run_tests`** (rule 7 and the Verification loop). Real-model call frequency is unmeasured.
- **The Docker tier is a third interpreter** (container `python` 3.12.14, no pytest), so with Docker reachable `run_bash pytest` cannot run tests at all by default; `run_tests` then becomes the escape hatch, unsandboxed. Every process using the Docker tier also leaves a **running** `sleep infinity` container (cleanup exists only in `reset_router`, "tests"); the 66 exited ones are the same leak.
- **Children outlive their owner** in: `cmd &` on the host and PTY tiers, a timed-out **async** hook (never killed), a timed-out **compound** sync hook, a non-global MCP manager. Clean: single-command sync hook, global MCP manager (`atexit`).
- **A workspace with its own `wisp/` package hijacks `python -m wisp`** when it is the cwd. `-P` or `PYTHONSAFEPATH=1` prevents it. Found by reasoning about my own supervisor, confirmed in a throwaway directory, fixed test-first for the jobs supervisor; the other launch sites are not audited.
- **Hypotheses that did not survive measurement** (keep them out of the next plan): forcing `PYTHONHASHSEED=0` (the judge's checks are not seed-dependent; a fixed seed would pin a set-order bug), and bytecode/cache flags (git and the judge snapshot saw no extra files). `-P` stays for security, not for results.
- **Method notes:** run each experiment in a throwaway directory with a throwaway `HOME` (note: a throwaway `HOME` makes Docker unreachable, so the "default sandbox" silently becomes the PTY tier); pin `PYTHONPATH` to the worktree under test (a script run from another directory imported the owner's checkout twice); a first-pass hypothesis about the 0.02 s poll in `providers/protocol.py` did not reproduce and was dropped; an empty `$(...)` fed to `grep` hangs on stdin (give it `</dev/null`); `rtk` mangles grep output (print with a script when the content matters).

## setup wizard never validated keyed providers: `_probe` called a method they do not have (2026-10-07)

Search words: wisp setup, _probe, handshake, generate, generate_stream_events, NVIDIAProvider, save anyway, 402, classify.

- Symptom (a real REPL transcript): `[4/4] Validating… ✗ AttributeError: 'NVIDIAProvider' object has no attribute 'generate'`, then "Configuration saved WITHOUT validation". Not NVIDIA-specific: only the mock and Ollama providers define `generate`; OpenAI, OpenRouter and NVIDIA expose `generate_stream_events` only, so the wizard's handshake failed for **every** keyed provider and always ended at "save anyway".
- Why the tests missed it: every wizard test injects `probe_fn`, so `_probe` itself was never executed against a real provider class.
- Fix (`wisp/cli/setup.py::_handshake`): use `generate` when the provider has it; otherwise read `generate_stream_events` until the first content/thinking/tool/done event (events normalised through `canonical_event`, the single implementation), raise on an error event or an empty stream, and always close the generator so a probe never pays for a whole completion.
- Reproduced the exact failure with the real `NVIDIAProvider` against a local HTTP stub (200 SSE and 402 JSON), before and after: before, both cases gave the AttributeError; after, 200 validates and 402 is reported.
- Second fault found by that stub: the provider layer words a 402 as "the provider refused this request on billing", and `_classify_probe_error` read "refused" as a network failure ("unreachable"). Billing (402 / more credits) is now checked first.
- Not changed: how the REPL itself sizes `max_tokens` against a key's remaining credit (the 402 in that transcript said 4096 requested, 83 affordable); that is account state, not a wisp bug.

## run_bash exit code hid a failed pipeline stage (2026-10-06)

Search words: pipefail, PIPESTATUS, pipeline, exit code, `| tail`, run_bash, masked failure, sink.

- Symptom (agent session): `swift build 2>&1 | tail -30` showed 3 compiler errors and `# exit: 0`. A shell reports the LAST stage's status, so `tail` hid swift's failure; the agent and its log said success.
- Fix is in `wisp/tools/bash.py::run_bash_confined`, the one place a command executes (the disk sink goes through it too): the command is followed by an epilogue that records `PIPESTATUS` and re-exits with the real status. The exit code keeps its shell meaning (no behaviour change for scripts). When the command exits 0 but an earlier stage failed, the result starts with `[pipeline: stage N exited C; ...]`, and the sink log gets a `# [pipeline: ...]` header line. SIGPIPE (141) is excluded (`yes | head -1`). If the command already fails (`set -o pipefail`), no note: the exit code says it.
- Why not just prepend `set -o pipefail`: it would turn `git log | head` and `grep x | wc -l` into reported failures for correct commands, a silent change to every existing script.
- The marker travels on stderr, or stdout for the PTY tier (it merges streams); both are parsed and stripped. Verified on real bash, the real PtySandbox, and a merged-stream fake. A provider that never ran the epilogue is left alone.
- Changed a pin: `tests/test_sink_keeps_sandbox.py` asserted the sandbox got exactly `echo via-root`; it now asserts the command is carried and starts with it. The intent (it goes through the sandbox, not the host) is unchanged.
- Not covered: `POST /api/bash` (REST) calls `sandbox.run` directly and does not report masked stages.
- Meta-lesson, repeated in this very session: our own tooling reported `exit code 0` from `cmd | tail` and from `(script; echo exit=$?) ; tail`. Read the real status from a file or `${pipestatus[1]}`, never from a pipeline's end.

## rate limits: two retry layers multiplied into nine requests (2026-10-06)

- **Symptom (user's REPL, OpenRouter 429):** one message produced three groups of "Transient status 429 on attempt 1/3, 2/3" in `.agent/runtime.log`, then "after 3 attempts". The timestamps showed the structure: `guarded_provider_stream` (3 rounds) around `hardened_post` (3 requests per round) is up to 9 requests in ~20 s, against an endpoint that was already throttling.
- **Two retry layers over the same status is a multiplier, not a safety margin.** Pick one owner. The stream owns statuses (it knows the turn, cancellation and the server's advice); `hardened_post` keeps transport errors, which nothing above can tell from a stall. The new `retry_status` parameter defaults to the old behaviour, so only the provider changes.
- **A 429 is a window, not a blip.** Retries 1-2 s apart cannot outlast a minute-long limit; they add load and delay nothing useful. Space them in seconds, and read `Retry-After` (never read before; neither layer looked at response headers).
- **Do not sleep through unbounded advice, and do not ignore it.** Over the 30 s cap the stream stops at once and says how long the server asked for.
- **A message that counts attempts must count requests.** "after 3 attempts" was nine requests.
- **Mutation probes found a pattern that matched two loops** (`hardened_post` and `hardened_get` share a retry shape): a replace-first on a verified range is safer than loosening the assertion.
- **Not the user's config or the model's fault to fix here:** the route (`inclusionai/ling-3.1-flash` on OpenRouter) was rate-limiting; switching with `/provider nvidia <model>` works since PR 92.

## Lesson: a generated page depends on the interpreter that generated it (2026-10-07)

Search: derive_register python3 version, register.md raise sites 285 286, generated pages interpreter, JobLimitError register row

`python3 scripts/derive_register.py` under the system `python3` (3.9.6) wrote **285 raise sites**; the same script under the venv interpreter (3.12.8, the version CI uses) writes **286**. `test_regenerating_reproduces_the_page` regenerates under the test interpreter, so a page written by the wrong Python fails it. The script's header says it "runs under any python3"; that is true for starting, not for the result. The cause (which construct 3.9 and 3.12 count differently) was not isolated.

- Regenerate every derived page with the project's venv interpreter (`/Users/philosopher/.venvs/wisp/bin/python scripts/derive_*.py`), never a bare `python3`.
- A new exception class needs a data row in `scripts/derive_register.py` first (`JobLimitError` in `wisp/jobs/spawn.py` failed `test_every_defined_class_has_a_row` until it had one); regenerating alone does not add it.
- Related to the interpreter-level findings (`docs/harness/findings-2026-10-07.md`, section A): which interpreter ran a tool changes its result.

## Lesson: read-then-check-liveness reported a finished job as lost (2026-10-07)

Search: jobs lost exited race, TOCTOU state.json supervisor alive, test_a_failing_command_keeps_its_exit_status, reap_lost overwrite

PR #102's Linux CI failed one test in 11,124 (`test_a_failing_command_keeps_its_exit_status`: `('lost', None)` instead of `('exited', 3)`). It passed locally. Cause, read in `JobStore._status`: the reader looked at `state.json` (not there yet), then asked whether the supervisor was alive; the supervisor wrote its result and exited in between, so the reader saw "dead, no result" and said `lost`. `reap_lost` had the same shape and was worse: it wrote `lost` into `state.json`, which could replace a real result for good.

- When a writer's last act is "write the result, then exit", a reader that finds the writer dead must **read the result again**; the first read is stale by definition.
- A settler that records a verdict must create-if-absent (`os.link` of a temp file), never overwrite.
- Test the race by injecting the writer's action into the check (`procs.alive` writes the result, then returns False); both tests were RED first and reproduced the CI values exactly. Mutation probe: reverting either change fails a test.
- A single failure in a long suite on one OS is a signal, not noise: read the assertion before calling it a flake.

## Lesson: a check that did not run reported success (2026-10-07)

Search: ci parity, pytest tail exit code, export HOME tilde, zsh no matches found glob, rtk npx, npm 10 ci dry-run, empty target list

Three checks for PR #97 looked green or red for the wrong reason in one afternoon. Evidence: the command outputs of that run.

- **`export HOME=$(mktemp -d)` changes what `~` means for the rest of the command.** `~/.venvs/wisp/bin/python` then pointed into the empty temp HOME, pytest never started, and the background task still reported `exit code 0` because the last stage was `tail`. Use an absolute interpreter path and set `HOME=...` only on the pytest command (`HOME=$H /abs/python -m pytest ...`), and write the real status to the output file (`echo rc=$?`).
- **An unmatched zsh glob can silently empty a list.** `tests/test_desktop*` matched nothing, the target variable was empty, and a 79-test run stood in for the intended 3201. Print the resolved target list and the collected count before trusting a pass.
- **The `rtk` hook rewrites `npx`.** `npx -y npm@10 ci` became "Unknown command: npm@10", which looked like a lockfile failure. `rtk proxy npx -y npm@10 ci --dry-run --ignore-scripts` exits 0, so the lockfile was in sync all along.
- General rule: when a check fails or passes unexpectedly, first prove that the check ran (resolved arguments, collected count, real exit status), then read the result.

## Lesson: R1 and R4 are enforced by default; an explicit setting is the kill switch (2026-10-07)

Search: reasoning core default enforce, DEFAULT_ENFORCED_RULES, WISP_REASONING_CORE observe kill switch, enforce roadmap flipped

The owner decided on 2026-10-07 to enforce R1 (unbacked success claim: withhold once, then flag) and R4 (affordability: one bounded retry, else an honest stop) when nothing is configured. This is a **decision made before the roadmap's exit criteria were met**: no false-positive rate on real transcripts, no valid live run (429 noise), and a real "can only afford N" 402 from OpenRouter was seen with `curl` but not driven through Wisp. The roadmap rows say so.

- One constant (`DEFAULT_ENFORCED_RULES` in `wisp/core/reasoning/decision.py`), applied in `WispConfig` only when neither `reasoning_core` nor `reasoning_core_rules` is set anywhere (env or file). **Any explicit value replaces it**: `WISP_REASONING_CORE=observe` makes every rule observe, `off` disables the core, `WISP_REASONING_CORE_RULES=` (empty) means no overrides. A per-rule override beats a global `observe`, so without the "explicit replaces default" rule the observe switch would not have worked; a global `off` already wins over overrides (`test_a_default_of_off_switches_everything_off`).
- Tests written first (two RED), a real-turn witness for the default and one for the kill switch, and four config mutants all killed. One seam test relied on the old default and now sets observe explicitly.
- Full-suite run on this branch: two failures, neither caused by the flip. `test_preflight_doctor::...no_runner_fails` fails on clean `main` as well when the Docker daemon is not reachable. `test_judge_cli::...judged_noop` passes alone and fails when `HOME` holds the state of a whole suite run; with that `HOME` it gives the same INFRA "Traceback" on clean `main` code. The trigger inside that `HOME` was not isolated: a test-isolation weakness, not a regression.

## Lesson: a turn must never end in silence about its own state (2026-10-07)

Search: honest finish, verification floor surrender, UNVERIFIED note, empty round, thinking only, last_uncounted, pytest echo tail masked, token events

A real session (the owner's kvagent project) ran its tests many times with `pytest > f 2>&1; echo "EXIT:$?"; tail -40 f`. `gates.verify.classify` rightly says that command proves nothing (its exit status is `tail`'s), so the verification floor never counted it, rejected the finish twice with "no verification command has been run" (false: one ran, it did not count), then let the turn end; the model answered with reasoning only and the user got no summary and no word that the work was unverified. Reproduced with the real engine and a scripted model; the sequence matched the paste event for event, with the reasoning core `off` and `enforce` alike. `docs/harness/field-observations-2026-10-07.md` (O-9, O-10).

- **Say why a run did not count.** The guard now remembers the last verification-looking command that did not count and its reason (`last_uncounted`); the nudge names it. A gate that is right but silent about its reason makes the model repeat the thing the gate rejected.
- **The harness says it, not the model.** The only statement that the work is UNVERIFIED used to be an instruction to the model. When the floor lets a code-changing turn end without a verification that exited 0, the harness appends one line itself (not when the model already said UNVERIFIED, and not when R1 already flagged the answer). A round that ends with no answer gets one bounded nudge, then a plain statement.
- **Do not define "empty" by one event type.** The first version tested `partial_content`, which only collects `content` events; a provider whose text arrives as `token` events looked empty and got an extra round. Two old tests caught it. Production providers emit `content`, but the gate must never call a streamed answer empty: it now watches content/text/token (not reasoning).
- **A new harness line changes baselines.** The persona `ClaimsWithoutRunning` no longer "reaches the user" with the core off, because the floor's own note now tells the user; `flagged()` in the persona harness was split into any harness note and R1's note specifically. A published table and eight tests had to say which one they meant.
- Method that worked: reproduce the owner's sequence with a scripted provider first (it showed the same event order), RED tests, 12 mutants (11 killed first time; the survivor was a test bound that was too loose), full suite twice. Two failures remain locally and are environmental (Docker daemon; a test that needs a clean `HOME`); a third group (web tests) failed once because DNS dropped during the run.

## Lesson: the user's global instructions file reached only the web route (2026-10-08)

Search: user guidelines, ~/.config/wisp/CLAUDE.md, boot seed, Turn-0, load_context_files, WISP_USER_GUIDELINES, context_files, same workflow as Claude, skills block dropped

`WispConfig.load_context_files` reads `~/.config/wisp/CLAUDE.md`, and its docstring says so, but its only caller is `server/routes/context.py`. The CLI/REPL builds the static prompt without `context_files` and the Turn-0 seed read only the workspace's `AGENTS.md` / `CLAUDE.md` / `.wisp/instructions.md`, so instructions a user set for every session never reached a terminal session (found while giving Claude Code and Wisp one shared workflow file). The seed now reads the user file first, under its own 8,000-char budget; `WISP_USER_GUIDELINES=off` keeps it out; `/doctor`'s `boot_context` reports `found | none | off`.

- **Find the production consumer of a config path, not its docstring.** The function existed, was documented and was called; the call site was the web route only.
- **Measure before using the static prompt.** Adding ~1.5k tokens there (tried as an inline skill, `inline-instructions: true`) pushed the real prompt past its 6,000-token budget and the fitter dropped the whole skills block (priority 2). The seed is outside that budget and is where boot.py's docstring says new bytes belong.
- **A file read from the real HOME leaks into tests.** With a user file present, `test_no_history_no_seed_without_substance` failed (reproduced). `tests/conftest.py` now switches the file off for every test except live E2E; the file's own tests remove the switch.
- **A probe that copies with `cp -R dir/` measures nothing.** On macOS the trailing slash copies the folder's contents, so the probe HOME had no skills and the prompt looked empty. Check the fixture before believing the measurement.
- Method: RED first (7 of 9 failed for the missing section; the 2 passing ones pin what must not change), a real `WispAgentCore.turn()` through a recording provider, 6 of 6 mutants killed on a copy of the tree, and 2,405 related tests against `origin/main` as sets (one run each, empty HOME): no new and no gone failures; the one common failure is the Docker deep-sandbox check.

- **A path that bypasses the main loop must still report to the conversation, and say what the work was worth.** The REPL's strategy gate (`coding.handle_prompt`) sends a prompt to the coding graph instead of the agent loop. Driving a real REPL (a pty, the mock provider) showed three things no unit test had: the gate routed by substring (`author` counted as `auth`, `capital` as `api`); the graph reviewer rejected the change four times and the REPL still printed a green tick because the graph's own status (`succeeded`: every node ran) was read as the work's success; and afterwards the session held zero messages, so the next prompt did not know a graph had run. Fixed: word-boundary hints, `success` requires the review's `ALLOW` (a change nobody reviewed is not a success either), and the prompt plus a bounded outcome note go into the session dict (the module still never touches the database). The graph's nodes are subagents running the same `WispAgentCore.turn()` (so the reasoning core, the gates, tool truncation and the test runner reach them): checked in `graph/runner.py` and `multi_agent/_runner.py`, not assumed. Not changed: a graph that raises still ends the turn instead of falling back to the agent loop, and Ctrl-C during the blocking graph call is PR #106.

- **A loop that exists but is not connected is the same as no loop; connect it and the first end-to-end run finds what the unit tests could not.** `wisp converge` (host-derived acceptance, harness measurement, recovery ladder, journal) is Karpathy's loop in this codebase and no REPL prompt reached it: every `run_turn` caller dispatches exactly one turn. The REPL now routes a prompt to it only when the derivation says the user *stated* a checkable end (or named a symbol to define), and `/converge` always loops (design: `docs/harness/repl-converge-design.md`). Driving it through the real runtime, a real pytest probe and a real pty found four things: `resume` never resumed (the journal's `derivation` line, written since ADR-0048, was read as an attempt, raised `KeyError`, and the loader's `break` dropped every attempt after it: a resumed run started again at attempt 0); the derivation grammar stops at a dot (`Fix totals.py so they pass` is not "stated", `Fix the failing tests` is); the detected check assumes a `tests/` directory; and the probe runs the first `python` on `PATH`. Pinned and passing: a model that "passes" by weakening the test is not proven (the suite is a declared input of the check). Method: RED first (two regression tests failed for the right reason), 24 mutants of the new code, 24 killed (one survivor was an equivalent mutant, replaced by a real order-changing one). Reusable detail: a scripted model keyed by the attempt number in the harness's own prompt survives engine nudges that change the number of provider calls per attempt; an index into a list of rounds does not.

- **Half of Karpathy's loop was missing: keep-or-revert. A loop that only measures lets a failed attempt's edits become the next attempt's starting point.** `autoresearch/program.md` (read in the original) keeps an experiment only if the metric improved and otherwise resets to where it started; `ConvergenceController` measured, classified and journaled but left every failed edit in place, and its one revert (the `ROLLBACK` rung) was opt-in, restored the *baseline* and had a test that never ran the controller. Added per-attempt keep-or-revert (`docs/harness/repl-converge-design.md`, principle 7): an attempt whose harness-measured progress is `NO_PROGRESS` (or that moved the check's own inputs) is undone, the attempt's own files are written to `<journal>.discarded/attempt-N/` first, the reverted paths are journaled and printed, and the next attempt is shown the state it starts from plus one harness line saying the last attempt was reverted. On in the REPL (`WISP_REPL_CONVERGE_REVERT=off`), opt-in for `wisp converge --revert`. Fails closed: no journal, a workspace over 10,000 files / 128 MB (the first bound, 2,000 files, was 10% above Wisp's own 1,804-file tree and would have switched the feature off on the first big change; measured, then raised), a tree that cannot be compared, no earlier measurement, a passing attempt or an authorization event each revert nothing and the record says why. Turning it on in a default path exposed what an opt-in rung had hidden: the snapshot did not skip `.agent/` (the running agent writes its own logs there, so a revert deleted them), walked into `node_modules` before filtering, followed symlinks (a revert could write outside the workspace), dropped permission bits (a deleted script came back without `+x`), and a partial failure was reported as success. Each has a RED test. Method: 39 distinct mutants, 9 survived the first runs and each produced a test or the deletion of dead code; the REPL-level tests drive a real runtime, real tools and a real pytest probe, and a pty test shows it in `python -m wisp repl`. Reusable: **a safety mechanism that is opt-in is untested on the paths where it will matter once it is on by default; before flipping a default, list what the mechanism touches and run it against the process's own state directories.** Stated limits: a concurrent writer is indistinguishable from the attempt (its files are kept aside, not lost); a binary check cannot see partial progress, so a good half-step is reverted (the same property as Karpathy's rule); Ctrl-C does not revert; effects outside the workspace are not undone.
