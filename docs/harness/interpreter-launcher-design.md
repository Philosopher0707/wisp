# Interpreter launcher and project-interpreter attestation: design

Status: **design, 2026-10-11. Nothing here is built.** It follows `interpreter-level-harness.md`, whose gate (verify ideas #3, #4, #5 first) was cleared on 2026-10-07. Owner decision, 2026-10-11: write the design; **no code until it has been read.**

Covers ideas 1 (one launcher), 2 (provenance), 3 and 10 (which interpreter answers "do the tests pass?", and with which environment), and 13 (a silent 0/0). Does not cover the rest (see the last section).

## Where the findings leave us

`run_tests` (`wisp/test_runner.py:84,136,146`) is the one verification path that does not follow the rules the other paths follow:

| | `run_bash` and the sandbox tiers | `run_tests` |
|---|---|---|
| interpreter | the project's (`_put_venv_first` puts `<workspace>/.venv/bin` first) | Wisp's own `sys.executable` |
| environment | `credential_free_env` (credentials stripped) | none passed: the project's tests see Wisp's API keys |
| pytest plugins | whatever the project has | decided by `_has_plugin("pytest_jsonreport")`, asked in **Wisp's** process |
| no pytest in the interpreter | pytest's own error | `## Test Results (0/0 passed)`, no message |

Verified on 2026-10-07 (`interpreter-level-harness.md`, Results #3 and "The four open concerns"): a **false failure** when a dependency exists only in the project's venv, a **false pass** when it exists only in Wisp's, the key visible to the project's tests, and the verification floor (`core/verification.py`, `_run_tests_is_evidence`) counting that pass as fresh evidence. The system prompt tells the model to verify through `run_tests` (same document, concern 1), so this is the path models are steered to.

`credential_free_env` is already the authority for "what may a child process see": the sandbox router (so `run_bash`), MCP servers, the doctor, the jobs supervisor and the terminal-bench adapter all use it. `run_tests` is the outlier, so the first slice is joining the existing rule, not writing a new one.

## One correction to the earlier policy: `-P` is for harness children only

The earlier notes say "always `-P`" (no current directory or script directory on `sys.path`). That is right for Wisp's own children and wrong for a project's tests. Verified on 2026-10-11, Python 3.12.8, a throwaway project with `mymod.py` at its root and `tests/test_x.py` doing `import mymod`:

| command | result |
|---|---|
| `python -m pytest tests` | 1 passed (`-m` puts the current directory on `sys.path`) |
| `python -P -m pytest tests` | collection error, `ModuleNotFoundError: No module named 'mymod'` |

So there are two kinds of child, and the launcher treats them differently:

| | harness child | project child |
|---|---|---|
| what runs | Wisp's own code: the jobs supervisor, the judge's child, the benchmark runners, the exit probe | the project's tests, `run_tests`, probes of the project |
| interpreter | Wisp's own | the project's, resolved below |
| `-P` | always (the verified import hijack: a workspace with its own `wisp/` package took over `python -m wisp`) | never |
| environment | `minimal_process_env` plus what the caller names | `credential_free_env(workspace=...)`, the same as `run_bash` |
| current directory | never the workspace | the workspace |

## Design

### 1. The resolver (`wisp/proc/interpreter.py`)

```python
@dataclass(frozen=True)
class ProjectInterpreter:
    path: str                  # absolute path of the python executable
    source: str                # "explicit" | "workspace-venv" | "wisp-own"
    version: str               # "3.12.8"
    pytest: str | None         # pytest's version, None when it cannot be imported
    plugins: frozenset[str]    # which of pytest_jsonreport, pytest_asyncio, xdist import
```

Order, first match wins, and an explicit setting that is invalid is an error, never a silent fall-through:

1. `WISP_PROJECT_PYTHON` (or the same key in the config file): must exist and run.
2. `<workspace>/.venv`, `venv`, `env`: a directory that holds `pyvenv.cfg` and `bin/python` (`Scripts\python.exe` on Windows). The `pyvenv.cfg` check keeps a dotenv file or an unrelated folder from being taken for an environment. This replaces `_put_venv_first`'s `.venv`-only detection, so `run_bash` and `run_tests` cannot disagree about where the project's Python is (one authority).
3. `wisp-own`: `sys.executable`, **flagged** in every result that uses it.

Deliberately not in the first slices: `VIRTUAL_ENV` or `CONDA_PREFIX` of Wisp's own process (they describe the shell Wisp was started from, not the project), poetry, uv, pyenv shims and conda environments elsewhere. Each needs a tool to be executed to find the answer; they come later, opt-in.

The probe that fills `version`, `pytest` and `plugins` is one `python -c` with `importlib.util.find_spec` and `importlib.metadata`, run in the project-child environment with a 10 s timeout, once per `run_tests` call (no cache across calls: a `pip install` mid-session must be seen). A probe that fails or times out makes `pytest=None` with the reason attached, not a guess.

### 2. The launcher (`wisp/proc/launch.py`)

Two functions, one for each kind of child: `harness_python(args, cwd, extra_env)` and `project_python(interpreter, args, workspace, timeout)`. Everything that starts a Python child goes through one of them, and an AST test lets nothing else build a command from `sys.executable`, with an allow-list that shrinks as sites migrate (a ratchet, not a big bang). The sites today, from `git grep sys.executable`: `test_runner.py` (3), `judge/core.py` (2), `benchmark/swebench.py` (2), `benchmark/tasks.py`, `__main__.py` (the VS Code MCP server's command), `wisp_net/evaluation.py` (a default argument); `jobs/spawn.py` already passes `-P`.

### 3. Provenance in the result

`UnitTestRunSummary` gains `interpreter`, `interpreter_source`, `python_version`, `pytest_version`, and `format_for_llm` adds one line below the header:

```
## Test Results (3/3 passed)
Interpreter: /work/proj/.venv/bin/python (workspace-venv, Python 3.11.9, pytest 8.2.0)
```

`_TEST_RESULTS_RE` keeps matching the header, so nothing that parses it changes. When the source is `wisp-own` the line says so and adds: "no project interpreter was found; this ran under Wisp's own Python and may not match the project's environment". The model and the person both see what answered the question.

### 4. The verification floor

Observe, then enforce (`.agents/skills/observe-then-enforce-rollout`):

- **Observe.** Each `run_tests` call is journalled with its interpreter source. This is also the missing measurement from the earlier notes: *how often real models call `run_tests`*, and how often the answer came from `wisp-own`.
- **Enforce, only after the owner chooses how** (decision 1 below). What the floor does with a `wisp-own` pass is a policy, not a mechanism, so it is left out of the first slices.

### 5. What a naive fix would break, and the answer to each (Results, concern 4)

| hazard | answer |
|---|---|
| (a) `--json-report` is chosen by asking Wisp's own process whether the plugin imports; another interpreter without it exits rc 4, `unrecognized arguments` | the probe asks the **target** interpreter; `interp.plugins` replaces `_has_plugin` |
| (b) no pytest in the interpreter: a silent `0/0 passed` | `pytest is None` produces `ERROR: pytest is not installed in <path> (<source>)`, not a results header. `_run_tests_is_evidence` already refuses 0/0; the message now says why, and the model can act on it |
| (c) an async project whose venv lacks `pytest-asyncio` passed under Wisp's Python and now fails | correct, and explained: when `pytest_asyncio` is not in `plugins` and a failure says `async def functions are not natively supported`, the summary adds a line saying the project's environment has no pytest-asyncio |
| (d) `_put_venv_first` only knows `.venv` | the resolver replaces it (above) |

The Docker tier stays out: its interpreter is the container's and it has no pytest by default (idea 14). The only change is a label in the result, "host, unsandboxed", so nobody reads a `run_tests` pass as having run inside the sandbox.

## Slices

Each is shippable and reversible on its own, and each has a first caller, because code nothing reaches is not delivered.

1. **Join the existing rule, report the rest.** `run_tests` runs with `credential_free_env` (closes the credential exposure, the only security finding here); a missing pytest becomes a named error; the result gets the provenance line, and the resolver runs in report-only mode ("project interpreter available: X, not used"); the journal records each call. No interpreter changes yet, so no pass turns into a failure.
2. **Use the project's interpreter.** `run_tests` runs under the resolved interpreter, behind `WISP_RUN_TESTS_INTERPRETER=project|wisp`, in the same migration pattern as `WISP_STRICT_ENV`: the flag first, the default flipped after the journal shows the effect. This is the slice that turns false passes into failures; (a), (c), (d) land here.
3. **The floor's policy for `wisp-own`**, per decision 1.
4. **The launcher for harness children**, one file per commit with its own test and the ratchet tightened each time; `judge/core.py` gets its child's environment isolated in #119 (open).

## Tests that would prove it (RED first)

A helper `make_project_venv(tmp_path, modules)` builds a real environment with `venv.create(with_pip=False)` and links in only pytest and its own dependencies from the running environment, plus the modules a case needs (`venv.create` took 4.7 s on this machine, so the helper is module-scoped). Then, against `run_tests` and the floor through their production entry points:

- the four rows of the 2026-10-07 matrix as tests: the false failure, the false pass, the key visible to the project's tests, and which interpreter ran;
- each of (a) to (d); a project that imports its own top-level module passes (the `-P` finding above); a harness child does get `-P`;
- the ratchet: a new `[sys.executable, ...]` command outside the launcher fails the AST test;
- a mutation probe over the resolver (the order, the `pyvenv.cfg` check, the explicit-setting error) before anything merges.

## What this does not do

Leftover children of hooks, MCP managers and backgrounded commands (ideas 12 and 15), the container reaper (11), the runtime manifest shared with the macOS app (7), exit diagnostics (6), cold-start budget (8), uniform rlimits (9). The Docker image without pytest (14) gets only the label.

## Decisions for the owner, after reading

1. **A `wisp-own` pass when no project interpreter is found** (a project with no venv, or one the resolver cannot see): label it only (my lean for the first release); count it only if the project declares no dependencies; or refuse it as evidence. The last one is the strictest and will annoy people with conda or system-Python projects until the later resolvers exist.
2. **Slice 2's default:** project interpreter from the first release, or opt-in for one release while the journal fills.
3. **Which later resolvers matter to you:** poetry, uv, conda, pyenv. Each is a tool Wisp would have to run to ask.

## Constraints

Standing rules apply: experiments in throwaway directories with a throwaway `HOME`, never print `.env`, never remove a process or container the experiment did not start, mutation-probe and CI-parity before any PR (`.agents/skills/mutation-probe`, `.agents/skills/ci-parity-before-push`).
