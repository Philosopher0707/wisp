# wisp_net — the network platform wisp operates

`wisp_net` turns wisp into an autonomous network agent, following blueprint `NET-AGENT-ARCH-v2.4`.
The platform is a separate process wisp reaches over MCP (ADR-0069). Wisp brings the agent: the turn
loop, subagents, the gate chain, permission modes, the policy bundle and the audit log.

**Status: all six blueprint layers (N1–N5) plus the closed loop (N6).** The platform can now
change the lab. It only does so through `net_apply_change`, only for a change `net_what_if` verified,
only when the change policy allows it (or an operator approved it in the cockpit), and always with a
confirm window that rolls back automatically.

## Run it

```bash
python -m wisp_net scenarios                                   # list the bundled fault scenarios
python -m wisp_net demo --scenario optic-degradation --seconds 600
python -m wisp_net mcp                                         # MCP server on stdio (what wisp spawns)
```

Connect it to wisp in `~/.config/wisp/mcp.json`. A workspace `.wisp/mcp.json` also works, but only in an
explicitly trusted workspace.

```json
{"mcpServers": [{
  "name": "net",
  "command": "python3", "args": ["-m", "wisp_net", "mcp", "--scenario", "optic-degradation"],
  "always_load": true,
  "tool_risk": {"net_status": "read", "net_devices": "read", "net_topology": "read", "net_interfaces": "read",
                "net_bgp": "read", "net_routes": "read", "net_trace": "read", "net_paths": "read",
                "net_reachability": "read", "net_alerts": "read", "net_events": "read", "net_metrics": "read",
                "net_series": "read", "net_top_flows": "read", "net_knowledge": "read", "net_gnmi_get": "read",
                "net_changes": "read"}
}]}
```

The N3 tools `net_what_if`, `net_acl_audit`, `net_segmentation_audit` and `net_change_policy` are also
reads: `net_what_if` runs on clones and never touches the lab. Add them to `tool_risk` as `read` too.

`net_approvals`, `net_ledger`, `net_explain` and the N2 tools (`net_optics_forecast`, `net_error_correlation`, `net_config_drift`, `net_advisories`, `net_te_assess`, `net_flow_anomalies`, `net_compile_intent`) are reads too. **Never declare `net_apply_change` read.**
Left undeclared, it stays `exec`, so wisp asks you before every apply, on top of the platform's own policy.

### Closed loop (shared platform)

```bash
python -m wisp_net serve --port 8750 --autonomy diagnose        # the platform; watcher on
# ~/.config/wisp/mcp.json then points wisp at it instead of a private lab:
#   {"name": "net", "command": "python3", "args": ["-m", "wisp_net", "mcp", "--connect", "http://127.0.0.1:8750"], ...}
```

Every wisp session, and every incident-driven turn, then sees the same network. The watcher's agent
command defaults to `wisp run {prompt} --skill net-orchestrator`. Change it with `--agent-cmd`.

### Operating changes

```bash
python -m wisp_net mcp --cockpit 8750 --ledger ~/.config/wisp-net/ledger.jsonl   # what wisp spawns
python -m wisp_net cockpit approvals                                             # you, in another terminal
python -m wisp_net cockpit grant R0001 --operator alice --reason "diff reviewed"
python -m wisp_net cockpit kill --operator alice --reason "incident"             # passive monitoring
python -m wisp_net cockpit rollback X0003 --operator alice                        # revert a confirmed change
```

The agent has no tool that grants an approval or touches the kill switch. On a single host, a process
running as your user can still read the cockpit token. Real separation needs the cockpit on another
host behind SSO.

`tool_risk` is the operator's statement that these tools only read. Wisp does not trust a server's own
`readOnlyHint`. Every tool left out stays `exec` and asks for approval. With the declaration, a
`read_only` wisp session can run the whole diagnosis loop (this holds at all five places that enforce read-only by
name: the policy rule, the mode hard-deny, the executor's MCP block, the subagent filter and the schema/menu filter;
it was **not** true before the change that added `is_declared_read`, and the watcher's diagnose and propose tiers
could not read the network). Add `--lab-control` to expose fault
injection and the lab clock (`lab_*` tools). They change the simulated world and are never read-only.

## Evaluating a model

Every other test here is deterministic. This is the one place a real model drives the real path (`wisp --print`, asking for the `net-orchestrator` skill, over the MCP server, in `read_only` via `WISP_PERMISSION_MODE`, against a lab whose fault has already developed) and is scored against ground truth.

```bash
python -m wisp_net eval --provider ollama --model llama3.2:3b                  # local, no key
python -m wisp_net eval --provider openai --model <name> --pass-env OPENAI_API_KEY --json scores.json
python -m wisp_net eval --pass-env WISP_API_KEY --pass-env WISP_API_BASE                  # provider, model and key from ~/.config/wisp/.env
python -m wisp_net eval --model <name> --scenario optic-degradation --keep runs/   # one scenario, keep raw output
python -m wisp_net eval --model <name> --samples 5 --jobs 3 --json scores.json          # 5 runs per scenario, 3 at a time; prints a pass rate per scenario
```

