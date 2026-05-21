---
name: qa-cli
description: >
  QA tests for the Wisp CLI. Covers single-shot execution, REPL interaction,
  slash commands, session management, tool execution, config commands, and server mode.
  Uses tuistory for interactive testing and shell commands for non-interactive scenarios.
---

# QA: Wisp CLI

## Testing Target

The CLI is tested by:
1. Running `wisp <command>` in a terminal (non-interactive workflows)
2. Using tuistory for the interactive REPL: `wisp repl`, `wisp tui`, `wisp tui --ink`

The CLI is built by running `pip install -e .` in the repo root.

## Test Mode: MockProvider vs Ollama

For deterministic, fast QA: use MockProvider (`--model mock-model`). This generates predictable responses like "hello world" or "[mock: no more responses]".

For integration testing: use a real Ollama model (default: `deepseek-v4-pro:cloud` or any available model). Requires Ollama running at localhost:11434.

Prefer MockProvider for rapid QA cycles. Use Ollama only for smoke-testing the real LLM pipeline.

## App-Specific Config Notes

- CLI entry point: `python -m wisp` or `wisp` (after `pip install -e .`)
- Session storage: SQLite at `.wisp/wisp.db` (relative to cwd)
- Config file: `~/.config/wisp/config.json`
- Agent sessions survive across runs (stored in DB)
- Permission modes: `FULL` (default dev), `ASK_ALL`, `AUTO_EDIT`, `READ_ONLY`

## Available Test Flows

### Flow 1: CLI Startup & Basic Commands
**Run when diff touches:** `wisp/__main__.py`, `wisp/entry.py`, `setup.py`, `pyproject.toml`

Steps:
1. Run `wisp --version` → verify version number printed
2. Run `wisp --help` → verify help text with subcommands listed
3. Run `wisp config --validate` → verify "Configuration is valid" or no custom config

Evidence: terminal output as text snapshot.

### Flow 2: Single-Shot Execution
**Run when diff touches:** `wisp/core/engine.py`, `wisp/providers/`, `wisp/core/runtime.py`, `wisp/entry.py`

Steps:
1. Run `wisp --model mock-model "say hello"` → verify agent responds with mock output
2. Run `wisp --print "say hello" --model mock-model` → verify JSON output with status and result
3. Run `wisp --print "what is 2+2" --model mock-model --output-format stream-json` → verify stream-json format

Evidence: terminal output as text snapshot.

### Flow 3: REPL Initialization & Slash Commands
**Run when diff touches:** `wisp/transport/cli_v2.py`, `wisp/transport/cli.py`, `wisp/commands.py`, `wisp/entry.py`

Steps:
1. Start REPL: run `wisp repl --model mock-model`
2. Wait for banner and prompt to appear (may take a few seconds)
3. Type `/help` → verify slash command list printed
4. Type `/session` → verify session info displayed
5. Type `/tokens` → verify token usage info displayed
6. Type `/clear` → verify "Conversation cleared" message
7. Type `hello` (a prompt) → verify agent responds (with mock: "[mock: no more responses]")
8. Type `/exit` → verify REPL exits cleanly

Evidence: tuistory text snapshots at each step.

### Flow 4: Session Management Commands
**Run when diff touches:** `wisp/adapters.py`, `wisp/infra/store.py`, `wisp/core/runtime.py`

Steps:
1. Run `wisp "test prompt" --model mock-model`
2. Run `wisp session list` → verify at least one session listed
3. Run `wisp session show <id>` where `<id>` is the first session ID → verify session details displayed
4. Run `wisp session compact <id>` → verify compaction message
5. Run `wisp session trim <id> 5` → verify trim message

Evidence: terminal output as text snapshot.

### Flow 5: Tool Execution in REPL
**Run when diff touches:** `wisp/tools/*.py`, `wisp/core/engine.py`, `wisp/transport/cli_v2.py`, `wisp/tool_executor.py`

Steps:
1. Start REPL: `wisp repl --model mock-model`
2. Type a prompt that triggers a tool call (e.g., `read the README` if mock provider can trigger tools, or use a real model)
3. For deterministic testing: run a single-shot that invokes a tool:
   ```bash
   wisp --model mock-model --print "what files are in this directory?"
   ```
   (MockProvider may not trigger tools; this flow is best tested with a real model or a tool executor unit test)
4. Verify tool result is formatted correctly in the output

Alternative for real testing:
- Create a test Python file with simple content
- Use the REPL with auto-approve: `wisp repl --model qwen2.5-coder --auto-approve`
- Type "read the file test.py" and verify the file content is read and displayed

Evidence: terminal output + any created test files.

### Flow 6: Config Commands
**Run when diff touches:** `wisp/config.py`, `wisp/__main__.py`

Steps:
1. Run `wisp config --set model=mock-model` → verify config set message
2. Run `wisp config` → verify model shown in output
3. Clean up: Run `wisp config --set model=deepseek-v4-pro:cloud` (restore default)

Evidence: terminal output as text snapshot.

### Flow 7: Model & Provider Commands
**Run when diff touches:** `wisp/providers/`, `wisp/ollama_client.py`, `wisp/entry.py`

