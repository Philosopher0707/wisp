---
name: live-model-paired-runs
description: Run a real model against Wisp (paired core off vs enforce) through wisp's own judge, safely and repeatably - key handling, spend cap, forcing the right checkout, rate-limit handling, resumable runs. Use before flipping a heuristic to enforce or when a result needs real-model evidence.
agent_created: true
---

# Live paired runs

Evidence for a flip comes from real models, but live runs cost money, leak keys and lie about infrastructure. The procedure that worked (OpenRouter, `inclusionai/ling-3.1-flash`, `wisp judge`):

## Preflight (read-only, nothing printed)

- The user names the key and model. Identify the key **by shape**, never by echoing it (`sk-or-` is OpenRouter; check it matches the endpoint before sending it anywhere: do not send one provider's key to another).
- `GET /models`: the model exists, its price, tool support. `GET /key`: usage, limit, remaining. Set **your own stop** (e.g. +$0.25 of usage) even for a free model; abort the loop past it. State the cap in the report.
- System Python may lack CA certificates: use the venv's `httpx`, never disable verification.

## Run

- **Force the checkout under test**: `PYTHONPATH=<worktree>`. Without it the venv imports the user's own checkout (`~/dev/wisp`) and the run silently tests old code. `python -c "import wisp; print(wisp.__file__)"` from a temp dir.
- Child gets a throwaway `HOME` so synthetic tasks do not pollute the user's wisp memory; the key goes in via the environment, read in-process from `.env`, never printed, redacted from any captured output.
- Mode and journal per run: `WISP_REASONING_CORE`, `WISP_REASONING_CORE_RULES`, `WISP_REASONING_JOURNAL=<file per task and mode>`. Run `off` and `enforce` over the same tasks, one after the other.
- Run one task per child through `core.run_one` so you can attach what the judge does not report (here the child's 429 count).

## Rate limits are infrastructure, not results

A free model sat behind a shared upstream pool: HTTP 429 "temporarily rate-limited upstream". The judge scored such a run NO-OP or marked an honest claim dishonest (the child's `ok` was false only because of the 429s). So: probe with a tiny request until 200 before each task (back off), count 429s per run, **label any run with 429s as INFRA and retry** (bounded), and exclude INFRA from rates. Report how many were retried.

## Background and resumability

Use the harness's `run_in_background: true`, not a bare `( ... ) &` (those died with the call's shell). Even so, long runs got cut: append every result to a JSONL file and skip tasks already in it, so a rerun resumes. One writer per log.

## Report

Per task and mode: verdict, claim honest, rules fired and applied, 429 count, seconds; the usage delta; what was retried or INFRA; sample size (13 tasks is not a rate). Never claim a flip is justified from a handful of runs.
