"""What a transcript cost: input and output tokens, reconstructed per provider call.

Providers report no usage in the stream, so spend is estimated from the transcript. Every provider call re-sends the system
prompt, the tool schemas and the whole history, and a turn with tool rounds makes several calls, so counting the transcript
once (or only the user's new prompt) understates the spend by roughly the number of calls times the overhead. Each assistant
message is one call: its input is the fixed per-call overhead plus every message before it, its output is the message itself
(content and tool-call arguments).

One function, used by both sides that have to agree: the subagent runner (a child's budget) and the runtime (the parent's
telemetry, which charges the cost meter). It lived on ``SubagentRunner``, so only children were charged for what they spent.
"""

from __future__ import annotations

from typing import Any

from wisp.infra.token_counter import TokenCounter

__all__ = ["estimate_spend"]


def estimate_spend(
    messages: list[dict[str, Any]],
    overhead_chars: int = 0,
    *,
    from_index: int = 0,
    chars_per_token: int = 4,
) -> tuple[int, int]:
    """Estimate ``(input_tokens, output_tokens)`` the provider was sent and returned for ``messages``.

    ``overhead_chars``: the fixed per-call overhead (system prompt plus tool schemas). ``from_index``: only assistant messages
    at or after this index are charged (a turn's own calls), but every earlier message still counts as input to them, because
    the provider is sent the whole history each call. ``chars_per_token`` is the ruler (``WISP_CHARS_PER_TOKEN``).
    """
    counter = TokenCounter(chars_per_token=chars_per_token)
    history_chars = 0
    in_tok = out_tok = 0
    for i, msg in enumerate(messages):
        content = msg.get("content", "") or ""
        chars = len(content if isinstance(content, str) else str(content))
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []) or []:
                args = tc.get("function", {}).get("arguments", "")
                chars += len(args) if isinstance(args, str) else len(str(args))
            if i >= from_index:
                in_tok += counter.estimate_chars(overhead_chars + history_chars)
                out_tok += counter.estimate_chars(chars)
        history_chars += chars
    return in_tok, out_tok
