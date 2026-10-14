# Removed in M0: every front end except the CLI and the REPL (2026-10-14)

**Decision (owner, 2026-10-14):** Wisp is built from the CLI and the REPL first; other front
ends come back only when that core is solid. Code that no kept entry point reaches is removed,
not archived in the tree: an archive folder is still collected, linted, pinned and read.

**Restore anything below:** it is all at the tag `checkpoint/2026-10-14/main-pre-m0-subtract`.

```bash
git checkout checkpoint/2026-10-14/main-pre-m0-subtract -- wisp/server tests/test_server_routes.py
```

## What went

| Removed | What it was | Why it could go |
|---|---|---|
| `wisp/server/` (4.7k lines), `wisp server` | FastAPI + WebSocket API server | Not a CLI/REPL path. Its only kept consumer was the composition root's `ApprovalBridge`, used by REST routes only (`WISP_REST_APPROVAL`, default off). |
| `wisp/tui/` (2.0k), `wisp/transport/tui.py`, `wisp tui` | Textual full-screen app | Not the REPL. The one screen the REPL uses, the subagent monitor, moved to `wisp/repl/subagent_monitor.py`. |
| `wisp/dashboard/` (1.6k), `/dashboard`, the presence file | Local web dashboard | Started from the REPL, but a web page, not the REPL. The REPL no longer writes a presence file. |
| `wisp/acp_*.py` (1.0k), `wisp acp` | Zed editor (ACP) adapter | Not the CLI/REPL. |
| `wisp/app_server.py`, `wisp/supervisor.py`, `wisp/runtime_protocol.py` | JSON-RPC app server | Reached only by each other. |
| `wisp/transport/websocket.py`, `WISP_WS_AUTO_APPROVE` | WebSocket transport | Used only by the server. |
| `wisp/arena.py`, `wisp/background_agent.py`, `wisp/suggestion_watcher.py`, `wisp/completion.py` | Server-route helpers | Reached only from server routes. (`wisp completion` the subcommand is unaffected: it never used `wisp/completion.py`.) |
| `wisp/mcp_servers/vscode_server.py`, `wisp mcp add-vscode` | VS Code MCP server | An editor integration. |
| `scripts/authorization_parity_measurement.py` | Agent-vs-REST parity instrument | One of its two paths is gone. |
| `fastapi`, `uvicorn`, `websockets` dependencies | | Nothing kept imports them. `textual` stays for the monitor. |

`wisp -p/--print` used to try a server on port 8000 first; it now always runs in-process.

## Tests

Whole files that tested only removed code were removed (`test_server_*`, `test_tui_*`,
`test_acp_*`, `tests/dashboard/`, the REST approval and REST authorization reliability files,
the external-input-path file, and others; the list is in the PR). In mixed files only the tests
that drove a removed module were removed; every agent-side check stays, including the
agent's half of the authorization parity table and the no-approver predicate.

Derived pages: `WISP_REST_APPROVAL` and `WISP_WS_AUTO_APPROVE` left `CURRENT_FLAGS.md`; the
`DiskTooFull` row left `register.md`; findings and open items whose tripwire file was removed
keep their row, with the tripwire set to `—` and the reason in the source cell. ADR-0061 R1
("the round-trip is pinned against a stub channel, not a live client") is SUPERSEDED: the path
it concerned no longer exists.

## The apps and the server deployment (second commit, owner's yes for the CI files)

| Removed | What it was |
|---|---|
| `wisp-desktop/` (164 files) | Electron macOS app; it bundled `wisp/server` as its backend |
| `android/` (40 files) | Android remote-control client of the server |
| `vscode-extension/` (10), `warp-integration/` (5) | Editor and terminal clients of the server |
| `Dockerfile`, `docker-compose.yml`, `nginx.conf` | Deployment of `wisp server` (`CMD ["wisp", "server", ...]`) |
| `.github/workflows/desktop-ci.yml`, the `test-android` job in `ci.yml` | Their CI |

`release.yml` no longer builds an APK or a Docker image; it now installs `.[dev]` with full
history, as `ci.yml` does (before, it installed without the dev extras on a shallow clone, so its
test step could not have passed). `docker/wisp-sandbox.Dockerfile` stays: `run_bash` uses it.

Found while removing them: the `subagent_start` event's capability report had one reader, the
desktop `SubagentPanel`. The REPL does not show it, so it is now built and not read; recorded for
the dead-code pass.

## Not in this change

- Modules no entry point reaches that are not front ends (about 5.8k lines, listed by the
  reachability audit): a separate change, each checked on its own.
- `wisp_net/` (6.7k lines): a separate product in this repo; where it lives is the owner's call.
- `uv.lock` still lists `fastapi`, `uvicorn` and `websockets`. CI installs with pip and does not
  read it; regenerating a lockfile needs the owner's yes.
