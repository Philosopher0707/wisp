---
name: background-process-supervision
description: Build or change anything that starts a process which must outlive its caller and still be killable - background shell jobs, daemons, supervisors. Use for wisp/jobs, run_in_background, sandbox tiers, kill and reap logic. Covers what each sandbox tier does on cancel, how to find and kill a whole process tree on macOS and Linux, and how to test it with real processes.
agent_created: true
---

# Background process supervision

Design: `docs/harness/background-jobs-design.md` (invariants BJ1-BJ9). Code: `wisp/jobs/` (`store`, `procs`, `spawn`, `supervisor`), tests: `tests/jobs/` (real processes).

## Shape that works

- **One execution path.** The supervisor runs the command through `run_bash_confined`; the spawner repeats the tool's own validation (same functions) so a refused command never creates a job directory. Exactly one spawn site.
- **A separate process owns each job** (`python -m wisp.jobs.supervisor <dir>`, `start_new_session=True`), so it outlives wisp. State is files, written atomically (`os.replace`), state last: `meta.json`, `supervisor.json` (pid + start time + container), `children.json`, `out.log`, `state.json`.
- **Never report success it did not see.** Dead supervisor with no `state.json` is `lost`; unreadable state is `unknown`; a pid is trusted only with its start time (pid reuse).
- **Ids are `bj-` + 16 hex**, nothing else becomes a path, and they live under the workspace hash. Test traversal with an *existing* id plus a suffix (`<id>/../<id>`, `../<wshash>/<id>`); a nonexistent id hides a missing anchor.
- Run the supervisor with the credential-free environment and `PYTHONPATH` of the wisp that spawned it (else it imports whatever the venv has).

## What the sandbox tiers do on kill (verified in code and by tests)

| Tier | On cancel/kill | Consequence |
|---|---|---|
| host (`NoopSandbox`) | kills its process group | its own kill **orphans grandchildren out of the tree** before you look |
| PTY | runs in `asyncio.to_thread`; a cancel does not stop it; the child has its own session | a process-group kill misses it |
| Docker | `docker exec` client dies, the command keeps running in the container (one container per `DockerSandbox`, never removed) | remove the container |

So: capture the process tree **before** cancelling, remember snapshots (every 0.25 s), kill tree + remembered pids + their process groups, then `docker rm -f` the supervisor's own container. `kill_all` is SIGTERM, grace, SIGKILL, and checks each pid's start time first.

## Platform facts (macOS vs Linux)

- `ps -axo pid=,ppid=` gives the tree on both. An orphan (parent exited) is reparented to init and leaves the tree but **keeps its process group**: sweep by pgid (`ps -axo pid=,pgid=`).
- macOS `ps` hides other processes' environments for Apple-signed binaries (`bash`, `sleep`), so an environment marker (`WISP_JOB_ID`) only works on Linux (`ps -axeww`); `ps ewwp <pid>` works for one non-system process. Setting `os.environ` at runtime does not change what `ps` shows: put the marker in the launch environment.
- A zombie still lists in `ps`: treat state `Z` as dead.
- A command that backgrounds a process with stdout still open keeps the job's pipe open, so the job does not finish (the tiers wait for EOF); redirect (`> /dev/null 2>&1 &`) in tests.
- The danger check refuses inline interpreter code (`python -c ...` with `open`/`subprocess`): write a script file in the workspace for test commands.

## Test with real processes

Host tier via `WISP_SANDBOX=off`; PTY by giving the child a `PATH` without `docker`; Docker opt-in (`WISP_JOBS_TEST_DOCKER=1`) and assert the job really ran on Docker (`view.tier == "docker"`, a router can fail over silently). Cases that found real defects: a grandchild in its own session, a command that ignores SIGTERM, a supervisor SIGKILLed (reaper), a SIGSTOPped supervisor (force path), kill before start, a job that outlives the process that spawned it. Poll with `wait_until`, never fixed sleeps. Mutation-probe the package (`mutation-probe`): 17 survivors on the first pass were real.

## Do not

Run a job through a second path, trust a bare pid, assume cancel kills, call a SIGKILLed supervisor's job finished, or use the worktree for anything else while a mutation probe runs.
