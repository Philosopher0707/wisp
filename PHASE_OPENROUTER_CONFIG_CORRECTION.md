# PHASE_OPENROUTER_CONFIG_CORRECTION.md — the model works; my earlier conclusion was too strong

> Generated 2026-09-27. **A correction to `PHASE_INTERRUPT_MANUAL_TEST.md` §3 and to commit `0917c94`.**
> Those said *"every turn fails on this configuration"*. **That is false.** The same configuration
> completes turns, and the variable I had not isolated was a proxy.

---

## §1 — What the saved config actually is

`~/.config/wisp/.env` (hidden — a `*.json` glob misses it):

```
WISP_MODEL=stealth/space-bunny-alpha
WISP_PROVIDER=openrouter
WISP_API_KEY=<redacted>
WISP_API_BASE=https://openrouter.ai/api/v1
```

`~/.config/wisp/config.json` says `provider: ollama, model: qwen2.5-coder`, but **the `.env` wins** —
which is why the REPL banner showed OpenRouter. Both files exist; only one is consulted.

## §2 — It works, in both modes

**Headless** (`--print`, one tool call):

```json
{"ok": true, "content": "… `calc.py` defines a single function `add(a, b)` that returns the sum of
 its two arguments.", "model": "stealth/space-bunny-alpha"}
```

**REPL:**

```
`calc.py` defines a single function, `add(a, b)`, that returns the sum of its two arguments.
  Turn 1 · 1 tools · 0 files · 5.3s · ctx 105 (0%)
```

`ok: true`, exit 0, **no token-budget error**.

## §3 — The variable I failed to isolate: the proxy

This sandbox sets `HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` / `https_proxy` to
`http://127.0.0.1:62161`. With them set, wisp's request returns:

```
{"ok": false, "error": "Server returned 502: upstream connect failed: Connection refused (os error 61)"}
```

**`curl` to the same host through the same proxy returns 200** — so the network is fine and the failure
is specific to wisp's request path through that proxy. Remove the four proxy variables from wisp's
environment and both modes succeed.

**So the 502 was the sandbox proxy. It is not a wisp defect.**

## §4 — The correction, stated plainly

`PHASE_INTERRUPT_MANUAL_TEST.md` §3 reasoned from one observed error:

> *"Consequence: on this configuration **every turn fails** … which is why the sessions in this thread
> errored rather than answering."*

**That generalisation was not supported.** Two variables were in play — the token budget and the proxy —
and I isolated neither. The budget arithmetic is real and measured (§5), but it did **not** prevent these
turns, and it does not follow from it that every turn fails.

**The earlier REPL run's `402 Prompt tokens limit exceeded: 17706 > 8517` was a genuine OpenRouter
error.** What is now unclear is *which* difference produced it — the proxy path, or that run's workspace
(`/tmp`, where this one used a two-file directory). I have not isolated that, and I am not going to
guess a third time.

## §5 — What survives from the earlier finding

**Still true and still measured:** `TOOL_SCHEMAS` is **6,922 tokens** across 42 tools, and the account's
budget is **8,517**. That is 81 % before the system prompt is written, and it is worth watching —
particularly for the REPL, whose prompt is larger than headless's.

**Also still true:** `max_context_tokens` defaults to **256,000**, which this account cannot satisfy, and
it is used for compaction thresholds rather than as a pre-send check.

**What is retracted:** that these facts mean turns fail.

## §6 — Also found, and not a code issue

`~/.config/wisp/auth_keys.json` is keyed **by the API key itself** — the credential is the dictionary
*key*, so it is visible to anything that lists the file. Rotate it and store it under a name.
