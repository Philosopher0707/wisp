---
name: fault-injection-personas
description: Measure how often a failure reaches the user, and whether a new control moves it, by scripting a fake model ("persona") against the REAL engine in a real temp workspace. Use for any harness rule (completion claims, repeated failures, refusals, provider errors) before enabling it, and to pin a before/after table.
agent_created: true
---

# Fault-injection personas

Unit tests build the shape the code expects; real failures arrive in another shape. Personas drive the real `AgentRuntime`/`WispAgentCore` with a scripted provider, so every seam is exercised the way a user's turn exercises it. Files: `tests/reasoning/personas.py` (the harness and table generator), `test_personas.py` (pins), `docs/harness/reasoning-core-baseline.md` (generated table).

## Build

1. **Provider**: `generate_stream_events` yields scripted rounds in order and repeats the last forever (a model with nothing new to say). Round helpers: tool call, content, error event. Record the messages it was shown (`seen`) so you can assert what the model read.
2. **One persona per failure that really happened** (claims success without running, repeats a failing command, rephrases a refused command, hits a provider limit) **plus an honest control** that must stay untouched under every mode.
3. **A mechanical "reaches the user" predicate per persona that does not use the code under test** (its own regex, the files on disk, the commands actually run). Otherwise the baseline agrees with the control by construction.
4. **Run each persona in every mode** (off, observe, enforce, per-rule) and tabulate: reaches the user, observe equals off, which rules would fire.

## Pin

- Observe equals off: compare the whole event stream with timestamps and the temp workspace path normalised (a first run "failed" only because of that path).
- Baseline: with the control off, today's failures reach the user (the number to move). Each enabled rule moves exactly its rows and nothing else.
- The generated table is embedded in the doc and a test regenerates and compares it.
- Add the case where the model *would* comply if nudged (withhold, then verify) and the case where it ignores the nudge (flag, end): both paths of a rule.

## What this found that unit tests did not

- Refused calls bypass the tool-result loop (a seam after `_execute_tool` never saw them).
- A real 402 arrives as a provider `error` event, not an exception.
- A failing shell command is an "ok" tool result with `[exit code: N]` in the text.
- The existing announced-step gate is a bounded delay, not a guarantee.

## Limits to state

Scripted failures show that rules fire on known shapes, not how often real models fail that way: say so, and pair with live runs (`live-model-paired-runs`). Do not make the persona set larger than the failures you have evidence for.
