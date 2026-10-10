# Interpreter-level harness: ideas, evidence and what must be verified first

> **Note (2026-10-10):** `wisp judge`, mentioned in several rows below, was removed. Read those rows as design history.

Status: **notes, 2026-10-07. Nothing here is built.** Owner decision: **verify #3, #4 and #5 first; only then proceed.** "Interpreter level" means which Python runs Wisp, how Wisp launches itself and everything it spawns, and what environment those children get.

## The ideas, ranked

| # | Idea | Evidence | State |
|---|---|---|---|
| 1 | One launcher for every child interpreter (`wisp/proc/launch.py`: pins `sys.executable`, `-P`, the running package's path, credential-free env, a cwd that is never the workspace; an AST test lets only the launcher start `sys.executable` children) | **Verified:** 7 files build child Python commands separately (`benchmark/swebench.py`, `benchmark/tasks.py`, `test_runner.py` x3, `judge/core.py` x2, `__main__.py`, `wisp_net/evaluation.py`) with different env, cwd and path policy | accepted; waits on #5 |
| 2 | Provenance, "which wisp am I": version/doctor/judge verdict report interpreter, Python version, package root, git SHA, dirty flag; live and judge runs fail closed if the child's root is not the expected checkout | **Verified:** the venv's `wisp` imported `~/dev/wisp` (the owner's checkout), so a live run would have silently tested old code; the global command lags `origin/main` | accepted; waits on #5 |
| 3 | Project-interpreter resolution and attestation (one resolver for run_bash PATH, run_tests, judge and the verification floor; the ledger records which interpreter ran each verification) | **From the code, not run:** `wisp/test_runner.py` uses `sys.executable` (Wisp's own interpreter) while `run_bash` puts the workspace venv first | **CONFIRMED 2026-10-07** (see Results) |
| 4 | Process registry and reaper for ALL harness children (tiers, hooks, MCP servers, subagents, containers), reported by `wisp doctor` | **Verified count:** 66 exited `wisp-sandbox-*` containers (55 on `python:3.12-slim`, 11 on `wisp-sandbox:py312`); **cause read, not proven:** `DockerSandbox` starts one container per instance and I found no removal | **CONFIRMED 2026-10-07** (see Results) |
| 5 | Child interpreter flags policy (`-P`, `PYTHONHASHSEED=0` for judge and verification, `-X faulthandler`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUTF8`) | `AGENTS_LEARNING.md` records a judge task (`dedupe`) whose result depended on `PYTHONHASHSEED`; the effect of the other flags on workspace diffs is a hypothesis | **MOSTLY REFUTED 2026-10-07**; revised policy in Results |
| 6 | Exit diagnostics: the exit watchdog names live non-daemon threads; SIGUSR1 dumps stacks via `faulthandler` | open item: the thread that blocks exit is unidentified | later |
| 7 | Runtime manifest and one blessed interpreter shared by the CLI and the macOS app | the app bundles its own Python; earlier we removed two stale `wisp` binaries | later |
| 8 | Cold-start import-time budget (`-X importtime` test) | hypothesis | later |
| 10 | `run_tests` must use the credential-free environment and the project's interpreter (found by #3: the project's tests saw the API key and ran under Wisp's Python) | **Verified** by experiment (see Results) | new, follows from #3 |
| 11 | Register container cleanup at process exit and on signals; `wisp doctor` reports stray `wisp-sandbox-*` containers (found by #4) | **Verified** by experiment (see Results) | new, follows from #4 |
| 9 | Uniform rlimits for all children (the PTY tier already has them) | read in `sandbox/router.py` | later |

Already fixed from this exploration: a workspace carrying its own `wisp/` package hijacked `python -m wisp` when the workspace was the working directory (verified in a throwaway directory); the background-jobs supervisor was launched that way and now uses `-P` and its own directory (PR #102, `62d1684`, test written first and RED before the fix).

## Verification plan (nothing proceeds until each has a result written below)

### #3 Which interpreter answers "do the tests pass?"
1. Build a temp project with its own venv containing a package `projdep` that is **not** installed in Wisp's venv, and a test that imports it.
2. Run the tests (a) through `run_bash "python -m pytest"` (workspace venv first on PATH) and (b) through the `run_tests` tool / `wisp/test_runner.py` (`sys.executable`).
3. Repeat in the opposite direction: a project that does **not** declare a dependency that Wisp's own venv happens to contain; does the test pass under (b) and fail under (a)? This direction is the dangerous one (a false "pass").
4. Record the interpreter each path actually used and which one the verification floor consults.
- **Confirmed** if (a) and (b) disagree for the same project in either direction. **Refuted** if both resolve to the same interpreter. Record: what each path does, with the command and output.

### #4 Do harness children leak?
1. Count `wisp-sandbox-*` containers (running and exited), note image and age. Find every cleanup registration (`atexit`, `__del__`, `_cleanup_router` callers) by reading, and say which paths call it.
2. Run N separate short-lived `wisp --print` invocations that execute one `run_bash` on the Docker tier; count containers after each.
3. Separately check processes after exit: a `run_bash` that backgrounds a child, a hook, an MCP server started by a session.
- **Confirmed** if the container count grows by about one per process with no cleanup on exit, or any child outlives its parent. **Refuted** if cleanup runs on every exit path and the 66 came from crashed or killed runs only (then say which, and what would have prevented them). Do not remove any existing container as part of the check.

### #5 Do the flags change results?
1. `PYTHONHASHSEED`: run the judge `dedupe` hidden check under seeds 0 to N and record the pass rate; then with `PYTHONHASHSEED=0` forced in the child environment.
2. `PYTHONDONTWRITEBYTECODE` / pytest cache: run a verification in a workspace and diff the tree before and after; do `__pycache__` or `.pytest_cache` appear in what the judge and the git-diff features treat as changes?
3. `-X faulthandler` / `-P`: confirm they change nothing else (a before/after run of the targeted suites).
- **Confirmed** if (1) shows seed dependence that the flag removes, or (2) shows workspace pollution that the flag removes. **Refuted** where the measured effect is nil; drop that flag from the policy. Record the numbers.

## Results

### #3 CONFIRMED (2026-10-07), in both directions, plus a credential exposure

Script: a throwaway project with its own venv (pytest and a module `projdep` installed only there; no `httpx`), run through three paths with a throwaway `HOME` and a FAKE key `MY_FAKE_API_KEY` (never a real one). Each test wrote `sys.executable` and whether the fake key was visible.

| project's test needs | `run_bash` (host or PTY) | `run_tests` tool |
|---|---|---|
| `projdep` (only in the project's venv) | **passed**, interpreter = the project's `.venv/bin/python` | **error** (0/1 passed, collection error): a false failure |
| `httpx` (only in Wisp's own venv) | **error** (collection error): correct, the project does not declare it | **passed 1/1**, interpreter = `~/.venvs/wisp/bin/python`: a **false pass** |

- `run_tests` runs `[sys.executable, "-m", "pytest", ...]` with no `env=`, so it answers "do the tests pass?" under **Wisp's** interpreter, and `run_bash` answers it under the **project's**. They disagree for the same project in either direction.
- **Credential exposure:** under `run_tests` the project's tests saw `MY_FAKE_API_KEY` (**KEY-VISIBLE**); under `run_bash` they did not (**key-hidden**, `credential_free_env`). Any API key in Wisp's environment is visible to the project's test code when `run_tests` runs it. New finding, not in the ideas table before: add as idea 10.
- Consequence for the reasoning core: the ledger records `run_tests` as a VERIFICATION_RUN, so a false pass counts as fresh verification. Which of `_VERIFY_TOOLS` (`run_bash`, `exec_sandbox` only) or the `run_tests` evidence rule in `core/verification.py` the verification floor consults was **not traced**: unverified.
- Caveat: with the throwaway `HOME` the router fell back to the PTY tier ("no Docker daemon": the Docker context lives under `HOME`), so the Docker tier (a third interpreter, the container's `python:3.12-slim`) was **not** exercised here.

### #4 CONFIRMED (2026-10-07): every process that uses the Docker tier leaves a running container

- Before: 66 `wisp-sandbox-*` containers, all Exited. Three separate short-lived Python processes each ran one `tool_run_bash("echo hi-N")` on the Docker tier. After the three processes had exited: 69 containers, the 3 new ones **still `Up`**.
- Cause (read in code, matches the observation): `docker_run_args` starts the container with `-d` and `--entrypoint sleep ... infinity`, no `--rm`; `DockerSandbox.cleanup()` exists, but its only caller path is `reset_router()`, whose docstring says "(tests)" and which nothing in production calls (no `atexit`, no exit hook). So the container runs until Docker restarts, and the 66 Exited ones are the containers a Docker or machine restart stopped, never removed. Each held `--memory=2g --cpus=2` limits while running.
- Cleaned up: only the 3 created by this experiment (exact names from a before/after diff); the original 66 are untouched. Final count 66.
- Not checked: leftover non-container children (a `run_bash` that backgrounds a process, hooks, MCP servers started by a session): not run.
- New idea 11 follows: register `cleanup()` for every router at process exit and on signals, and have `wisp doctor` report stray `wisp-sandbox-*` containers.

### #5 MOSTLY REFUTED (2026-10-07): the flags are not the fix; `-P` stays for security

Script: `verify5.py` in throwaway directories with a throwaway `HOME`, 100 seeds per case (`PYTHONPATH` pinned to the worktree).

| question | measured | verdict |
|---|---|---|
| Is the judge's `dedupe` result seed-dependent? | current hidden check (12 strings): buggy `list(set(xs))` passes **0/100** seeds, correct solution **100/100**; visible check (ints): buggy **0/100** | **No.** The 3-string version was fixed earlier. |
| Is the hazard real in general? (positive control: 3-string order check against the buggy solution) | passes **16/100** seeds (expected 1 in 6) | Yes, for any check that depends on set order. |
| Would forcing `PYTHONHASHSEED=0` help? | seed 0 alone decides the control forever (it failed): a fixed seed makes a set-order bug permanently visible OR permanently hidden (deterministic by design; one seed measured, not 100 repeated runs) | **No: do not force a seed.** Run with a random seed and **record it** with the verification so a failure can be reproduced. |
| Do bytecode and cache files pollute workspace diffs? | default pytest run created `.pytest_cache`; `git status --untracked-files=all` saw **0** untracked (pytest ignores its own cache); `judge.snapshot` saw **no** extra files; with `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider` no files were created at all | **No measurable effect.** No `__pycache__` appeared even in the default run, probably because this environment already sets a bytecode flag: not investigated. Wisp's own `/api/git/diff` uses git and so shares the result; other file-walking diffs were not tested. |
| Do `-P` and `-X faulthandler` change any result? | `tests/reasoning`: **451 passed** plain and with `-P -X faulthandler`, 29.7 s vs 28.9 s | No. Harmless. |

Revised policy for idea 5: always `-P` (justified by the verified import-hijack, not by test results), `-X faulthandler` is harmless and useful for the exit diagnostics (idea 6), **do not** force `PYTHONHASHSEED` (record it instead), `PYTHONDONTWRITEBYTECODE` is not needed for diffs.

### A second occurrence of idea 2, found while running these

The first run of `verify5.py` failed with `ModuleNotFoundError: No module named 'wisp.judge'`: a script run from the scratch directory put the venv's installed `wisp` (the owner's own checkout at `~/dev/wisp`, which has no `judge` package) ahead of the worktree under test. Pinning with `PYTHONPATH=<worktree>` fixed it. Same cause as the live-run provenance problem: nothing says which `wisp` a process imported.

## What is open

**Gate cleared (2026-10-07): #3 confirmed, #4 confirmed, #5 mostly refuted.** Still open: the leftover-children part of #4 (a backgrounded `run_bash` child, hooks, MCP servers; not run). Next is the design of #1 (one launcher) and #2 (provenance) with these findings: the launcher must give `run_tests` the same credential-free environment and interpreter resolution as `run_bash`, always passes `-P`, does not force a hash seed, and the process registry (#4) must include sandbox containers.

## The four open concerns, verified (2026-10-07)

Experiments: `v_floor.py` (real engine turns, scripted model), `v_docker.py` (real Docker tier), `v_children.py` (separate processes, unique `sleep 43NN` markers, only those killed), `v_naivefix.py` (throwaway venvs). All with a throwaway `HOME` except the Docker one (needs the real Docker context; it removed only the container it created: 66 before, 66 after).

### 1. Does the verification floor accept `run_tests`? YES, and the prompt steers models to it

| scripted turn (real engine) | provider rounds | floor nudges | reading |
|---|---|---|---|
| A. write, `run_tests` passes (only under Wisp's python), finish | 3 | **0** | the floor accepts a false pass |
| B. write, finish | 4 | 2 | the floor is active |
| C. write, `run_bash pytest` **fails**, `run_tests` passes, finish | 4 | **0** | a `run_tests` pass overrides an earlier failed `run_bash` verification |
| D. write, `run_tests` **fails**, finish | 5 | 2 | a red `run_tests` is not recorded as failure, but the floor still blocks finishing |

- Code: `VerificationFloorGuard.note_tool_result` has a `run_tests` branch that sets `verify_ok_after_edit = True` on a green summary (`core/verification.py`).
- **The system prompt steers models to `run_tests`**: rule 7 ("bare `python3` may not exist where commands execute ... verify through `run_tests`/`lsp_diagnostics`") and the Verification loop ("run verification with ... `run_tests` or `lsp_diagnostics`"), `context_assembler.py`. So the path with the false pass, the host execution and the visible API key is the path the prompt recommends.
- **Not measured: how often real models call it.** No transcripts were read; the live-run harness did not record tool calls. The prompt's wording is the only evidence of intent.

### 2. The Docker tier is a third interpreter, and it cannot run pytest by default

On the Docker tier `python` is the container's `/usr/local/bin/python` 3.12.14 on Linux, and `python -m pytest` fails with **`No module named pytest`** (the default image `python:3.12-slim` has none). So when Docker is reachable (the default here), `run_bash` cannot run a project's tests at all unless `WISP_SANDBOX_IMAGE` is overridden. The prompt's rule 7 then pushes the model to `run_tests`, which runs **on the host, outside the sandbox, with Wisp's interpreter and environment**. The sandbox's protections do not cover the test-running path the prompt recommends.

### 3. Leftover children: YES in four cases, clean in two

| scenario | result |
|---|---|
| `run_bash "sleep N &"` on the host tier, then the process exits | **LEFTOVER** |
| same on the PTY tier | **LEFTOVER** |
| async hook (`_arun_one_hook`) that times out | **LEFTOVER**: the code returns "Hook timed out" and never kills the process (`asyncio.wait_for` cancels the wait, not the child) |
| sync hook, single command, times out | clean (killed) |
| sync hook, compound command (`a & b`), times out | **LEFTOVER**: `subprocess.run(timeout=)` kills the shell, not its children |
| MCP server held by the global manager, normal exit | clean (`atexit` shutdown works) |
| MCP server held by a non-global `MCPManager`, normal exit | **LEFTOVER** (still alive after the harness's 120 s timeout; it also held the harness's output pipe open) |

Not exercised: the LSP client (needs a server that speaks the protocol; the code has `shutdown()` and no `atexit`, read not run), and abnormal exits (SIGKILL of the parent). A backgrounded `run_bash` child outliving its call is arguably intended (`nohup`-style); it is still unowned and unreported. Strays from this experiment: 0 after cleanup.

### 4. What a naive fix of #3 would break

| check | result |
|---|---|
| (a) the plugin flags | `run_tests` adds `--json-report` when `pytest_jsonreport` imports in **Wisp's** process; run under another interpreter that lacks it, pytest exits **rc 4, `unrecognized arguments`**. Not hit today (Wisp's env has no `pytest_jsonreport`, so the plain path always runs), but any swap must detect plugins in the **target** interpreter |
| (b) no pytest in the interpreter that runs it (a user who installed wisp without `[dev]`: pytest is **only** in the `dev` extra) | `run_tests` reports **`Test Results (0/0 passed) - Failed: 0, Errors: 0`**: a silent, empty result with no "No module named pytest" message. A usability bug in the current tool, independent of the fix |
| (c) an async-test project whose venv lacks `pytest-asyncio` (Wisp's env has it) | passes under Wisp's interpreter, **fails under the project's** (`async def functions are not natively supported`): the fix converts this false pass into a failure, which is correct but must be explained to the model |
| (d) `_put_venv_first` | recognises only `<workspace>/.venv`; `venv/`, poetry, uv-managed and conda environments elsewhere are not found, so a "project interpreter" resolver is more than a one-line change |

### Still unverified after this pass

How often real models call `run_tests`; the LSP leftover; abnormal-exit behaviour of hooks and MCP; `wisp doctor` coverage of strays; whether `_put_venv_first` is the only environment detection.

### New ideas from these results

12. Kill a timed-out hook's whole process group (sync and async). 13. Make `run_tests` report "pytest not found" instead of a silent 0/0. 14. Decide what the Docker tier does about pytest (an image with pytest, or run verification in the container with the project's own environment, or label `run_tests` as host-unsandboxed in its result). 15. Clean up non-global MCP managers at exit.

## Constraints

Standing rules apply: no experiment may print `.env` contents, push, or delete containers or processes it did not start. Experiments run in throwaway directories with a throwaway `HOME`. Mutation-probe and CI-parity rules (`.agents/skills/mutation-probe`, `.agents/skills/ci-parity-before-push`) apply to whatever gets built afterwards.
