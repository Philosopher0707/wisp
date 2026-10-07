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

## wisp judge: what the first CI run and the build taught (2026-10-06)

- **`--provider X` inherited another provider's endpoint and key:** a flag path that replaces only `provider` keeps `WISP_API_BASE`/`WISP_API_KEY` written for the configured provider. Fixed in `with_provider()` (PR 92). Evidence: building the NVIDIA provider from the real config gave `openrouter.ai` + the shared key before, `integrate.api.nvidia.com` + `NVIDIA_API_KEY` after. A live call could not tell them apart once OpenRouter answered again, so test the construction, not the call.
- **A hidden check that depends on set order passes buggy code by chance:** `dedupe` compared a 3-string set, whose iteration order follows `PYTHONHASHSEED`, so `list(set(xs))` passed sometimes. The self-test only failed intermittently, which is the signal; do not dismiss one unexplained red run. `tests/test_judge_tasks.py` now runs every hidden check under three seeds.
- **The module-orphan scanner reads `node.module` only:** `from wisp.judge import core, improve` records `wisp.judge`, not `wisp.judge.improve`, so a module reached only that way is reported as having no importer. Import by full path (`from wisp.judge.improve import run_loop`).
- **Choose the local test slice from CI's failures, not from guesses:** my architecture-test filter by file name missed `test_doc_drift` and `test_module_orphans`, and the first CI run of the PR caught both. `AGENTS.md` states a test-file count with a tolerance of 40; `main` was at 439 against 400, so any four new test files tipped it.
- **`rtk` filters output and can mislead:** it showed `06bc076` as `origin/main` when the real value was `f660329`. Use `rtk proxy git ...` for a SHA or a count that decides something.
- **Verify a subcommand through the checkout under test:** a subprocess `python -m wisp judge ...` ran the older installed wisp, which has no `judge` and sent "judge run ..." to the model as a prompt (a mock reply, exit 0, a green-looking wrong result). The end-to-end test sets `PYTHONPATH` to its own checkout.

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
