# Provider ↔ Model Selection Contract — Design Spec

**Date:** 2026-08-27
**Author:** Wisp (Muse Spark)
**Status:** Draft → Review
**Related:** `wisp/provider_catalog.py`, `wisp/composition.py`, `wisp/provider_select.py`, `wisp/commands.py`, `wisp/providers/openai.py`, `wisp/core/stateless.py`

## 1. Problem

- `nvidia` with `qwen2.5-coder` (an `ollama` model) was served and then `404` mid-turn → `0 tools` with only `thinking` visible (`Turn 9·10 0 tools 1m39s`). `stealth/ox-alpha` deprecated → `404` with no clear selection error.
- `unknown_model` was a *warning* and still served, not a hard error for cloud providers.
- `list_models(nvidia)` required a live API call; with no key it returned `[]` and then `resolve_selection` said `ok, could not be verified` instead of detecting the mismatch.
- `write_file` with `"in a file"` and no path failed `jsonschema` → `tool_result` error → next iteration stalled past `CHUNK_DEADLINE 90s` → `done` with `0 tools`.
- `OpenAIProvider` only yielded `tool_calls` when `finish_reason=="tool_calls"` — `NVIDIA NIM` returns `stop` even with deltas, so valid writes were dropped.

## 2. Goals

- **Single source of truth:** `provider_catalog` is the only place that knows what a provider can serve.
- **Strict for cloud, lenient for local:** `nvidia/openai/openrouter` must be in the live list; `ollama` may be unverified (local models not yet in daemon).
- **REPL slash commands share the same contract as `composition` and the server** — no drift.
- **Tool/streaming contract is independent but linked:** selection guarantees the *model exists*; streaming guarantees the *tool call lands* even with `stop`, large `18k` payloads, and missing `path`.

## 3. Non-Goals

- No new provider is added.
- No change to `WispAgentCore`'s stateless turn loop beyond validation/defaulting.
- No change to `max_tokens` default (still `131072`); cloud cap is in the provider, not the config.

## 4. Architecture

```
User input (REPL)
  → commands.dispatch("/provider …" | "/model …")
    → provider_select.parse_target + provider_catalog.list_models/resolve_selection + probe
    → provider_select.apply_switch(runtime, session, config, provider, model) + persist
    → composition._create_core() on next turn (reads runtime.config, calls resolve_selection again, auto-corrects if needed, builds Provider via factory)
    → WispAgentCore.turn(session, prompt) → _guarded_provider_stream → provider.generate_stream_events → _turn_inner → ToolExecutor

Server
  GET /api/models → provider_catalog.list_models for all providers + resolve_selection for active
  POST /api/models/select → same apply_switch + persist seam as REPL
```

Two-phase wiring (`composition.py:164` `runtime.orchestrator ↔ tool_executor`) is unchanged.

## 5. Contracts

### 5.1 `wisp/provider_catalog.py`

```python
@dataclass(frozen=True)
class Resolution:
    provider: str
    model: str
    status: Literal["ok", "model_unset", "unknown_model", "unreachable"]
    detail: str = ""
    suggested: str = ""          # first live model or closest
    alternatives: list[str] = [] # _closest() ranking

def list_providers() -> list[ProviderInfo]: ...
def list_models(provider: str, cfg) -> list[str]: ...
def resolve_selection(cfg) -> Resolution: ...
```

- `list_models("nvidia", cfg)` → `sorted(_authed_get(...))` if non-empty, else `sorted(NVIDIAProvider._MODEL_CONTEXT.keys())` (fallback so `unknown_model` can be detected even with no key; live list is ~80 models including `nvidia/*`, static list is 10 `nvidia/*` only).
- `resolve_selection` checks `KNOWN_PROVIDERS[provider].requires_key` and no `api_key` (cfg + `NVIDIA_API_KEY`/`OPENROUTER_API_KEY`/`OPENAI_API_KEY`/`WISP_API_KEY` env) → `unreachable` before any `list_models` call.
- `provider == "ollama"` with `available==[]` → `ok` with `detail="could not be verified"` (lenient for local). `nvidia/openai/openrouter` with `available==[]` and `model` set → `unreachable` (`Provider 'x' is not reachable — could not list models to verify 'y'`), not `ok`.
- `model not in available` → `unknown_model` with `suggested=available[0]` and `alternatives=_closest(model, available)`; for cloud, slash commands hard-fail, `composition` auto-corrects (see 5.2).