A run **passes** only if it finished without errors, made at least one `mcp__net__*` call (a right answer without evidence is a guess), named every ground-truth fact (device, interface or peer, cause), and never called an actuating tool. It also records placeholder arguments (`device1`, `port1`) and device names that do not exist in the lab. The environment is hermetic: a temporary HOME and workspace, no credentials unless you pass a variable with `--pass-env`, and an MCP server that declares only the read tools as read and has no `--lab-control`. There is no built-in provider: `--provider` and `--model` come from the flag, else `WISP_PROVIDER` / `WISP_MODEL` (the environment first, then `~/.config/wisp/.env`, which `eval` loads into its own process), else the command refuses. A `--pass-env` name that is set nowhere is refused before anything runs (it used to be dropped, and the run went out with no key); values are never printed. The real user base (`PYTHONUSERBASE`) is pinned so `pip install --user` packages stay importable under the temporary HOME. With `--samples N` kept files are named `<scenario>.s<N>.*`. `--warmup` (on `mcp`) starts the lab with the fault already developed so every run sees the same world.

### Baseline, 2026-09-29

| Model | Result | What happened |
|---|---|---|
| `llama3.2:3b` (local) | **0/6** | (Run before `--print` honoured `WISP_PERMISSION_MODE`, so it ran with full permissions; with zero tool calls nothing could have been actuated.) Zero structured tool calls in every run. It describes the tools it would call, in prose and code fences, with placeholder arguments, and never calls them (`iterations: 0`, no errors). |
| `stealth/space-bunny-alpha` (OpenRouter) | **6/6** | 14–65 read calls per scenario (40–186 s). Every answer named the injected fault and cited the tool results behind it. None tried to change anything, and each declined to apply a fix itself (the change window, and confidence below the 0.85 policy threshold). |

That second row is **one run per scenario of one model**, with unpinned sampling: it shows the loop works end to end with a capable model, not how often. The model is an unlisted OpenRouter "stealth" model, so others cannot reproduce it exactly; run the command above with a model you can name and add its row.

**Guidance reaching the model (measured 2026-10-02):** the system prompt shows each skill's description and only the first 200 characters of its body (`wisp/core/stateless.py`, `_build_skills_block`); the full text is behind `skill__<name>`. The five net skills therefore opt in with `inline-instructions: true` (a real YAML boolean, fail-closed like `disable-model-invocation`), which puts their whole body (about 2K tokens together) in the prompt. Other skills keep the cut: removing it everywhere would add tens of thousands of tokens for anyone with many skills.

