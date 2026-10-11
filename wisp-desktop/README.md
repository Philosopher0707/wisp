# Wisp Desktop

A macOS app (Electron) around the Wisp agent. The app is **self-contained**: it ships its own Python with wisp installed, so
the machine needs no Python, virtualenv or repository checkout.

```
Wisp.app
├─ Electron main process   src/main/       window, backend lifecycle, browser view, terminal, prefs
├─ Renderer (React)        src/renderer/   sidebar, header, chat, the workbench dock
└─ Resources/backend/python/…              relocatable Python 3.12 + wisp  →  FastAPI on 127.0.0.1 (Bearer key)
```

## What is in the window

| Area | What it does |
|---|---|
| **Sidebar (left)** | New session, search, plugins; sessions from **every Wisp store** on the machine; the model-provider switcher. Hiding it moves the macOS window buttons' space to the header. |
| **Header** | Session title (the only item that shrinks), status chips (project, git branch, sandbox, authority, connection), tools, and the dock toggles. Chips drop out by container width instead of overlapping. |
| **Dock (right)** | **Diff**, **Terminal**, **Browser**, **Files**. Clicking the active tab closes it; the edge resizes it. |
| **Project folder** | The folder button under the composer. Diff, Files and the terminal follow it; it is remembered across launches. |

## Run it in development

```bash
cd wisp-desktop
npm install
npm run dev            # electron-vite; the backend runs on the repo's .venv (or WISP_PYTHON)
npm run typecheck && npm test
```

Backend interpreter, in order: `WISP_PYTHON` → the bundled Python → the repo's `.venv` → system `python3`
(`src/main/backend-launch.ts`, with tests). The bundled interpreter is sealed: no bytecode written into the app, no user
site-packages, no inherited `PYTHONHOME`.

## Build and package

```bash
npm run package        # = npm run bundle && npm run build && electron-builder
```

1. `scripts/bundle-backend.sh` copies a uv-managed python-build-standalone 3.12 into `build/backend/python` and installs wisp
   and its dependencies into it (≈169 MB; needs `uv` and network). **It must run again whenever backend code changes**:
   a stale bundle starts fine and silently lacks routes the UI calls. `npm run package` does this for you; a bare
   `electron-builder` does not.
2. `electron-vite build` builds the app.
3. `electron-builder` produces `release/mac-arm64/Wisp.app`, a `.dmg` and a `.zip` (arm64). `scripts/adhoc-sign.cjs` ad-hoc
   signs the bundle in an `afterPack` hook (without it the bundle seal is invalid).

## Verify the artifact you ship

```bash
node scripts/verify-packaged.mjs [path/to/Wisp.app]     # default: release/mac-arm64/Wisp.app
```

Launches the **packaged** app with a throwaway HOME and user-data folder (it never touches your real app data) and checks, among
others: window opens, managed backend, `/api/health`, 401 with no/wrong key, `/api/sessions` rows carry their `source`,
`/api/git/diff`, session import and model-select routes exist, workspace switch refused outside the home folder and accepted
inside, the backend is the bundled Python, loopback only, no renderer errors, no orphan process after quit. It exits non-zero
on any failure. Run it on the `.app` that came out of the build, not on `out/`.

## Security model

- **Backend:** binds `127.0.0.1` only; every `/api/*` route except `/api/health` needs the per-launch Bearer key.
- **Renderer:** `contextIsolation` on, `sandbox` on, no Node integration. The auth header is built in one place (`useApi`);
  `authHeaderAuthority.test.ts` ratchets this.
- **Browser tab:** a separate `WebContentsView` with its own persistent partition, no preload, sandboxed, every permission denied,
  http(s) navigation only (`src/main/browser-url.ts`). It never sees the backend key or the app's IPC. It hides while a modal or
  approval is open, because a native view paints above web content.
- **Terminal tab:** your own login shell on a pty, running on the host, started by the main process. **It is not routed
  through the agent's tool policy** (that policy is unchanged and still governs the agent). Only the main window's renderer can
  talk to it, it is never given the backend key, its working directory must be inside your home folder, and it dies with the
  window. Anything that can run code in the app window could still send it keystrokes. See
  [ADR 2026-10-07](../docs/adr/2026-10-07-desktop-host-terminal-and-workspace.md).
- **Project folder:** the backend only switches into folders under `WISP_ALLOWED_WORKSPACE_ROOTS`, which the app sets to your
  home folder (an explicit value in the environment wins). A refused switch shows the server's reason.

## Where data lives

| What | Where |
|---|---|
| App preferences (remembered project) | `~/Library/Application Support/wisp-desktop/prefs.json` (0600, atomic writes) |
| Sessions the app owns | `<workspace>/.wisp/wisp.db`, the default workspace being `~/.wisp/workspace` |
| Sessions from the CLI/REPL | `~/.config/wisp/wisp.db` and `~/.wisp/wisp.db`, **read-only** to the app |
| Backend workspace | `~/.wisp/workspace` until you choose a project |

### Sessions across stores

Wisp keeps one database per workspace, so a session made from a terminal elsewhere is invisible to an app whose workspace is
different. The sidebar merges the app's store with the two global stores above (tagged `GLOBAL` / `HOME`). They are opened
`mode=ro`, so listing never writes. **Opening** a session copies it into the app's store (`POST /api/sessions/{id}/import`);
the original is never modified. Delete and rename are refused for sessions that only exist in another store. Per-project
stores (`<project>/.wisp/wisp.db`) are not scanned.

## Tests

| Layer | Command |
|---|---|
| Renderer + main logic | `npm test` (vitest): launch policy, diff parser, browser URL policy, terminal policy including a real pty, prefs, API shapes |
| Backend routes | `pytest tests/test_session_sources.py tests/test_git_diff_route.py tests/test_workspace_switch.py` |
| Packaged app | `node scripts/verify-packaged.mjs` |

Test launches pass `--user-data-dir` so they cannot read or write your real app data (Electron ignores `$HOME` for it).

## Known limits

- **Distribution:** ad-hoc signed only. Other Macs show a Gatekeeper warning (right-click → Open) and the auto-updater will not
  accept unsigned updates. Developer ID signing and notarization need credentials added as CI secrets; the release workflow
  does not build the Mac app yet. See [`docs/RELEASE.md`](../docs/RELEASE.md).
- arm64 only.
- The terminal restarts as a new shell if you change project (a prompt offers it); it is not killed for you.
- Assistant text in some imported sessions is already concatenated across tool steps in the stored data.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Sidebar empty / Diff tab errors in the packaged app | Stale bundled backend. Re-run `npm run bundle`, then package and `verify-packaged`. |
| CI `npm ci` fails with "Missing: esbuild@0.28.2" | Lockfile generated by a different npm than CI's (npm 10). Regenerate with `npx -y npm@10 install --package-lock-only`. |
| `codesign --verify` fails on the built app | The `afterPack` hook did not run (`mac.identity: null` needs `scripts/adhoc-sign.cjs`). |
| "Backend Startup Failed" dialog | It shows the backend's last output; check which interpreter was used (`describeStartupFailure`). |
