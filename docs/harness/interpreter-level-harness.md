# Interpreter-level harness: ideas, evidence and what must be verified first

Status: **notes, 2026-10-07. Nothing here is built.** Owner decision: **verify #3, #4 and #5 first; only then proceed.** "Interpreter level" means which Python runs Wisp, how Wisp launches itself and everything it spawns, and what environment those children get.

## The ideas, ranked

| # | Idea | Evidence | State |
|---|---|---|---|
| 1 | One launcher for every child interpreter (`wisp/proc/launch.py`: pins `sys.executable`, `-P`, the running package's path, credential-free env, a cwd that is never the workspace; an AST test lets only the launcher start `sys.executable` children) | **Verified:** 7 files build child Python commands separately (`benchmark/swebench.py`, `benchmark/tasks.py`, `test_runner.py` x3, `judge/core.py` x2, `__main__.py`, `wisp_net/evaluation.py`) with different env, cwd and path policy | accepted, waits on #3-#5 |
| 2 | Provenance, "which wisp am I": version/doctor/judge verdict report interpreter, Python version, package root, git SHA, dirty flag; live and judge runs fail closed if the child's root is not the expected checkout | **Verified:** the venv's `wisp` imported `~/dev/wisp` (the owner's checkout), so a live run would have silently tested old code; the global command lags `origin/main` | accepted, waits on #3-#5 |
| 3 | Project-interpreter resolution and attestation (one resolver for run_bash PATH, run_tests, judge and the verification floor; the ledger records which interpreter ran each verification) | **From the code, not run:** `wisp/test_runner.py` uses `sys.executable` (Wisp's own interpreter) while `run_bash` puts the workspace venv first | **TO VERIFY** |
| 4 | Process registry and reaper for ALL harness children (tiers, hooks, MCP servers, subagents, containers), reported by `wisp doctor` | **Verified count:** 66 exited `wisp-sandbox-*` containers (55 on `python:3.12-slim`, 11 on `wisp-sandbox:py312`); **cause read, not proven:** `DockerSandbox` starts one container per instance and I found no removal | **TO VERIFY** |
| 5 | Child interpreter flags policy (`-P`, `PYTHONHASHSEED=0` for judge and verification, `-X faulthandler`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUTF8`) | `AGENTS_LEARNING.md` records a judge task (`dedupe`) whose result depended on `PYTHONHASHSEED`; the effect of the other flags on workspace diffs is a hypothesis | **TO VERIFY** |
| 6 | Exit diagnostics: the exit watchdog names live non-daemon threads; SIGUSR1 dumps stacks via `faulthandler` | open item: the thread that blocks exit is unidentified | later |
| 7 | Runtime manifest and one blessed interpreter shared by the CLI and the macOS app | the app bundles its own Python; earlier we removed two stale `wisp` binaries | later |
| 8 | Cold-start import-time budget (`-X importtime` test) | hypothesis | later |
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

_Not yet run._ Each section above gets: the exact command, the output, and one of confirmed or refuted. Then #1 and #2 are designed (a single launcher and provenance), using the verified findings.

## Constraints

Standing rules apply: no experiment may print `.env` contents, push, or delete containers or processes it did not start. Experiments run in throwaway directories with a throwaway `HOME`. Mutation-probe and CI-parity rules (`.agents/skills/mutation-probe`, `.agents/skills/ci-parity-before-push`) apply to whatever gets built afterwards.