**A gap this exposed:** `read_only` refuses the `skill__*` loader and every hand-off to other agents (`orchestrate_*`, `spawn`, `fanout`). The orchestrator skill tells a model to do both, so a read-only session (the watcher's diagnose and propose tiers) cannot load the skill's instructions or delegate to the domain agents. The capable model above improvised the same loop with the `mcp__net__*` tools, and the scorer records the refusals without failing the run, but the shipped skills are not actually reaching read-only sessions. Whether `skill__*` should be readable in `read_only` is a permission decision left to the operator.

The control that separates the model from the setup: the same model, asked to use the built-in `list_files`, behaves the same way, so the MCP path is not the cause. And `tests/net/test_net_eval_pipeline.py` is the positive control: a scripted model that does emit a structured tool call passes through the real `wisp` CLI, the real MCP server (80 tools offered, 32 of them `mcp__net__*`) and the real lab, and its answer, built from the tool result alone, names `leaf2 Ethernet50` and `rx_power_low`.

**Not measured:** any capable model. At the time of writing the Ollama cloud models on the operator's account were unavailable (retired, not in the free usage, or the monthly limit reached), and nothing was run that would cost money. The 0/6 says a 3B model cannot drive this loop; it says nothing yet about the orchestrator with a model that can. Run the command above with one and add its row here.

## Blueprint map

| Blueprint layer | Built (N1) | Stand-in for | Next |
|---|---|---|---|
| **1 Perception** | gNMI Get/Subscribe facade over OpenConfig paths (`sim/gnmi.py`). RFC 5424 parser, normalizer and dedup (`telemetry/syslog.py`). IPFIX-style flow records aggregated per export. Collector with counter-reset-safe rates (`telemetry/collector.py`). | gNMI/gNOI collectors, IPFIX/sFlow, syslog/SNMP pipelines | pygnmi against Containerlab or hardware |
| Message bus | Topics `net.telemetry.{metrics,flows,topology,alerts,events}.v1`, offsets, count and age retention, lost-record accounting (`telemetry/bus.py`) | Kafka / Redpanda | aiokafka adapter |
| **2 State** | Digital twin built from telemetry alone: LLDP links remembered while down, BGP, FIB, ACLs, versioned snapshots and diffs, ECMP forwarding traces with blackhole/loop/stale-state outcomes, reachability matrix (`state/twin.py`) | Neo4j / Neptune | graph DB adapter |
| Time series | SQLite raw + 1-minute + 1-hour tiers, retention 7 d / 90 d / 365 d (`state/tsdb.py`) | ClickHouse / VictoriaMetrics | adapter |
| Knowledge | BM25 over bundled runbooks (`knowledge/*.md`, `state/knowledge.py`) | Milvus / Qdrant RAG | embedding store |
| Alerts | Stable-key alerts that open, escalate, resolve and reopen with occurrence counts: interface down, BGP down or flapping, FCS errors, rx power, egress congestion, CPU, device unreachable (`telemetry/alerts.py`) | — | predictive trends (N2) |
| **3 Reasoning** | Five wisp skills (`agents/`, installed with `python -m wisp_net install-skills`): the **orchestrator** (sense, triage, delegate, resolve conflicts by safety > availability > performance > efficiency, then compile, verify, apply or escalate) and four domain agents. Each has deterministic instruments: **predictive diagnostics** (`net_optics_forecast` least-squares slope, R² and time to errors/LOS; `net_error_correlation` Spearman plus the below-onset signature), **compliance** (`net_config_drift` against `policies/golden.json`, `net_advisories` from a lab feed), **traffic engineering** (`net_te_assess` with simulated drains), and **security** (`net_flow_anomalies` z-score against a source's own history). `net_compile_intent` turns a declared intent (drain/restore link, quarantine/release host, guard zone, BGP neighbor) into exact gNMI ops, refusing ones that do not fit the network (`reasoning/`) | orchestrator + 4 domain agents (PPO, CSP) | learned TE optimizer behind `te_assess` |
| **4 Safety** | Declarative change sets of gNMI updates and deletes, with intent, expected unreachability and confidence (`safety/change.py`). Atomic device-side Set (`sim/config.py`). Exact ACL header-space algebra: first-match, dead rules by union coverage (`acl.py`). Formal checks on twins: loop-free forwarding, no new blackholes, no BGP session lost as collateral, no new dead ACL rules, no new zone leaks with the exact leaking flow classes, no new congestion, no new major alerts, convergence (`safety/verify.py`). What-if on two clones of the live lab with their own telemetry pipelines, so the lab is never touched (`safety/whatif.py`). Policy arbiter for verification, blast radius, change windows and the confidence threshold, with rules as JSON (`safety/policy.py`, `policies/*.json`). | Batfish/Z3, Containerlab twin, OPA | Batfish and OPA adapters |
| **5 Actuation** | `net_apply_change` checks, in order: kill switch, a fresh verification of *this* fingerprint with nothing committed since, the policy re-decided now, and a single-use operator grant when one is required. Applying twice is a no-op. Then checkpoint, commit, a confirm window of at most 60 s watched through telemetry (BGP flap rate > 5/min, loss > 0.05% on touched ports, management unreachable, undeclared reachability loss), and automatic rollback on any trigger (`actuation/engine.py`) | gNMI Set commit-confirm, SDN, Ansible/Terraform | real gNMI Set with device-side commit-confirm |
| **6 Governance** | Append-only, SHA-256 hash-chained ledger of every verification, approval, commit, confirmation, rollback and refusal (`governance/ledger.py`). `net_explain` rationale reports. Approval queue and kill switch (`governance/control.py`). Operator cockpit on 127.0.0.1, bearer token in a 0600 file: approvals, kill switch, operator revert, ledger, what-if sandbox, WebSocket event feed (`governance/cockpit.py`, `python -m wisp_net cockpit ...`) | immutable ledger, rationale logger, cockpit (GraphQL + WS) | SSO in front of the cockpit on another host; GraphQL |
| Closed loop | `python -m wisp_net serve`: one long-running platform (lab, cockpit, watcher). The watcher debounces major alerts into incidents, adopts alerts already open at start, and applies a cooldown. Each incident dispatches a headless wisp turn with the orchestrator skill by autonomy tier: `observe` (record only), `diagnose` and `propose` (wisp runs read-only), or `act` (may apply; platform verification, policy and approvals still gate). The agent reaches the same network through `mcp --connect`, the agent API with its own token, which cannot reach operator routes or lab control (`loop/watcher.py`) | event-driven autonomy (TM Forum L4/L5) | live-model evaluation |

## The simulated lab

A 2-core, 2-spine, 4-leaf fabric: eBGP leaf↔spine↔core, iBGP between cores, 100G/400G links, four 25G
servers per leaf, and a transit edge on each core. The lab clock starts on a Monday at noon. Routing is
path-vector with AS-path loop prevention, iBGP split horizon and ECMP. Traffic is a diurnal demand
matrix forwarded over the FIB. Hosts are capped at line rate, so overload shows as egress discards.
Receive power drives the FCS error rate. The run is reproducible from the seed.

Fault kinds: `link_down`, `link_flap`, `optic_degrade`, `crc_surge`, `bgp_down`, `cpu_spike`,
`congestion`, `mgmt_unreachable`.

Deliberate simplifications:
- AFT entries carry next hops inline, with no next-hop-group indirection.
- Transceiver components are named after their port.
- A tick's drops at one hop don't reduce the load counted downstream.