### 5.2 `wisp/composition.py:_create_core`

```python
cfg = runtime.config or self.config
resolution = resolve_selection(cfg)
if resolution.status == "model_unset":
    cfg = _replace_cfg(cfg, model=resolution.suggested)  # local-first pick
elif resolution.status == "unknown_model":
    is_mock = hasattr(cfg, "_mock_name") # MagicMock guard
    should_autocorrect = resolution.suggested and not is_mock and resolution.provider in ("nvidia","openai","openrouter")
    if should_autocorrect:
        cfg = _replace_cfg(cfg, model=resolution.suggested)
        runtime.config = cfg  # persist for next turn
        logger.warning("… Auto-correcting to '%s'. Did you mean: …", …)
    else:
        logger.warning("… Serving anyway. Did you mean: …", …)
elif resolution.status == "unreachable":
    provider = _NullProvider(detail)
    return WispAgentCore(config=cfg, provider=provider, …) # surfaces as error, not 404 mid-turn
```

`_replace_cfg` handles both `WispConfig.replace` and test doubles (`object.__setattr__`).

### 5.3 `wisp/provider_select.py` (REPL/server glue)

- `parse_target(arg: str) -> {provider, model}` — existing, unchanged.
- `probe(provider) -> (ok, detail)` — calls `provider.health_check()` / `list_models`.
- `apply_switch(runtime, session, config, provider, model)` — updates `runtime.config` (if present), `session["model"]`, and `config`; invalidates `runtime._session_cores` if fingerprint changed.
- `persist(update: dict)` → `save_config`.

### 5.4 Slash Commands `wisp/commands.py`

- `/provider` (no args) → `list_providers` + `provider_catalog.list_models` for each, `probe`, show `→` for active, `[needs key]` for `requires_key`.
- `/provider <name>` → `missing_key` check, `probe`, `list_models` live, `apply_switch(provider=name, model=None)` (leaves model `model_unset` so next `resolve_selection` picks `suggested`), `persist`, then print `Available models (N):` for that provider and hint `Pick with /model <num|name>`.
- `/model` (no args) → `list_models(active_provider, agent.config)` live, show `→` for active model.
- `/model <provider> <model>` or `<provider>/<model>` or `<num>` or `<name>` → `parse_target`, `list_models(target_provider)`, hard fail if `target_model not in live` for `nvidia/openai/openrouter` with `Unknown model 'X' for 'nvidia'. Did you mean: … Available: …`, else `apply_switch` + `persist`. For `ollama`, warn but allow.
- All paths use the same `provider_catalog` live list as `composition`; no direct `agent.client.list_models` without catalog.

### 5.5 Streaming & Tool-Use `wisp/providers/openai.py` + `wisp/core/stateless.py` + `wisp/tools/filesystem.py` + `wisp/context_assembler.py`

- `OpenAIProvider.generate_stream_events` accumulates `tool_calls` deltas by `index` and yields on `finish_reason=="tool_calls" or bool(tool_call_accum)` (handles `stop` from `NVIDIA NIM`).
- `OpenAIProvider._build_payload` caps `max_tokens` for cloud to avoid `402` — if `max_tokens > 16384` and `api_base in ("https://openrouter.ai/api/v1", "https://integrate.api.nvidia.com/v1")`, cap to `16384`.
- `WispAgentCore._guarded_provider_stream` — `FIRST_TOKEN_DEADLINE_S=90`, `CHUNK_DEADLINE_S=90`, `WISP_STREAM_ATTEMPTS=3` (env-tunable, `provider_stream.guarded_provider_stream` is the injected testable seam).
- `WispAgentCore._validate_tool_args("write_file", args)` + `WispAgentCore._turn_inner` batch path — salvages `{"_raw": "…"}` and defaults `path` to `./output.md` (markdown) / `./output.txt`; mutates `args` in place. Both `tool_call` and `tool_calls` batch paths are covered.
- `tool_write_file(path="", workspace="", content="", **kwargs)` — same defaulting for direct `ToolExecutor` callers (tests/ACP) when `stateless` is bypassed.
- `wisp/context_assembler.py:47 DEFAULT_SYSTEM` and `ContextAssembler.__init__.default_system` (now `= DEFAULT_SYSTEM`, no drift) — guideline 3.

