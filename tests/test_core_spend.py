"""`core.spend.estimate_spend`: what a transcript cost, reconstructed (providers report no usage in the stream).

Each assistant message is one provider call. That call's input is the fixed per-call overhead plus every message before it;
its output is the message itself (content and tool-call arguments). Summing over calls is the spend; the transcript counted
once understates it by roughly the number of calls. Shared by the subagent runner (children) and the runtime (the parent).
"""
from __future__ import annotations

from wisp.core.spend import estimate_spend

OVERHEAD = 12_000  # chars: 3,000 tokens per call at 4 chars/token


def _conversation() -> list[dict]:
    return [
        {"role": "user", "content": "u" * 400},                                                         # 100 tokens
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": "a" * 40}}]},
        {"role": "tool", "content": "t" * 800},                                                         # 200 tokens
        {"role": "assistant", "content": "f" * 200},                                                    # the final report
    ]


def test_each_call_resends_the_overhead_and_the_history_so_far():
    in_tok, out_tok = estimate_spend(_conversation(), OVERHEAD)
    # call 1: overhead + user                      = 12,400 chars -> 3,100 in; its output: 40 chars of arguments -> 10
    # call 2: overhead + user + call + tool result = 13,240 chars -> 3,310 in; its output: 200 chars -> 50
    assert in_tok == 3_100 + 3_310
    assert out_tok == 10 + 50


def test_it_is_far_more_than_the_transcript_counted_once():
    once = sum(len(m.get("content", "")) for m in _conversation()) // 4
    in_tok, out_tok = estimate_spend(_conversation(), OVERHEAD)
    assert in_tok + out_tok > 15 * once


def test_without_overhead_the_history_is_still_resent():
    in_tok, out_tok = estimate_spend(_conversation(), 0)
    assert in_tok == 100 + 310 and out_tok == 60


def test_no_provider_call_means_no_spend():
    assert estimate_spend([{"role": "user", "content": "hi"}], OVERHEAD) == (0, 0)
    assert estimate_spend([], OVERHEAD) == (0, 0)


def test_from_index_charges_only_this_turns_calls_but_counts_the_earlier_history_as_input():
    msgs = _conversation() + [{"role": "user", "content": "w" * 400}, {"role": "assistant", "content": "z" * 80}]
    # only the last call (index 5) is this turn's; its input is overhead + EVERYTHING before it
    in_tok, out_tok = estimate_spend(msgs, OVERHEAD, from_index=4)
    history = 400 + 40 + 800 + 200 + 400  # chars of messages 0..4 (the assistant tool-call message counts its arguments)
    assert in_tok == (OVERHEAD + history) // 4
    assert out_tok == 80 // 4


def test_chars_per_token_is_honoured():
    a = estimate_spend(_conversation(), OVERHEAD, chars_per_token=4)
    b = estimate_spend(_conversation(), OVERHEAD, chars_per_token=2)
    assert b[0] == 2 * a[0] and b[1] == 2 * a[1]


def test_system_messages_count_as_input():
    msgs = [{"role": "system", "content": "s" * 400}] + _conversation()
    in_tok, _ = estimate_spend(msgs, 0)
    assert in_tok == (400 + 400) // 4 + (400 + 400 + 40 + 800) // 4