Steps:
1. Run `wisp check --model qwen2.5-coder` → verify provider checks (Ollama checks connection)
   - If Ollama is not running, expect "not available" or connection refused
   - This is expected for MockProvider
2. Run `wisp check --model mock-model` → verify mock provider passes check
3. Run `wisp models` → verify model list (if Ollama available) or error (if not)

Evidence: terminal output as text snapshot.

### Flow 8: Server Mode Start/Stop
**Run when diff touches:** `wisp/server/**`, `wisp/entry.py`, `docker-compose.yml`

Steps:
1. Start server in background: `wisp server --port 9000 &` (use a non-default port to avoid conflicts)
2. Wait 3 seconds for startup
3. Verify server is listening: `curl -s http://localhost:9000/api/health` → expect `{"status":"ok"}`
4. Verify API key works (if WISP_API_KEY is set):
   ```bash
   curl -s -H "X-API-Key: $WISP_API_KEY" http://localhost:9000/api/prompt -H "Content-Type: application/json" -d '{"prompt":"hello","model":"mock-model"}'
   ```
   (If no API key set, server runs in dev mode with no auth)
5. Stop the server: `pkill -f "wisp server"` or kill the background PID

Evidence: terminal output + curl responses as text snapshots.

### Flow 9: Headless JSON Output
**Run when diff touches:** `wisp/__main__.py` (cmd_print), `wisp/entry.py`

Steps:
1. Run `wisp --print "hello world" --model mock-model` → verify JSON output
2. Parse JSON and verify fields: `status`, `data`, `metadata`
3. Run with `--quiet` → verify only JSON output, no stderr messages
4. Run with `--output-format stream-json` → verify streaming format

Evidence: terminal output as text snapshot (with JSON parsing).

### Flow 10: Negative / Error Handling Tests
**Run always (at least 1 per QA run)**

Steps:
1. Run `wisp repl --model nonexistent-model` → verify error or fallback behavior
2. Run `wisp session show fake-id` → verify "not found" message
3. Run `wisp` (no args) → verify help is shown
4. Run `wisp unknown-command` → verify error message about unknown command
5. Run `wisp --skill nonexistent-skill "test"` → verify skill not found message

Evidence: terminal output as text snapshot.

### Flow 11: Multi-Agent / Swarm
**Run when diff touches:** `wisp/multi_agent/`, `wisp/swarm.py`

Steps:
1. Run `wisp agents list` → verify available agent roles
2. Run `wisp swarm "test task" --max-parallel 1 --model mock-model` → verify swarm execution
3. Run `wisp agents status` → verify status output (may be empty if no active swarms)

Evidence: terminal output as text snapshot.

### Flow 12: Memory Commands
**Run when diff touches:** `wisp/memory.py`, `wisp/agent_memory.py`

Steps:
1. Run `wisp memory add "test fact"` → verify added
2. Run `wisp memory list` → verify fact shown
3. Run `wisp memory remove "test fact"` → verify removed
4. Run `wisp memory` → verify empty state or remaining facts

Evidence: terminal output as text snapshot.

### Flow 13: TUI Mode (Experimental)
**Run when diff touches:** `wisp/tui/`, `wisp/transport/tui.py`, `wisp-tui/`

Steps:
1. Build TUI if needed: `cd wisp-tui && npm run build`
2. Run `wisp tui` → verify Textual TUI launches (requires textual)
3. Run `wisp tui --ink` → verify React/Ink TUI launches
4. Press Ctrl+Q or q to exit

This flow is FLAKY -- TUI testing can fail due to terminal differences. If BLOCKED, note the reason and continue.

Evidence: tuistory text snapshot of TUI state.

## Per-Persona Variations

Since Wisp is a single-user local tool, there is only one persona (developer). The persona doesn't affect test flows significantly. However:

- **With WISP_API_KEY set**: Server mode requires auth; test that routes reject without key
- **Without WISP_API_KEY**: Server runs in dev mode (no auth); test that all API routes work without auth
- **With WISP_PERMISSION_MODE=read_only**: Test that write tools are blocked (use `/approve` to toggle, or set via config)

## Known Failure Modes

1. **Ollama not running.** If testing with a real model and Ollama is down, commands fail with connection errors. Fix: start Ollama with `ollama serve`, or use MockProvider (`--model mock-model`).
2. **TUI build missing.** `wisp tui --ink` requires `wisp-tui/dist/wisp-tui.mjs` to exist. Fix: `cd wisp-tui && npm run build`.
3. **Session DB locked.** If another Wisp process is running, SQLite may be locked. Fix: kill other wisp processes with `pkill -f wisp`.
4. **Slow first startup.** The first run may be slow as the model loads or the provider initializes. Fix: increase wait times in tuistory snapshots.
5. **Ink CI detection.** The Ink TUI detects CI environments and disables certain features. In CI, prefix with `env -u CI`.
6. **MockProvider no more responses.** MockProvider with no pre-configured responses returns "[mock: no more responses]". This is expected behavior, not an error.
7. **Permission mode blocks tools.** If `WISP_PERMISSION_MODE` is set to `read_only`, write tools fail with "Blocked" errors. This is expected for security testing.
