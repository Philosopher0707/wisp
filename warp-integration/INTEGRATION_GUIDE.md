# Wisp + Warp Integration Guide (OSC 777)

This document describes how to integrate **Wisp** (your open-source Ollama-based coding agent) with **Warp Terminal** using Warp's built-in OSC 777 protocol — **no Oz subscription required**.

---

## What You Get

With this integration, running `wisp --warp-mode` in Warp gives you:

| Feature | How | Status |
|---------|-----|--------|
| Agent toolbelt toolbar | `CLIAgent::Wisp` detection | ✅ |
| Footer status chips (In Progress / Blocked / Done) | OSC 777 `session_start` / `permission_request` / `stop` | ✅ |
| Inline approval banners with [Approve] [Reject] | OSC 777 `permission_request`/`permission_replied` | ✅ |
| Ctrl+G rich input composer | `IdlePrompt` event | ✅ |
| Desktop notifications on completion | `Stop` event | ✅ |
| Code review panel | Built-in for CLI agents | ✅ |
| Vertical tab metadata ("Wisp · project") | `SessionStart` with project/cwd | ✅ |
| Session sharing URL | Built-in for CLI agents | ✅ |

**What you DON'T get (and why):**
- Native diff/code/thinking blocks — These require `RichContentMetadata` changes to Warp's core Rust. Not possible without rebuilding.
- Native "Run this command" buttons — Same restriction.
- Wisp replaces Oz as LLM backend — Not possible; Oz cloud is Warp's core architecture.

---

## How It Works

```
┌──────────────┐      OSC 777 events        ┌──────────────────────┐
│  wisp process │ ─────────────────────────▶│  Warp PTY Listener   │
│  stdout       │                           │  (cli_agent_sessions)│
│               │                           │                      │
│  stdin ◀──────│── "approve" / "reject" ───│  Inline Banners      │
│               │                           │  Footer Chips        │
│               │                           │  Notifications       │
└──────────────┘                           └──────────────────────┘
```

Wisp emits **OSC 777 escape sequences** to stdout. Warp's PTY listener parses these and renders native UI.

When user clicks Approve/Reject in Warp, Warp sends the text "approve" or "reject" to Wisp's stdin. Wisp's runtime reads this and continues or aborts the tool call.

---

## Files Added/Modified

### Wisp-side (this repo)

| File | Role |
|------|------|
| `wisp/transport/warp.py` | **WarpTransport** — emits OSC 777 events, reads approvals from stdin |
| `wisp/transport/__init__.py` | Exports (optional, just for consistency) |
| `wisp/entry.py` | Modified `_run_cli` to use WarpTransport when `--warp-mode` |
| `wisp/__main__.py` | Add `--warp-mode` flag parsing |

### Warp-side (in your warp-source checkout)

| File | Change |
|------|--------|
| `app/src/terminal/cli_agent.rs` | Add `CLIAgent::Wisp` variant |
| `app/src/terminal/cli_agent_sessions/listener/mod.rs` | Add Wisp to `is_agent_supported()` and `create_handler()` |
| `app/src/terminal/cli_agent_sessions/plugin_manager/mod.rs` | Add `wisp` module import and `WispPluginManager` dispatch |
| `app/src/terminal/cli_agent_sessions/plugin_manager/wisp.rs` | **New**: `WispPluginManager` (no-op install — OSC 777 needs no plugin) |
| `crates/warp_core/resources/bundled/svg/wisp.svg` | **New**: Wisp icon |
| `crates/warp_core/src/ui/icons.rs` | Register `Icon::WispLogo` |

---

## Step-by-Step Setup

### Step 1: Apply the Warp Patch

In your `warp-source/` checkout:

```bash
# Apply the patch (it's in your wisp repo)
cd /path/to/warp-source
git apply /path/to/wisp/warp-integration/wisp-cli-agent-v2.patch

# Verify files changed
git diff --stat

# Build Warp
cargo build --release
```

If the patch doesn't apply cleanly, apply the changes manually using the diff in the patch or the instructions below.

### Manual Changes (if patch fails)

**A. Add CLIAgent::Wisp** (`app/src/terminal/cli_agent.rs`)

Add the `WISP_COLOR` constant after `CURSOR_COLOR`:

```rust
/// Wisp brand color — indigo/violet (#6366F1)
const WISP_COLOR: ColorU = ColorU {
    r: 99, g: 102, b: 241, a: 255,
};
```

Add `Wisp` to the `CLIAgent` enum after `CursorCli`.

Add the `command_prefix()` mapping: `CLIAgent::Wisp => "wisp"`.

Add the `agent_name()` mapping: `CLIAgent::Wisp => "Wisp"`.

Add the `icon()` mapping: `CLIAgent::Wisp => Some(Icon::WispLogo)`.

Add the `skill_providers()` mapping: `CLIAgent::Wisp => &[SkillProvider::Agents]`.

Add the `brand_color()` mapping: `CLIAgent::Wisp => Some(WISP_COLOR)`.

Add the `brand_icon_color()` mapping: `CLIAgent::Wisp => ColorU::new(0, 0, 0, 255)`.

**B. Add Wisp to OSC 777 support** (`app/src/terminal/cli_agent_sessions/listener/mod.rs`)

In `is_agent_supported()`, add `| CLIAgent::Wisp`.

In `create_handler()`, add `| CLIAgent::Wisp` to the first match arm.

**C. Add WispPluginManager** (`app/src/terminal/cli_agent_sessions/plugin_manager/mod.rs`)

Add `pub(crate) mod wisp;` and `use wisp::WispPluginManager;`.

In `plugin_manager_for()`, add:
```rust
CLIAgent::Wisp => Some(Box::new(WispPluginManager)),
```

