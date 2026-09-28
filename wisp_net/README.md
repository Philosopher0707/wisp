# wisp_net — the network platform wisp operates

`wisp_net` turns wisp into an autonomous network agent, following blueprint `NET-AGENT-ARCH-v2.4`.
The platform is a separate process wisp reaches over MCP (ADR-0069). Wisp brings the agent: the turn
loop, subagents, the gate chain, permission modes, the policy bundle and the audit log.

**Status: N1 (sense and state), N2 (reasoning), N3 (safety), N4 (actuation) and N5 (governance).** The platform can now
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
`read_only` wisp session can run the whole diagnosis loop. Add `--lab-control` to expose fault
injection and the lab clock (`lab_*` tools). They change the simulated world and are never read-only.

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
| Closed loop | — | event-driven autonomy | N6: an alert starts a headless wisp turn, tiered autonomy |

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
