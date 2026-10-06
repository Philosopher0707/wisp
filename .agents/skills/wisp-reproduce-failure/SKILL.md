---
name: wisp-reproduce-failure
description: Reproduce a reported wisp failure (provider error, hang, slow tool, Ctrl-C) hermetically and keep the repro as the regression test.
---

# wisp-reproduce-failure

Reproduce the user's exact failure before fixing; the repro becomes the regression test.

- **Provider failure:** a local `HTTPServer` returning the provider's response (e.g. 402 JSON on POST `/chat/completions`, a model
  list on GET), then drive the real CLI:
  `WISP_PROVIDER=openai WISP_API_BASE=http://127.0.0.1:<port>/v1 WISP_API_KEY=test-key WISP_MODEL=<m> python -m wisp -w <ws> /swarm "task"`.
  Use `tests/test_cli_surface_e2e.py::cli_env` for a hermetic HOME.
- **Stalled provider / Ctrl-C:** stub that sleeps on POST, run the REPL under a pty, send Ctrl-C, expect "Turn interrupted"
  within seconds (`tests/test_turn_cancel_pty.py`). From a signal handler use `core.turn_control.request_cancel`
  (`loop.call_soon_threadsafe`), never a bare `task.cancel()`; test with a real signal, not a fake task.
- **Slow step:** time each stage separately (tool, lint, affected-test lookup) with `time.perf_counter()` before blaming one.
  Anything that runs on every write needs its own budget.
- **Hang on exit:** Python waits for non-daemon threads; the process is bounded by `core.shutdown.arm_exit_watchdog`
  (`WISP_EXIT_GRACE_S`, default 5, 0 off). Capture the culprit: `PYTHONFAULTHANDLER=1 wisp repl`, then
  `kill -ABRT $(pgrep -f "wisp repl")`.
- **Verify a slash command by dispatching it** (`wisp /doctor harness`), not by calling the function you edited: some commands
  have two handlers.
