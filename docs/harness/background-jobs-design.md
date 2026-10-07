# Background jobs for Wisp: design (draft for review)

Status: design only, 2026-10-07. Nothing is built. Branch `feat/background-jobs` (worktree `~/dev/_scratch/wisp-bg`), from `origin/main` 7b34349.
Request: "`run_in_background` for Wisp, for shell commands and for agents; jobs survive restarts; build it surgically and carefully."

## 1. What exists (verified in this checkout)

| Piece | Where | Behaviour today |
|---|---|---|
| Background **subagents** | `wisp/multi_agent/background.py`, tools in `wisp/tools/subagent_tools.py` | `spawn_background` returns an id at once; `subagent_wait`; durable run rows with leases; `drain_notifications()` surfaces finished agents in the **operating context at the start of the next turn** (`stateless.py` ~1668). Running agents are `asyncio` tasks, so they die with the process; the durable rows exist for reaping, not resumption. |
| Shell execution | `wisp/tools/bash.py::run_bash_confined` | validates, danger-checks, routes through the sandbox tier router (Docker -> isolated PTY -> host), **awaits the command to completion**, returns a `BashRun`. Every command (and the verification floor) goes through it. |
| Pre-dispatch policy | `_gate_tool_call` (+ the invariant gates on the gates branch, PR #100) | decides on the *tool call*, before execution. |
| Completion folding | `VerificationFloorGuard.note_tool_result(name, text, args)` | classifies a finished verify command by its text and orders it against edits. |

So the **agent half already exists in-process**; the missing pieces are (a) background **shell** jobs and (b) **restart survival** for either.

## 2. Findings that shape the design

1. A shell job must go through `run_bash_confined`'s policy (validation, danger check, tier router). A second execution path is a second authority for "what may run".
2. `run_bash_confined` returns only at the end and `sandbox.run` has no handle for the process, so a job cannot be killed or tailed from outside, and a job in the wisp process dies when wisp exits. Survival therefore needs a **separate process that owns the job**.
3. `--print` mode exits after one turn and the REPL may run each turn on its own event loop, so a job cannot be an `asyncio` task of the caller.
4. A model only learns of a result when something tells it: the existing channel is the next turn's operating context; mid-turn needs a tool the model calls.
5. The verification floor orders results against edits by the order they arrive. A job started *before* an edit and finishing *after* it must not count as fresh verification.

## 3. Architecture

```
wisp (any process)                         supervisor (own session, survives wisp)
 run_bash(run_in_background=true)  --spawn-->  python -m wisp.jobs.supervisor <job dir>
   gates + validation (unchanged)                 runs the command through run_bash_confined
   returns {job_id}                               streams stdout/stderr to <job dir>/out.log (capped)
 bash_output(job_id) / kill_bash(job_id)          writes <job dir>/state.json (atomic): running -> exited|killed|timed_out
   read the job dir only                          enforces max runtime, output cap, process-group kill
```

- **Job store:** `<state home>/wisp/jobs/<workspace-hash>/<job-id>/` with `meta.json` (command, workspace, started, `mutation_index` at start, pid, pid start time, max runtime), `out.log`, `state.json`. Not under the workspace (so `.wisp/` stays untouched and the path jail is unaffected); mode 0700.
- **Identity:** a job id is a random token bound to the workspace hash; `bash_output`/`kill_bash` refuse any id not in the current workspace's store.
- **Supervisor:** a small module that imports `run_bash_confined` (the one execution path), runs it in its own session (`start_new_session=True`), and on SIGTERM/kill terminates the **process group**. It records its pid and start time so liveness is `pid alive AND start time matches` (pid reuse). A supervisor that dies leaves `state.json` at `running`; the reader marks such a job `lost` (never `success`).
- **Delivery:** (a) `bash_output` is the polling tool; (b) a `## Operating context` line per newly finished job, exactly like finished agents; (c) optional later: per-round injection inside a long turn. A finished job is surfaced once (`notified` flag in `state.json`).
- **Agents:** reuse `BackgroundAgentManager` unchanged for in-process agents. Restart survival for agents is a **separate, later phase** (see 6); the first release does not change them.

## 4. Invariants (each needs a witness test before it ships)

| ID | Invariant | Enforced in | Witness |
|---|---|---|---|
| BJ1 | A background command is gated exactly like a foreground one: the gate decision is on the `run_bash` call, and the supervisor runs it through `run_bash_confined`; no other execution path exists | one spawn site, AST-pinned; gate runs before spawn | a refused command never creates a job dir; a real turn with a refused background command |
| BJ2 | A job can only be read or killed from the workspace that started it | job id bound to workspace hash; store lookup under that hash | foreign id refused; `..` and absolute ids refused |
| BJ3 | Wisp exiting never leaves an unowned process: every job has a max runtime and a reaper | supervisor deadline; startup reaper marks dead supervisors `lost` and kills their process groups | a job past its deadline is killed; a killed supervisor is reported `lost` |
| BJ4 | A result is never reported as success unless the exit status said so; a missing or unreadable state is `lost`/`unknown`, never `exited 0` | reader | truncated/absent `state.json` |
| BJ5 | A verification job counts as verification only if it started after the last edit | job records the edit counter at start; folding compares it | start-before-edit then finish: stale |
| BJ6 | Output is bounded and scrubbed: disk cap, tail-only delivery, secrets scrubbed on the way back like any tool result | supervisor cap; `bash_output` goes through the tool-result path (`scrub_secrets`) | a 100 MB producer stays under the cap; a printed token is scrubbed |
| BJ7 | Finite resources: a ceiling on live jobs per workspace and per user, and on retained finished jobs | registry | the (N+1)th job is refused with a clear error |
| BJ8 | Kill is reliable: group kill, escalating SIGTERM -> SIGKILL, reports what happened | supervisor | a job that traps SIGTERM still dies; its children die |
| BJ9 | The job store is bounded in age: finished jobs are pruned, running ones never | prune at start | age/size pruning test |

## 5. Phases (each shippable and probe-able)

| Phase | Deliverable | Exit criterion |
|---|---|---|
| P0 | `wisp/jobs/` store + supervisor + reader, **no tool wiring**; unit and real-process tests | BJ2-BJ4, BJ6-BJ9 witnessed with real processes; mutation probe clean |
| P1 | `run_bash(run_in_background)`, `bash_output`, `kill_bash` wired behind a setting (default off); one spawn site; operating-context line | BJ1 witnessed through a real turn; registry/schema/doc pins updated; tool-profile decision (see 7) |
| P2 | Verification-floor and ledger folding for background results (BJ5) | start-before-edit stale; floor and reasoning-core behaviour unchanged for foreground |
| P3 | Mid-turn delivery (inject at the next provider round) | a long turn learns of a finished job without polling |
| P4 | Restart survival for background **agents** | separate design; the durable run rows and leases are the starting point |

## 6. What is deliberately out of scope for the first release

- Streaming a *running* job's partial output into the model (polling `bash_output` returns the tail so far, from `out.log`).
- Windows. The supervisor relies on POSIX sessions and process groups; on other platforms the feature reports itself unavailable.
- Interactive jobs (stdin). Background jobs get no stdin.
- Changing how background agents run.

## 7. Decisions for the owner

1. **Default**: `background_jobs` setting default `off` until P2 lands (recommended), then `on`.
2. **Tool profile**: the default profile offers 11 core tools (about 1,700 schema tokens). Adding `bash_output` and `kill_bash` costs schema tokens on every call. Recommended: put them in `full` only and add the `run_in_background` argument to the existing `run_bash` schema (a few tokens), with `bash_output` offered only while a job exists.
3. **Where state lives**: `~/.local/state/wisp/jobs` (XDG state) vs. `~/.config/wisp/jobs`. Recommended: state dir; it is runtime data, not configuration.
4. **Max runtime default** (recommended 1 hour, hard cap 24 hours) and **concurrent job ceiling** (recommended 4 per workspace, 16 per user).
5. **Sandbox tier**: jobs use whatever tier `run_bash` would (Docker, then PTY, then host). On the host tier a detached job is unconfined for the filesystem exactly like a foreground command; say so in the tool result (as the foreground path already logs).

## 8. Risks

- Orphans: the point of surviving restarts is also the risk. BJ3 and BJ8 are the mitigations; they must be proven with real processes, not mocks.
- A second path to execution: BJ1's single spawn site and its AST test.
- Secrets in `out.log` on disk: 0700 directory, bounded retention, scrub on delivery; the log itself is unscrubbed on disk (stated limit).
- Staleness errors (BJ5) would let a stale pass satisfy the floor: the reason P2 exists as its own phase.

## 9. What I did not verify

Whether each sandbox provider (`DockerSandbox`, `PtySandbox`, host) reacts to SIGTERM on the supervisor by killing the command (needed for BJ8; checked in P0 with real processes). Whether the REPL's per-turn event loop affects spawning (it should not: spawn is `subprocess`, not a task). Disk and fd behaviour under many concurrent jobs.