Create `app/src/terminal/cli_agent_sessions/plugin_manager/wisp.rs`:

```rust
use super::*;

pub(crate) struct WispPluginManager;

impl CliAgentPluginManager for WispPluginManager {
    fn name(&self) -> &'static str {
        "wisp-warp"
    }

    fn minimum_plugin_version(&self) -> &'static str {
        "0.1.0"
    }

    fn install_instructions(
        &self,
        _is_update: bool,
        _detected_command: Option<&str>,
        _detected_args: &[String],
    ) -> PluginInstructions {
        PluginInstructions {
            title: "Wisp Warp Plugin",
            subtitle: "Wisp uses OSC 777 — no plugin needed!",
            steps: &[PluginInstructionStep {
                description: "Run Wisp with --warp-mode:",
                command: "wisp --warp-mode",
                executable: true,
                link: Some("https://github.com/wisp/wisp"),
            }],
            post_install_notes: &[],
        }
    }

    fn install_plugin(
        &self,
        _command: &str,
        _args: &[String],
        _env_vars: Option<HashMap<String, String>>,
    ) -> Result<(), PluginInstallError> {
        Ok(()) // No plugin needed — OSC 777 is built in
    }
}
```

**D. Add Wisp SVG icon** (`crates/warp_core/resources/bundled/svg/wisp.svg`)

Create the file with the SVG content from the patch.

**E. Register icon** (`crates/warp_core/src/ui/icons.rs`)

Add `WispLogo` to the `Icon` enum and its mapping: `"bundled/svg/wisp.svg"`.

### Step 2: Wire --warp-mode into Wisp

**A. Modify `wisp/__main__.py`**

In the global flags section, add:
```python
    flags_warp = False
```

In `extract_global_flags`, add:
```python
            elif a == "--warp-mode":
                flags_warp = True
                i += 1
```

Update the `cmd_run` and `cmd_repl` calls to pass `warp_mode=flags_warp`.

Update `cmd_run` definition to accept `warp_mode` parameter.

Update `cmd_repl` definition to accept `warp_mode` parameter.

Add to help text:
```
  --warp-mode              Enable Warp terminal integration (OSC 777 protocol)
```

**B. Modify `wisp/entry.py`**

Update `_run_cli` to check `kwargs.get("warp_mode")` and instantiate `WarpTransport` instead of `CLITransport`.

### Step 3: Run

1. Build and run your patched Warp:
   ```bash
   cd warp-source
   cargo run --release
   ```

2. In Warp terminal, run:
   ```bash
   wisp --warp-mode "refactor the auth module to async"
   ```

3. You should see:
   - Footer chip: "⚡ Wisp · In Progress"
   - Inline banner when Wisp wants to edit a file
   - Footer: "✅ Wisp · Done" when finished

---

## OSC 777 Event Reference

Wisp emits these events via `WarpTransport`:

### `session_start`

Sent when Wisp starts.

```json
{
  "agent": "wisp",
  "event": "session_start",
  "session_id": "a1b2c3d4e5f6",
  "cwd": "/Users/me/project",
  "project": "my-app"
}
```

Warp shows: Footer chip "Wisp · In Progress", auto-opens Ctrl-G if setting enabled.

### `idle_prompt`

Sent when Wisp is at REPL waiting for input.

```json
{
  "agent": "wisp",
  "event": "idle_prompt",
  "session_id": "a1b2c3d4e5f6"
}
```

Warp shows: Ctrl-G rich input editor with placeholder.

### `prompt_submit`

Sent when user submits a prompt.

```json
{
  "agent": "wisp",
  "event": "prompt_submit",
  "query": "refactor to async",
  "session_id": "a1b2c3d4e5f6"
}
```

Warp shows: Prompt message in agent view, status remains "In Progress".

### `permission_request`

Sent before file edit or shell command.

```json
{
  "agent": "wisp",
  "event": "permission_request",
  "tool_name": "edit_file",
  "tool_input": {"file_path": "src/db.rs"},
  "summary": "Make query() async",
  "session_id": "a1b2c3d4e5f6"
}
```

Warp shows: Inline banner with Approve/Reject buttons. Footer: "Wisp · Blocked".

### `permission_replied`

Sent after user clicks Approve/Reject.

```json
{
  "agent": "wisp",
  "event": "permission_replied",
  "session_id": "a1b2c3d4e5f6"
}
```

Warp shows: Banner dismisses, status returns to "In Progress".

### `stop`

Sent when task completes.

```json
{
  "agent": "wisp",
  "event": "stop",
  "query": "refactor to async",
  "response": "Done! Made query() async.",
  "summary": "Query refactored",
  "session_id": "a1b2c3d4e5f6"
}
```

Warp shows: Footer "✅ Wisp · Done", desktop notification (if unfocused).

---

## Troubleshooting

**Problem:** Wisp runs but no footers/banners appear.

**Solution:** Check that your Warp patch is applied and built. Verify `is_agent_supported()` includes `CLIAgent::Wisp`. Also verify Wisp is emitting OSC 777 — you can test by running:

```bash
printf '\033]777;{"agent":"wisp","event":"session_start"}\007'
```

This should immediately show a "Wisp · In Progress" chip in Warp.

---

## Architecture Notes

- Warp's `ThirdPartyHarness` trait is for **cloud-mode** agents (Oz spawning Claude/Codex). Wisp runs as a **CLI agent** in the terminal — different code path, same result.
- OSC 777 is parse-only on Warp's side. It does not execute arbitrary code.
- The `tool_input_preview` field is extracted from `tool_input.file_path` or `tool_input.command` by Warp's parser.
- Desktop notifications require macOS notification permissions (Warp already has these).

---

## License

This integration guide and associated Wisp transport code are MIT licensed, same as Wisp itself.
