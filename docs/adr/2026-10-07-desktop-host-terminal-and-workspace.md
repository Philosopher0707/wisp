# ADR 2026-10-07: the desktop app's host terminal, its project-folder allowlist, and cross-store sessions

Status: accepted (the owner decided each point explicitly). Applies to `wisp-desktop/` and `wisp/server/`.

## 1. The Terminal tab is the user's shell, not an agent tool

**Context.** The first Terminal tab called `POST /api/bash`. The server's default policy (AUTO_EDIT) refuses that route over REST
("no approver is present"), so the tab showed an error and ran nothing. That refusal is correct for an agent and wrong for a human
typing in their own app.

**Decision.** The tab runs the user's **login shell on the host**, on a pseudo-terminal, started by the Electron main process
(`src/main/terminal.ts`). It does **not** pass through the agent's tool policy, and that policy is **unchanged**: the agent is
still refused.

**Mechanism.** macOS `script` needs a real terminal on stdin and Node has only pipes (it fails with
`tcgetattr/ioctl: Operation not supported on socket`), and a native pty module would have to be rebuilt per Electron release. A
small Python helper (`PTY_HELPER` in `terminal-policy.ts`) runs on the app's own bundled interpreter, forks the shell onto a pty
and relays bytes; window-size changes travel on fd 3. The renderer uses xterm.js.

**Guards.** The IPC handlers accept only the main window's renderer. The shell path comes from `$SHELL` only if listed in
`/etc/shells`, and is passed as argv, never inside code. The working directory must be an absolute, existing directory inside the
user's home (no `..`). The environment drops `WISP_API_KEY`, `ELECTRON_*` and `NODE_OPTIONS`. Input is capped; output is kept as
a bounded scrollback; the shell is killed with the window.

**Consequence (accepted risk).** Anything that can run code in the app window (a compromised renderer dependency, an XSS) can
type into a shell that has the user's rights. The agent's policy does not protect this path. Mitigations are the sandboxed
renderer and the fact that the browser tab is a separate, isolated view.

**Revisit if** the app ever renders untrusted HTML in the main renderer, or exposes IPC to remote content.

## 2. Switching the project folder

**Context.** Three independent faults made the folder selector look dead: the backend refused any folder outside the *current*
workspace (`WISP_ALLOWED_WORKSPACE_ROOTS` defaults to it); about twenty route modules copied `WORKSPACE_ROOT` at import time so
an accepted switch never reached Diff, Files, git or the shell routes; and the UI ignored a refusal.

**Decision.** The desktop app starts the backend with `WISP_ALLOWED_WORKSPACE_ROOTS=$HOME` (an explicit environment value wins),
because the user picks the folder in a native dialog. `workspace._rebind_workspace_root` rebinds, by identity, every
`wisp.server*` module still holding the old value and clears the caches built for the old folder. The UI shows the refusal. The
chosen folder is remembered in `userData/prefs.json` (main process), not localStorage, and a remembered folder the backend refuses
is forgotten.

**Not changed.** Outside the desktop app the default allowlist is as before.

## 3. Sessions from every store, read-only

**Context.** Sessions live in per-workspace databases, so the app (workspace `~/.wisp/workspace`) showed none of the user's history.

**Decision.** `GET /api/sessions` merges the app's store with `~/.config/wisp/wisp.db` and `~/.wisp/wisp.db`, opened `mode=ro`.
Opening a session copies it into the app's store; sources are never written (tests hash them before and after). Delete and rename
are refused (409) for sessions that exist only elsewhere. Untitled sessions are labelled from their first text message.

**Not covered.** Per-project stores are not scanned; that needs a registry of known workspaces.

## Evidence

`tests/test_workspace_switch.py` (7; three fail if the rebind is removed), `tests/test_session_sources.py`,
`tests/test_git_diff_route.py`, `wisp-desktop/src/main/terminal-policy.test.ts` (includes a real pty: tty, resize, exit status),
and `wisp-desktop/scripts/verify-packaged.mjs` against the built `.app`.
