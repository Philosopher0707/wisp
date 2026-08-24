# Wisp Roadmap

Where the project stands and where it is going. Owned and updated as decisions
change — not a wish list; every item names its trigger and its definition of done.

## Baseline (this cleanup)

- Full suite green: **2,817 passed / 0 failed**, plus mypy-strict clean on the
  gated contract seams (`wisp/core/events.py`, `wisp/core/stateless.py`,
  `wisp/providers/protocol.py`, `wisp/transport/base.py`) and ruff clean on `wisp/`.
- Provider protocol fixed at the ABC level instead of patched per provider
  (see decision D1 below).
- Session handling unified on one contract (decision D2).
- Repo root reduced to the five documents that matter: `README.md`,
  `ARCHITECTURE.md`, `CONTRIBUTING.md`, `AGENTS.md`, this file. All historical
  analysis lives in `docs/archive/`.

## Principles

1. **Local-first, no-account-required.** Ollama stays the zero-friction default;
   hosted providers are opt-in adapters, never requirements.
2. **The event spine is the API.** Everything a transport can show must be an
   `AgentEvent`. UI concerns never leak below `wisp/transport/`.
3. **Stateless core, stateful edges.** `WispAgentCore.turn()` stays pure;
   sessions live in `AgentRuntime`; tools stay pure functions.
4. **Contracts are enforced, not assumed.** A contract seam without a strict
   type gate and a failing-test demonstration of its misuse doesn't count as done.

## Decisions made (D-series — cite these in reviews)

**D1 — Async is a default, not a burden.**
`Provider.generate_stream_events_async()` ships a concrete thread-bridge
implementation on the ABC. Providers with native async I/O override it; nobody
copy-pastes the bridge again. Precedent: `generate_structured()` being optional.

**D2 — A session IS a dict.**
`runtime.get_or_create_session() -> dict`, `AgentAdapter` carries it directly,
slash commands use `.get()`. `SessionDTO` is a construction/validation helper
whose `.to_dict()` output is the canonical wire shape. Any code that wants
attribute access gets a DTO *view*, never a second source of truth.

**D3 — Subagent provider health checks are advisory.**
An unhealthy probe logs and proceeds; the run's own timeout is the enforcement
point. Rationale: "model not found" and "server down" are indistinguishable at
probe time, and mocked cores in tests must not require a live provider.

## Theme 1 — Contract integrity (nearest term)

The `/new` command bug (a DTO assigned into a dict-typed slot, silently
detaching `agent.messages`) was caught by luck, not by types. Hardening:

- [x] Give `WispConfig` real annotations and pull `wisp/config.py`,
      `wisp/core/runtime.py` into the mypy-strict gate — both are named in
      `[tool.mypy]` in pyproject.toml and passing.
- [x] Introduce a typed `SessionView` (read-only accessor over the session
      dict) and route command code through it, so `session["id"]` vs
      `session.id` stops being a judgment call. Shipped as
      `wisp/core/session_view.py`, inside the mypy-strict gate.
- [ ] Retire `AgentAdapter`'s legacy-shim role gradually: move each slash
      command's dependency onto explicit parameters (runtime, session, config)
      so the adapter becomes a thin REPL loop concern instead of a god object.
- [x] Codify the REPL operating contracts — every layer (input, routing,
      commands, turns, rendering, persistence, lifecycle) now has written
      invariants with regression coverage: see `docs/repl_contracts.md`.

## Theme 2 — Provider maturity

OpenAI/NVIDIA support just landed; make it production-grade:

- [ ] Native async for OpenAI-compatible providers (`httpx.AsyncClient`),
      replacing the thread bridge where it matters (concurrent REPL, server
      transport under load).
- [ ] Streaming resilience: mid-stream reconnect with tool-call-delta
      resumption, and per-provider timeout budgets surfaced in config.
- [ ] Cost accounting per provider turn, feeding the existing
      `cost_estimation` module — the CLI already renders turn stats; make them
      truthful across providers.
