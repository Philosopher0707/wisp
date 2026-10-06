# Developer Quickstart (M7)

## 1. Run something

```bash
wisp repl                          # interactive (personal profile default)
wisp run "summarize AUTH.md"       # single-shot
wisp task start "add login page"   # durable task handle
```

## 2. Review before it touches anything

```bash
wisp task review <task-id>                  # files, risks, obligations, budget
wisp task approve-plan <task-id> --scope all
wisp policy dry-run write_file --args '{"path":"x.py"}'
```

## 3. Stay safe by default

- `y` approves once, `v` shows the diff, `c` cancels (recorded, not lost).
- Quarantined checkouts can't run writes — not even in full mode.
- Secrets are redacted from audit, traces, and diagnostics automatically.

## 4. Prove what happened

```bash
wisp trace <task-id>               # span tree with timings
wisp task export-evidence <task-id> --out evidence.json
wisp audit verify                  # hash chain intact?
```

## 5. Go offline / managed

```bash
wisp policy import bundle.json     # air-gap intake (signature verified)
wisp policy health                 # cache status, expiry, revocation_seq
```

Secure default profile for sensitive work: run with the
`offline-secure` or `read-only-review` profile posture (see
`wisp/task/profiles.py`), or `ci-headless` in pipelines.

## 6. Use the macOS app

```bash
cd wisp-desktop && npm install
npm run package                      # bundles Python + wisp, builds, writes release/Wisp-<version>-mac.dmg (arm64)
node scripts/verify-packaged.mjs     # checks the built app: launch, auth, session/diff/workspace routes, no orphan process
open release/mac-arm64/Wisp.app
```

The app ships its own Python. Pick your project folder with the folder button under the composer (it must be inside your home folder; it
is remembered). The Terminal tab is your own shell on the host, not the agent's `run_bash`. It is ad-hoc signed, so on another Mac use
right-click, Open. Details: [`wisp-desktop/README.md`](../wisp-desktop/README.md).