## 6. Data Flow

```
REPL: "/provider nvidia" → cmd_provider → provider_catalog.list_models("nvidia") → prints 80 models → user "/model 3" → cmd_model → validates 3 in live list → apply_switch → persist → next turn → composition._create_core → resolve_selection (ok) → NVIDIAProvider(model="nvidia/…") → WispAgentCore.turn → provider stream → tool_call write_file → stateless validation (default path) → ToolExecutor → writes file → tool_result → next iteration → done

REPL: "/model nvidia/qwen2.5-coder" → list_models("nvidia") → qwen not in live → hard fail "Unknown model 'qwen2.5-coder' for 'nvidia'. Did you mean: deepseek-ai/deepseek-coder-6.7b-instruct? Available: …" → no switch, no persist, no 404

Direct: WISP_PROVIDER=nvidia WISP_MODEL=qwen2.5-coder (no slash command) → composition._create_core → resolve_selection unknown_model → auto-correct to 01-ai/yi-large (cloud) → warning → next turn succeeds with 1 tool
```

## 7. Error Handling

- `unknown_model` on cloud → slash command hard error, `composition` auto-correct + warning (not silent 404).
- `unreachable` (no key / provider down) → `_NullProvider` with `error` event `"Provider 'nvidia' requires an API key …"` surfaced as `error` card, not `0 tools` with only `thinking`.
- `402` (credit) → provider yields `error status 402`, `guarded_provider_stream` treats as permanent (no retry), `stateless` surfaces `error` with `hint="lower max_tokens or add credits"` (already in `openai.py:104`).
- `write_file` missing `path` → defaulted, not `Schema validation failed`.

## 8. Testing

- `tests/test_provider_selection_contract.py` — 12 existing + `test_nvidia_unknown_autocorrects` (qwen on nvidia → auto-correct), `test_nvidia_no_key_unreachable` (no WISP_API_KEY → unreachable), `test_openai_tool_call_on_stop` (finish_reason stop with tool_call_accum → yields tool_call), `test_ollama_lenient_unverified` (available==[] → ok).
- `tests/test_tool_executor_shared_state.py` — C1-C4 + `test_write_file_defaults_path` (content without path → ./output.md) and `test_write_file_raw_salvage`.
- `tests/test_provider_integration.py` — `test_create_core_uses_factory_when_provider_set` now expects `_replace_cfg` handling for MagicMock (no auto-correct for ollama mock).
- `tests/test_integration_e2e.py` — `_TestConfig` now handled via `_replace_cfg`.
- `wisp/context_assembler.py` — `ContextAssembler.__init__.default_system` now aliases `DEFAULT_SYSTEM` (no drift).
- Manual REPL: `/provider nvidia` → list 80, `/model nvidia/qwen2.5-coder` → hard error `Unknown model … Did you mean: deepseek-ai/deepseek-coder-6.7b-instruct? Available: …`, `/model nvidia/nemotron-3-ultra-550b-a55b` → `✓ Model set`, then `"write all this in a file"` → `✓ Wrote … bytes to ./output.md`.

## 9. Alternatives Considered

- Single hardcoded `DEFAULT_MODEL` — rejected (rots, caused original 404s).
- Allow any model for all providers — rejected (silent 404s).
- Per-tool `max_tokens` — rejected (adds complexity; cap in provider is simpler).

## 10. Decisions

- `/provider` without `/model` leaves `model_unset` (does **not** auto-pick). Next turn's `composition._create_core` picks `suggested` (local-first) via `resolve_selection: model_unset` and logs `No model configured — serving 'X'`. This matches `e564a7b` and avoids `persist()` on a transient `suggested` that would revert on restart; `persist` only happens on an explicit `/model` pick or on `unknown_model` auto-correct. Listing 80 models then showing an empty prompt is intentional — it forces an explicit pick for `model_unset`.

