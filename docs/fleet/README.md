# The fleet: one manifest, read-only status, workers over MCP

Wisp is the orchestrator over a set of sibling repos. `wisp.fleet.toml` at the repo root is the single source of
truth for which repos exist, what role each plays, and how wisp may reach it. Everything here is **read-only
reporting plus config rendering**: nothing commits, pushes, switches branches or deletes.

## Commands

```bash
wisp fleet status                       # one table: branch, dirty, unpushed, no-remote, stashes, behind
wisp fleet doctor --strict              # status plus git repos under the scan roots that the manifest omits; exit 1 on any problem
wisp fleet status --json                # machine-readable
wisp fleet status --fetch               # git fetch each repo first (updates remote refs only)
wisp fleet workers                      # print the MCP servers the manifest declares (writes nothing)
wisp fleet workers --write ~/.config/wisp/mcp.json   # merge them into an existing MCP config
```

Run it as `.venv/bin/python -m wisp fleet ...` (or `~/.venvs/wisp/bin/python -m wisp ...`): a stale `wisp` on PATH
that cannot import the package fails without saying why.

Exit codes: `0` ok (or problems found without `--strict`), `1` problems with `--strict`, `2` unreadable manifest or
bad arguments.

## The manifest

```toml
[fleet]
scan = ["~/active", "~/workspace", "~/dev"]   # roots `doctor` searches for repos the manifest omits

[[repo]]
name = "gump"
path = "~/active/gump"
role = "agent"          # orchestrator | harness | runtime | agent | tool | app | archive
# remote_required defaults to true, and to false for role "archive"

[repo.worker]           # optional; only on roles that are driven (not orchestrator or archive)
command = ["/path/to/python3", "-m", "gump.mcp_server"]
timeout_seconds = 120
[repo.worker.env]
GUMP_ENV_FILE = "~/active/gump/.env"           # secrets live in a file, never in this manifest
[repo.worker.tool_risk]
gump_verify = "read"                            # read | write | exec | network | privileged
gump_fix = "exec"
```

A manifest must have **exactly one** `orchestrator`, unique names, valid roles and well-formed worker tables, or
`wisp fleet` refuses to run and says why. It never guesses a default.

## What `status` reports

| Problem | Meaning |
|---|---|
| `no-remote` | no remote configured: commits exist only on this machine (not required for `archive`) |
| `no-upstream` | the branch has commits on no remote |
| `unpushed` / `behind` | ahead of / behind its upstream |
| `dirty` | uncommitted paths |
| `stash` | stashes exist |
| `missing` / `not-a-repo` | the manifest points at a path that is absent or has no `.git` |

## Workers

A worker is an MCP stdio server that shells out to the repo's own CLI, so that CLI's guards apply unchanged. Wisp
connects through its normal MCP manager, so allowlist, origin pinning and first-use consent still apply
(`docs/adr/2026-10-04-fleet-worker-contract.md`, `docs/adr/2026-09-04-plugin-mcp-trust.md`).

Rendered entries carry `managedBy: wisp-fleet`. `--write` replaces only entries with that mark, keeps every
hand-written server, and refuses to overwrite a hand-written server of the same name. A corrupt existing config is
left untouched.

Current workers: `always-on-worker` (`worker_status`, `worker_plan`, `worker_events`, `worker_approvals`,
`worker_audit`, `worker_run`) and `gump` (`gump_verify`, `gump_trajectory`, `gump_task`, `gump_fix`). By design they
expose **no** way to grant an approval, record the human's decision on a fix, or run an arbitrary command, and no
path, model or credential arguments; tests in each repo pin that surface.

Set `WISP_STRICT_ENV=1`: without it wisp starts MCP servers with its full environment, API keys included.

### Adding a worker

1. In the worker repo, add a stdio MCP shim that shells out to its own CLI over a reviewed allowlist. Copy the
   pattern in `always-on-worker/src/always_on_worker/mcp_server.py`; test it with the real CLI, not a mock.
2. Put no secret in the manifest: pass an env file path.
3. Add a `[repo.worker]` table, run `wisp fleet workers`, read the output, then `--write`.
4. Drive it once through wisp's MCP client under `WISP_STRICT_ENV=1` and confirm a forbidden call is refused.

## Pre-push check

`.githooks/pre-push` runs `wisp fleet doctor` before every push from this repo. Enable it once per clone:
`git config core.hooksPath .githooks`. It is advisory by default so a dirty sibling repo cannot block an unrelated
push; `WISP_FLEET_ENFORCE=1` makes any problem block the push. If the check cannot run, the hook says so loudly
instead of passing silently.
