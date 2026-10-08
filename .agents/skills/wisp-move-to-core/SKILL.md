---
name: wisp-move-to-core
description: Move shared wisp logic into wisp/core with the layering rule, a differential test and a mutation check. Use when two entry points duplicate a rule.
---

# wisp-move-to-core

`wisp/core` must not import cli, repl, transport, server or tui (`tests/test_layer_direction.py`). Shared policy lives in core;
surfaces (CLI, REPL, subagents, fleet) call it.

1. Find every consumer: `grep -rn --include='*.py'` the symbol and the constants it uses (a missed consumer breaks collection).
2. Write the core module with the smallest API, keep behaviour identical, then migrate **one** caller per PR.
3. Where a rule composes several filters (tool offer = role list, permission-mode filter, tool profile, capability partition),
   add a differential test against the real pipeline (see `core/tool_surface.py`).
4. Core already holds: `workspace_walk` (bounded walk, `$HOME` guard), `approval_policy` (no approver is not an approval),
   `spend.estimate_spend` (charge each provider call), `tool_surface`, `recovery.describe_failure`, `turn_control`,
   `shutdown`. Child extension tools: `tool_surface.inherited_extension_tools` (declared-read MCP tools in any mode, other MCP
   tools in full mode, never `skill__*`).
5. Doctor/CLI code that imports `wisp.cli` stays outside core (`wisp/cli/doctor_harness.py`).
6. Add a lesson to `AGENTS_LEARNING.md` with evidence, regenerate derived pages, run `wisp-test-hermetic`.
Security defaults: an approval gate is never bypassed by a standing grant over a forced-approval or read-only guard; children
get a narrower tool list than the parent and that list is also their authorization.
