# ADR: Fleet roles and the worker contract

## Status: Proposed (Phase 2 of the fleet consolidation; needs the human's decision on the open questions)

## Context

Eighteen repos are declared in `wisp.fleet.toml`. Observed on 2026-10-04:

- **Six overlapping runtime/harness explorations** with no shared code: `archon` (11.0k LOC Python,
  event-sourced harness), `tbrr` (9.9k, trust-bounded runtime), `srr-runtime` (7.6k), `libraryr` (6.9k
  TypeScript), `agent-runtime` (8.6k), `plateform` (13.0k). Each re-implements an event log, a tool
  boundary and an authorization gate, which is also what `wisp/core`, `wisp/infra` and the audit chain do.
- **Five task-specific agents**: `always-on-worker`, `db-agent`, `gump`, `comp-neuroscientist`,
  `data-scientist`. Their CLIs differ (argparse subcommands `run|status|plan|events` in
  always-on-worker; `run|fix|verify|trajectory` in gump; `query|monitor|backup` in db-agent), and none
  shares an output contract. Only `kiro_research_api` ships an MCP server.
- **Wisp already has the integration machinery**: an MCP client (`wisp/mcp/manager.py`, transports
  `stdio|sse|streamable-http`) governed by the plugin/MCP trust ADR (allowlist, origin pinning,
  first-use consent, per-server scopes), a `SubagentOrchestrator` (`wisp/multi_agent`), and, on the
  unmerged branch `feat/process-subagents`, process isolation so a subagent can actually be killed.
- The orchestrator must keep the human in the loop: workers must not push, merge or widen their own
  permissions.

## Decision

1. **A worker is an MCP stdio server.** A repo with role `agent` or `tool` that wants to be driven by
   wisp exposes `run`, `status` and `events` tools over MCP. Wisp connects through the existing MCP
   manager, so consent, origin pinning, scopes, audit and the approval layer apply with no new spawn
   protocol and no new authority.
2. **The fleet manifest is the registry.** A repo may carry an optional `[repo.worker]` table
   (`command`, `cwd`, `scopes`). `wisp fleet` renders it to the MCP config instead of anyone hand-editing
   `~/.config/wisp/mcp.json`. The manifest stays the single source of truth for what exists.
3. **Workers never hold publish authority.** They return results and evidence; wisp opens the PR and
   the human merges. This keeps the standing boundary in `CLAUDE.md` and is enforced by not giving a
   worker's scope list any push or merge tool.
4. **The six runtime explorations are not folded in as code.** Their value is the invariants they tested
   (verifiable claims, trust labels, content-addressed redelivery), not their implementations. Each is
   mined for invariants that wisp lacks, those become wisp tests or ADRs, and the repo is then set to
   role `archive`. Nothing is deleted.
5. **Duplicates and stubs are archived now**: `cn-d1d3-fix` (a branch copy of `comp-neuroscientist`,
   already `archive`) and `autopipe` (already `archive`).

## Alternatives rejected

- **Spawn each worker as a raw subprocess from wisp.** Bypasses the MCP trust model and creates a second
  authority over what a child may do.
- **Import workers as libraries.** The fleet mixes Python and TypeScript, and an import couples release
  cycles.
- **One monorepo or git submodules.** Moves 7.7 GB and merges unrelated histories for no behavioural
  gain; the manifest gives the same single view without the move.
- **Fold `archon`/`tbrr` into `wisp/core` now.** Two parallel implementations of the same authority is
  the failure the existing authority ADRs exist to prevent; deciding which wins needs the invariant
  review in step 4 first.

## Consequences

- Each worker needs a thin MCP shim (small, and `kiro_research_api` already has one to copy). Until it
  exists, the worker is reachable only by hand.
- `wisp fleet` gains a `workers` action that renders MCP config from the manifest; it stays read-only
  unless the human passes an explicit write flag.
- The invariant review is real work and has not been done: this ADR does not claim any of the six
  explorations is redundant, only that none should be folded in untested.

## Open questions for the human

1. Which worker should be first: `always-on-worker` (already emits JSON and an event log) or `gump`
   (already has a hash-chained trajectory and a `verify` command)?
2. Should `plateform` (13k LOC, "AgentOS Enterprise") be reviewed for invariants first, since it is the
   largest and the most likely to overlap wisp's control plane?
3. Merge `feat/process-subagents` before building the first worker shim? Killability matters for a worker
   that runs for hours.
