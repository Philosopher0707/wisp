# wisp_net — the network platform wisp operates

`wisp_net` turns wisp into an autonomous network agent, following blueprint `NET-AGENT-ARCH-v2.4`.
The platform is a separate process wisp reaches over MCP (ADR-0069). Wisp brings the agent: the turn
loop, subagents, the gate chain, permission modes, the policy bundle and the audit log.

**Status: N1 (sense and state) and N3 (safety). Still read-only.** Nothing here can change a network yet.
N3 is the gate every future change must pass. Actuation (N4) is built behind it.

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
| **3 Reasoning** | wisp's agent over the MCP tools | orchestrator + 4 domain agents | N2: domain subagents (TE, security, diagnostics, compliance) and intent schema |
| **4 Safety** | Declarative change sets of gNMI updates and deletes, with intent, expected unreachability and confidence (`safety/change.py`). Atomic device-side Set (`sim/config.py`). Exact ACL header-space algebra: first-match, dead rules by union coverage (`acl.py`). Formal checks on twins: loop-free forwarding, no new blackholes, no BGP session lost as collateral, no new dead ACL rules, no new zone leaks with the exact leaking flow classes, no new congestion, no new major alerts, convergence (`safety/verify.py`). What-if on two clones of the live lab with their own telemetry pipelines, so the lab is never touched (`safety/whatif.py`). Policy arbiter for verification, blast radius, change windows and the confidence threshold, with rules as JSON (`safety/policy.py`, `policies/*.json`). | Batfish/Z3, Containerlab twin, OPA | Batfish and OPA adapters |
| **5 Actuation** | — (the device-side Set exists; nothing calls it on the live lab) | gNMI Set commit-confirm, SDN, Ansible/Terraform | N4: apply only a change whose what-if passed and whose policy verdict allows, with commit-confirm and health-checked auto-rollback |
| **6 Governance** | wisp's audit log and approvals | ledger, rationale, cockpit | N5: change ledger, operator cockpit, kill switch |
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
