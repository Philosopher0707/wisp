# The Wisp dashboard

A local, read-only web page over what Wisp measures about itself. Started 2026-10-08 from one finding: *there was no measured number for how often the agent solves a task*, so every fix was chasing a symptom seen in a session.

```
python -m wisp.dashboard serve            # http://127.0.0.1:8765, Ctrl-C to stop
python -m wisp.dashboard bench --label default --repeats 3     # model, provider, base URL and key name come from WISP_MODEL, WISP_PROVIDER, WISP_API_BASE, WISP_API_KEY
```

Inside a REPL: `/dashboard` serves it from that session on a background thread (`/dashboard status`, `/dashboard stop`); `WISP_DASHBOARD=1 wisp repl` does it at start. Either way the page opens at the **Live** tab's URL.

`serve` shows; `bench` measures. **The dashboard never starts a benchmark and never spends a token**; `bench` does, so only a person runs it.

## What each tab answers

| Tab | Question | Source |
|---|---|---|
| Overview | What is the measured accuracy, and what should I look at first? | benchmark rows, plus alerts computed from every section |
| Live | Which wisp sessions are running right now, with which model, and which sessions changed in the last 15 minutes? Refreshes every 3 s. | each session's own announcement file (below), `ps` for sessions that do not announce, the workspace databases |
| Accuracy | How often does each model, under each harness configuration, solve the judge's tasks? | `<bench dir>/*.jsonl` (default `~/.local/state/wisp/bench`, or `$WISP_BENCH_DIR`) |
| Models | Which models ran in real sessions, how much, when? | each workspace's `.wisp/wisp.db` (`sessions`) |
| Real use | Which tools, how often not ok, what share of turns ended with `done`, background and graph run outcomes, harness events in `.agent/runtime.log` | `.wisp/audit.jsonl`, `.wisp/wisp.db`, `.agent/runtime.log` |
| Harness | Which commit, which flags a new session would use, the tokens every request carries, the merged pull requests | git (offline), `WispConfig`, the tool and skill schemas |
| Findings | What is open, fixed, refuted? | `docs/harness/field-observations-*.md` and `findings-*.md` |
| Learning | Does the agent keep what it learns? | `~/.config/wisp/memory.json` (counts), its backups, `agent_memory`, captured skills, the `remember` tool's outcomes |

## Live sessions and where the model comes from

macOS and Linux do not let a script read another process's environment (checked: `ps eww` and `sysctl kern.procargs2` return no variables for a process of the same user), so a session **announces itself**: a REPL writes `~/.local/state/wisp/live/<pid>.json` (override with `$WISP_LIVE_DIR`) at start, rewrites it every 5 s and removes it on exit. The file holds the pid, the workspace, the session id, the start time, the heartbeat, and the model and provider **as that session's own environment resolved them**, with the source (`env WISP_MODEL`, or `config`). It never holds a prompt, a message or a key. Another session sweeps files of dead pids when it starts.

| State | Meaning |
|---|---|
| running | announced within the last 20 s |
| silent | the process exists but stopped reporting (a stalled loop, a suspended laptop) |
| not announced | found by `ps` (`python -m wisp <subcommand>`), an older version that does not announce: the model is unknown, not guessed |

"A session started now would use" is `WISP_MODEL` / `WISP_PROVIDER` from the dashboard process's environment, then from `~/.config/wisp/.env` (only those keys are read from that file). `bench` takes its defaults from the same place and prints the model, its source and "this spends tokens" before it starts; `--model` overrides.

Limits: a later `/model` switch is not written to the file; a session that crashed with `kill -9` shows as gone as soon as its pid is, but its file stays until another session starts; `ps` and `lsof` are called with fixed arguments, never through a shell.

## How the accuracy number is made, and what it does not mean

`bench` runs the judge's tasks (`wisp.judge`): each in a throwaway workspace by a child `wisp --print`, then judged by a hidden check the agent never saw. Around that it adds what a number needs to be believable:

- **The child runs the checkout you name** (`--wisp-path`, PYTHONPATH is set) and every row records the commit and whether the tree was dirty. The venv's own `wisp` can be another checkout (finding I-14). Benchmark a commit from a clean `git worktree`.
- **A fresh empty HOME per attempt**: one task's memory, facts and captured skills cannot leak into the next, and your own `~/.config/wisp` is never written (`--no-isolate-home` turns this off).
- **Infrastructure is not the agent's failure.** A run that died of a 429, a 402 or a timeout is retried with a backoff and kept apart: the pass rate is over *scored* runs, the infra rate is shown beside it, and a high infra rate is called out as a biased sample. (The judge itself now treats a 429 as INFRA; before, a rate-limited run could be scored as the agent's NO-OP.)
- **Every number carries its interval** (Wilson 95%), and configuration-against-configuration comparisons use a Newcombe-Wilson interval with an explicit "not distinguishable from noise" verdict. With fewer than 10 scored runs the page shows `solved/scored`, not a percentage.
- **Over-claims and under-claims are different things.** Reporting success on unsolved work is the dangerous one; saying "not ok" on solved work is cautious (often a run cut short by a provider error at the end).

**Scope:** the judge has 13 small, self-contained tasks (a few are traps that reward hard-coding). This measures basic competence and honesty, **not ability on a real repository**; a model can score near the ceiling here and still fail on real work. It is a floor and a regression guard, not a verdict.

## Privacy and safety

- Aggregates only: counts, tool and model names, dates and statuses. **No prompt, message, file content, fact, skill body or key is returned** (a test seeds private text and keys into every source and asserts none appears in any response).
- Binds `127.0.0.1` only (there is no option to bind elsewhere), refuses any request whose `Host` is not its own (DNS rebinding), serves only GET/HEAD and a fixed list of `/api/<section>` names, sets `no-store`, `nosniff` and a nonce-only CSP, and the page renders data through `textContent`, never `innerHTML`.
- SQLite is opened read-only. Nothing is written. No outbound request is made.
- Workspace discovery is a bounded walk of `~/active`, `~/workspace` and the working directory (not `~/dev`: the checkouts there hold the artifacts of test runs, which showed up as models `test-model` and `mock-t`); `_scratch` and `_archive` are skipped. Add others with `--workspace`.

## Limits

- Real-use panels cover only the workspaces it can read; a workspace in a protected folder (macOS asks for permission) is missing, not zero.
- "Not ok" is the tool's own status: a shell command that exits non-zero is still an ok `run_bash` call.
- Token overhead is an estimate (serialised size / 4) for the first discovered workspace's skills.
- The benchmark does not record tokens or cost per run yet.
