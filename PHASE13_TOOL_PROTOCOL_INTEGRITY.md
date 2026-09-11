# PHASE 13 ADJACENT — TOOL FAILURE → MESSAGE PROTOCOL INTEGRITY

## Exact live reproduction
4× `web_search` TLS failures rendered ✓; `web_fetch` guesses 404'd;
fetch breaker correctly paused fetching; next provider request 400'd
(`messages[14]: missing field tool_call_id`); turn ended
`9 tools (4 ok, 5 failed)`.

## Root cause
Two independent defects converging on one 400:
1. **ID loss** (`tool_executor.py::execute`): ~10 early-return paths
   (repeat-guard, fetch breaker, pre-tool hooks, plan/danger/perm
   blocks, user-decline, pre-bash/pre-file hooks) emitted tool results
   WITHOUT forwarding the inbound `tool_call_id`. History defaulted it
   to `""`; the OpenAI serializer sent `""`; strict gateways reject it
   as missing. The breaker refusal in the live session was the trigger.
2. **False ✓** (`transport/cli.py::_is_error_result`): the slow-tool
   spinner path tested raw strings (`startswith("Error"/"[Error"`),
   never parsing `{"status":"error"}` JSON envelopes — so network-slow
   failures (TLS/404, always >50ms) rendered success. (The header
   renderer and the turn counters already parsed correctly.)

## Tool-result lifecycle
`model tool_call(id)` → `stateless` normalize → `ToolExecutor.execute`
(`tool_call_id` threaded) → `_tool_result_event` (ID iff forwarded) →
stateless history (`role:tool`, ID or `""`) → OpenAI `_build_payload`
normalize (sent verbatim, `""` included) → provider 400.

## Fix
- All `execute()` early emissions forward `tool_call_id` (9 sites; the
  ID belongs to the originating call — never generated, never dropped).
- Shared `renderer.result_is_error` (parses JSON envelopes + executor
  and web-tool markers); adopted by the spinner check, the result
  header, and the progress counters (one predicate, three surfaces).
- Preflight pairing validator in `OpenAIProvider._build_payload`
  (inherited by OpenRouter/NVIDIA): every tool message must reference a
  known assistant call ID; empty/missing/foreign IDs raise ValueError
  before submission (fail closed; the adapter converts it to an honest
  error event, never a 400). Ollama native protocol carries no
  tool_call_id (verified absence — nothing to gate).

## Validation strategy
Fail closed at three depths: emission (IDs forwarded), history (IDs
preserved verbatim, never repaired), submission (preflight rejects).
§13 respected (no deletion of failed results); §14 respected (no
generated IDs — preflight raises instead).

## Parallel-tool behavior
Mixed parallel results keep per-call IDs (tested via concurrent
executions); out-of-order completion irrelevant (identity rides the
event, never position). Retry attempts receive fresh authoritative IDs
from new model calls; results pair per-attempt (tested).

## Exception behavior
Timeout (structured error), Python exception (generic wrap),
approval-cancel (verdict event) all carry the originating ID (tested).
CancelledError still propagates (turn terminates; no invalid message).

## Breaker behavior
Breaker refusals now carry the inbound ID; the twin session (2× TLS
fail + 3× fetch incl. breaker trip) serializes to 5 correctly paired
tool messages; failures preserved as honest errors.

## Provider matrix
| Provider | tool_call_id in protocol | Preflight | Result |
|---|---|---|---|
| OpenAI | yes | enforced, tested | valid/invalid matrices green |
| OpenRouter | yes (inherits `_build_payload`) | inherited, tested | green |
| NVIDIA | yes (inherits) | inherited | green (inheritance pinned) |
| Ollama native/direct | no (verified absent) | n/a | unaffected |

## G1B/G1D/G1E regression
Stream contract, provider faults, salvage gate, retry integrity,
no-bypass suites green (117 in one run). Turn-stats accounting
verified authoritative (parses status; untouched). Retry keeps
per-attempt IDs (tested). Salvage gate untouched.

## Remaining unknowns
- Persisted old sessions containing `""` IDs will now fail closed at
  submission (honest error directing to fresh history; previously 400).
- TUI/server renderers audited by grep only (no equivalent spinner
  logic found; CLI paths unified).
- Model URL fabrication and TLS-verification policy out of scope
  (fetch layer correctly 404s; verification stays enabled).