- [ ] Provider conformance suite: one parametrized test module every provider
      must pass (event shapes, error events, cancellation), so adding a
      provider means passing a gauntlet rather than hoping.

## Theme 3 — Reliability under failure

The circuit breaker exists; wire it into the paths users actually feel:

- [ ] Circuit breaker around provider calls in `stateless.py`'s turn loop,
      with events emitted (`AgentEvent` subtype) so transports can render
      "provider cooling down" honestly.
- [ ] Retry policy in one place (jittered exponential, idempotent-tool
      awareness), deleted from ad-hoc call sites.
- [ ] End-to-end timeout budget: turn_timeout must decompose into
      per-call/per-tool budgets, asserted in a test that fails when any layer
      can exceed its slice.

## Theme 4 — Developer experience

- [ ] CI gates `ruff check wisp/ tests/` and collects the whole suite
      explicitly; no more "some files may not collect".
- [x] Ruff F841 sweep: clean on `wisp/` (0 findings). The remainder lives in
      `tests/` (23 as of this pass, mostly dead assignments from old
      debugging); sweeping those is follow-up work.
- [ ] One-command onboarding: `make dev` (install, doctor-check ollama, run
      fast tests). The CONTRIBUTING.md refresh started this.

## Theme 5 — Multi-agent as a product, not a demo

- [x] Role-aware isolation standard (grill session): RoleConfig.wants_isolation;
      researcher/planner never isolate; contract default False matches schema.
      Same session: timeout retry x1.5 bounded by turn clock, explicit
      delegation fast-path + auto threshold 0.45, reader-proxy fallback for
      bot-blocked fetches, report = final-round-with-fallback.
- [ ] Known external limit: NVIDIA integrate endpoint stalls fresh
      early-session streams with zero bytes (keep-alives defeat idle
      timeouts) — analyzer-delegated children burned full budgets on
      silent sockets while later requests flew (47.8s). Instrumented
      evidence in _runner INFO logs (first-event latency). Mitigation
      options: prewarm ping before delegation, or first-token deadline
      inside the runner. Status: the first-token deadline is implemented on
      the direct runner path (`_runner.py`), not yet on `_run_via_runtime`.
- [x] Budget enforcement at orchestrator admission — ceiling from
      `subagent_token_budget` (default 2M/session), headroom floor
      scales with ceiling; conflicts in patch-apply now revert cleanly
      (no markers/.rej); first-token deadline aborts silent stalls.
- [ ] Worktree isolation tested against concurrent same-repo agents.
- [ ] Delegation analyzer outputs consumed by the planner so subagent
      contracts are derived, not hand-written.

## Theme 6 — Evidence: the local-model scoreboard (current)

Wisp claims "local-first"; the benchmark harness turns that claim into
data. `wisp bench -m llama3.1:8b,qwen2.5-coder` runs deterministic
tasks in isolated workspaces, verifies outcomes by executing code
(never an LLM judge), and scores models from the event stream.

- [x] v1 harness: task suite + headless runner + event-stream scoring +
      scoreboard report + `wisp bench` CLI (`wisp/benchmark/`).
- [ ] Grow the suite: medium-difficulty tasks (multi-file edit, run
      tests to green), plus a token-usage column once providers surface
      usage in events.
- [ ] Publish periodic scoreboards for popular local models; use results
      to tune per-model defaults (timeouts, iteration budgets, roles).
- [ ] SUBAGENT/PROVIDER_STATUS rendering parity in server + TUI
      transports so bench-style observability holds everywhere.

## Non-goals

- No telemetry home, no hosted control plane. Plugins/extensions run
  locally, and the code ships a marketplace *client* (`MarketplaceRegistry`
  + the `/api/plugins/marketplace` route) aimed at a remote marketplace URL —
  but the project operates no hosted marketplace service. Wisp runs on your
  machine and stays boring to trust.
