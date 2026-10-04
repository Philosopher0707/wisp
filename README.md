# Wisp

**A production-grade, local-first CLI coding agent with enterprise governance. Runs frontier cloud models and local inference behind one runtime.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#license)
[![SWE-bench](https://img.shields.io/badge/SWE--bench-Ready-orange)](wisp/benchmark/)

```bash
pip install -e .
wisp setup                    # pick provider, validate live, save sealed
wisp "fix the off-by-one in totals.py and prove it with tests"
```

Wisp reads your codebase, edits files, runs tests, and remembers context across sessions. One Python CLI drives interactive REPL, single-shot, headless CI, and server modes. It serves frontier cloud models (Claude 3.7 Sonnet, DeepSeek R1, GPT-4o via OpenRouter/OpenAI/NVIDIA) and local inference (Ollama, vLLM-compatible endpoints) through the same provider interface, with `wisp setup` handling selection, live validation, and sealed credential storage.

Where Claude Code, Aider, and SWE-agent stop at the single-turn loop, Wisp adds the runtime around it: multi-agent orchestration with bounded concurrency, AST-grounded repository context, automatic per-turn mutation checkpoints with rewind, and Ed25519-signed governance policies with tamper-evident audit logs. (The policy bundle is validated and inspectable; it is **not yet applied at runtime** — see [Enterprise-Ready Governance](#enterprise-ready-governance).)

---

## Key Architectural Pillars

### Resilient Mutation Engine

Every `write_file` / `edit_file` / `edit_file_multi` snapshots pre-mutation content automatically into a bounded per-workspace store. Failed mutations leave no snapshot behind.

- **Surgical search/replace** — exact match with Unicode-aware fuzzy fallback; multi-edit applies atomically against the original.
- **`rewind` tool + `/rewind [seq|path]`** — the agent undoes its own bad edits; restoring a never-existed file deletes it; rewind snapshots first, so rewind itself is rewindable.
- **Verification loop** — turns that edit code must see an exit-0 verification command before completing.

### AST Grounding & Smart Context

- **Tree-sitter RepoMap** (`wisp/repo_map.py`, `wisp/core/context/repomap.py`) — PageRank over the symbol graph grounds prompts in what the code actually references, not nearest-neighbor text.
- **Semantic + symbol search** — `search_codebase` (embeddings) and `search_symbols` (index) for definition lookup without reading every file.
- **3-tier session compactor** — auto-compaction past the token threshold preserves recent turns verbatim and summarizes the rest into a structured memory block; `remember`/`recall` carry facts across sessions.

### Subagents & Concurrency

- **Bounded orchestration as tools** — `spawn`, `fanout`, `orchestrate_vote`, `orchestrate_map_reduce`, `orchestrate_chain`, `orchestrate_dag` (cycles rejected before tokens are spent). Roles carry constrained `allowed_tools` so workers see scoped toolsets, not the full 42-tool schema.
- **Background workers** — `spawn_background` returns an agent id immediately; `subagent_list` / `subagent_result` / `subagent_send` / `subagent_cancel` inspect, collect, continue, or stop. Settlement notifications reach the parent on its next turn — no polling.
- **`/subagents` monitor** — suspends the spinner, parks input, and opens a full-screen Textual monitor (roster + live per-worker transcript over secret-masked telemetry rings), then restores the terminal untouched. `]`/`[` cycle, `c` cancels, `q` exits.

### Graph Execution Engine

Deterministic, durable multi-agent execution in `wisp/graph/` — the graph owns authority (what runs, waits, retries, stops); models provide judgment inside nodes:

- **Contracts** — every node declares input/output schemas, allowed tools, retry/timeout/budget; failures are typed values, not exceptions.
- **Real dependencies** — edges require a stated reason (`B consumes A.output`); independent branches fan out with isolated contexts and merge by node id, never positionally.
- **Control** — chain/fan/router/controlled-cycle topologies, six join policies, evidence-based verifier nodes + pure gate functions, human approval gates.
- **Durable** — per-transition SQLite checkpoints in the workspace store; crash-safe resume reuses completed nodes and never repeats successes; graph hash pinned per run.
- **Governed** — a graph's own declared policy narrows what its nodes may use (`allowed_nodes` / `allowed_tools` / `allowed_models`; `"all"` is refused under a restrictive policy). Every tool call still passes `ToolExecutor.authorize()`. *(This is independent of the policy-bundle layer, which is not yet wired.)*

### Enterprise-Ready Governance

> ⚠️ **The policy-bundle layer is not yet wired to the runtime.** Bundles are
> signed, verified, merged, and inspectable — but no entry point loads one, so
> `wisp policy dry-run` reports what *the bundle* says, not what the agent is
> permitted to do. Tracked as finding **E** in
> `PHASE_10_M4_GOVERNANCE_UNWIRED.md`. Everything else in this section is live.

- **Signed policy bundles** — Ed25519, narrow-only precedence, revocation + expiry; `wisp policy inspect/verify/explain/dry-run`. **Not applied to tool calls today** (see the note above).
- **Layered authority** — every effect passes `ToolExecutor` + `authorize()` (capabilities → workspace → risk → args → sensitivity → approval). Denials name the controlling layer.
- **Evidence** — hash-chained audit log (`wisp audit verify`), redacted span store, dry-run-only replay, tier-gated OTLP.
- **Durable runs & tasks** — SQLite run store with crash recovery and idempotent resume; `wisp task ...` lifecycle with plan-review-apply.

---

## Quick Start

> **Platform:** POSIX only (macOS/Linux). Windows is unsupported: sandboxing,
> PTY handling, process-group cleanup, and hook execution assume a POSIX
> environment, and several paths fail closed (or leak children) elsewhere.

```bash
# Install
git clone https://github.com/your-username/wisp.git && cd wisp
pip install -e .

# Configure (interactive: provider → model → credentials → live handshake)
wisp setup

# Interactive REPL
wisp repl

# Single-shot
wisp "add retry with backoff to the API client"

# Continue a session
wisp -S <session-id> "now cover it with tests"

# Headless (scripts, CI)
wisp --print "summarize uncommitted changes" --output-format json
```

`wisp setup` offers Ollama (local default), OpenAI, NVIDIA, and OpenRouter — frontier models such as Claude 3.7 Sonnet, DeepSeek R1, and GPT-4o are reachable through OpenRouter or direct endpoints. Typed keys are stored via the OS-appropriate vault path with `0o600` files; keys already in the environment are used, never re-written to disk. No usable provider at boot on an interactive terminal offers the wizard instead of crashing the REPL.

Common operations:

| Command | Description |
|---------|-------------|
| `wisp repl` | Interactive REPL (continuous chat) |
| `wisp "prompt"` / `wisp run "prompt"` | Single-shot turn and exit |
| `wisp -S <id> "prompt"` | Continue a saved session |
| `wisp --print "prompt"` | Headless JSON result on stdout |
| `wisp session list/show/trim/compact` | Session lifecycle |
| `wisp swarm 'goal'` | Multi-agent swarm for a goal |
| `wisp graph run coding-agent '{"goal":"..."}'` | Deterministic graph run (list/show/validate/resume/status/cancel/trace/inspect/metrics) |
| `wisp bench -m m1,m2` | Benchmark matrix (see below) |
| `wisp server` | API + WebSocket server (auth required, see below) |
| `wisp task/policy/trace/replay/audit/release` | Durable tasks, governance, evidence, supply chain |
| `wisp check` / `wisp models` | Provider health / model listing |

REPL essentials: `/subagents` worker monitor · `/graph` runs/status/traces · `/rewind [seq|path]` undo · `/hooks` list hooks · `/provider` + `/model` switch backends · `/compact` · `/help`.

---

## Tool Surface & Profiles

42 tools, one registry (`wisp/tools/registry.py`), uniform JSON envelope on every call:

| Category | Tools |
|----------|-------|
| Files & mutations | `read_file`, `write_file`, `edit_file`, `edit_file_multi`, `rewind`, `list_files` |
| Shell | `run_bash` (sandbox-routed, heuristic deny-list) |
| Delegation | `spawn`, `fanout`, `spawn_background`, `subagent_list/result/send/wait/cancel` |
| Orchestration | `orchestrate_vote/map_reduce/chain/dag` |
| Version control | `git_status/diff/branch/commit/push`, `gh_pr_create` |
| Code intelligence | `search_symbols`, `search_codebase`, `lsp_diagnostics/definition/references/hover/symbols` |
| Verification | `run_tests`, `diagnose`, `plan_task`, `mark_step_done`, `update_plan` |
| Memory & web | `remember`, `recall`, `web_fetch`, `web_search` |
| Meta | `capture_skill` (self-writing skills) |

Small models stay usable two ways: subagent **roles** receive constrained `allowed_tools` subsets instead of the full 42-tool schema, and **task profiles** set the posture without code changes:

| Profile | Posture |
|---------|---------|
| `personal` | Interactive default: writes auto-approved, exec asks |
| `enterprise-managed` | Every mutation asks; org bundle governs |
| `offline-secure` | Local models only; managed approvals |
| `read-only-review` | Mutations denied, reads free |
| `ci-headless` | Safest: exec denied, network off |

---

## Security & Confinement Architecture

Threat model (`docs/THREAT-MODEL.md`): the developer is trusted; **model output, workspace content, and extensions are not**. Host execution is unconfined by default — the controls below are the real boundaries, in order:

| Layer | Control |
|-------|---------|
| **Sandbox** | `run_bash` routes through `get_sandbox()`: Docker (network-none by default — `WISP_SANDBOX_NETWORK=bridge` opens egress — memory/CPU-capped, workspace-mounted) when the daemon is reachable, else host execution with a loud per-call `UNCONFINED` warning. `WISP_SANDBOX=off` forces host mode explicitly. |
| **Authority** | `ToolExecutor` is the only action path; layered `authorize()` denies closed-gate with the controlling layer named. Hook-controlled dirs (`.wisp/hooks/`) are un-writable by agent tools. |
| **Credentials** | `config.json` and `.env` written `0o600`; subprocess envs are credential-stripped; 6 secret families redacted at record construction. |
| **Server** | `wisp server` refuses to boot unauthenticated (exit 2 + key-minting instructions). `--no-auth` is loopback-only and loudly warned; non-loopback binds always require `WISP_API_KEY`. |
| **Transport/API** | WebSocket message caps; SQLite-backed rate limits on mutating routes; security headers + opt-in HSTS; error sanitization on production routes. |
| **Governance** | Ed25519 bundles, revocation + expiry-trim, extension consent + origin pinning, quarantine markers that deny writes even in full mode. |

What Wisp does **not** claim: the dangerous-command check (`sudo`, recursive `rm`, disk writes, pipe-to-shell) is a best-effort heuristic deny-list, not a security boundary — it cannot catch obfuscation, and it is documented as such. Confinement means Docker; everything else is defense in depth. Production hardening knobs:

```bash
export WISP_API_KEY="$(openssl rand -hex 32)"   # required for wisp server
export WISP_PRODUCTION_MODE="true"               # blocks metadata IPs
export WISP_ALLOWED_WORKSPACE_ROOTS="/var/wisp-workspaces"
export WISP_ALLOWED_OLLAMA_HOSTS="localhost,127.0.0.1,my-llm.internal"
```

---

## Benchmark & Evaluation

Deterministic tasks with machine-checked verifiers (no model-judged scoring), plus native SWE-bench ingestion:

```bash
# Built-in matrix across models
wisp bench -m model1,model2 -t json-edit

# SWE-bench instances with predictions output
wisp bench -m mymodel --instances instances.jsonl --predictions preds.jsonl
```

`--predictions` writes one JSON per line — `{instance_id, model_patch, model_name}` — capturing the turn's workspace git diff win or lose, ready for the official harness. Every patch is `git apply --check` clean (enforced by the test suite).

---

## SDK & Extensibility

Embed the runtime headlessly (sync wrapper over `CompositionRoot` + `HeadlessTransport`):

```python
from wisp import Wisp

with Wisp(model="llama3.2", workspace=".") as agent:
    for event in agent.run("refactor auth.py"):
        print(f"[{event.type}] {event.text}")
```

Run a declarative graph (YAML in `graphs/`, or build with `wisp.graph.Graph`) and get a typed handle:

```python
from wisp.graph.dsl import graph_from_yaml

with open("graphs/repo-audit.yaml") as fh:
    graph = graph_from_yaml(fh.read())

with Wisp(workspace=".") as agent:
    run = agent.graph(graph, {"goal": "audit the repository"})
    print(run.trace())   # ASCII execution trace
    result = run.wait()  # status / results_by_node / tokens / cost
```

Extension points: add a tool via `TOOL_SCHEMAS` + `TOOL_IMPLS` in `wisp/tools/registry.py`; add a transport by extending the `Transport` ABC (`send`/`recv`/`approve`/`start`/`stop`); gate tool calls with user hooks (`.wisp/hooks/*.json`, full guide in `docs/hooks.md`); add a slash command in `wisp/cli/dispatcher.py`. See `examples/sdk_basic.py`, `examples/custom_transport.py`, `examples/webhook_server.py`.

---

## Ecosystem / Community Interfaces

- **Remote control clients** (community-maintained, experimental): build and usage guide at [`docs/archive/ANDROID_USAGE_GUIDE.md`](docs/archive/ANDROID_USAGE_GUIDE.md); cloud deploy notes at [`docs/archive/CLOUD_DEPLOYMENT_GUIDE.md`](docs/archive/CLOUD_DEPLOYMENT_GUIDE.md).
- **Operators**: [`docs/QUICKSTART.md`](docs/QUICKSTART.md) · [`docs/ADMIN-GUIDE.md`](docs/ADMIN-GUIDE.md) · [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) · [`docs/RELEASE.md`](docs/RELEASE.md)

---

## License

MIT — free for any use, including commercial.

*Built with Python, FastAPI, Tree-sitter, and Ollama.*
