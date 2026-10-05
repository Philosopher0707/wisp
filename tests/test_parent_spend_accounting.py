"""What the PARENT is charged for a turn: every provider call, not the size of the user's prompt.

After each turn `AgentRuntime` recorded `prompt_tokens = count(prompt)` (the user's new message) and `completion_tokens =
count(assistant text)`. Those numbers feed `Telemetry.record_turn`, which charges the cost meter (the only charge path;
`composition` wires a `CostMeter` into `Telemetry`), so `max_cost_usd` was bounded by the size of what the user typed. Every
provider call re-sends the system prompt, the tool schemas (about 18,000 tokens under the core profile) and the history, and a
turn with tool rounds makes several calls. Measured below: a two-call turn on a 2-character prompt was charged about one token.

The same fix was made for subagent children (`_estimate_spend`); this is the parent's half, using the shared
`wisp.core.spend.estimate_spend`.
"""
from __future__ import annotations

from tests.reliability.test_13h4_success_semantics import (
    _content_only,
    _DictProvider,
    _read_call,
    _run_turn,
    _runtime,
    _session,
    _tool_round,
)


class _Meter:
    """Stands in for `CostMeter`: records exactly what Telemetry charges."""

    def __init__(self) -> None:
        self.charges: list[tuple[str, int, int]] = []

    def try_charge(self, model, *, input_tokens, output_tokens):
        self.charges.append((model, input_tokens, output_tokens))


def _two_call_turn(tmp_path, sid="sp"):
    """One tool round then the answer: two provider calls."""
    prov = _DictProvider([_tool_round([_read_call("a.txt", "c1")]), _content_only("done")])
    runtime, _repo, ws = _runtime(prov, tmp_path, ws_files={"a.txt": "alpha"})
    meter = _Meter()
    runtime.telemetry.cost_meter = meter
    runtime._model = "test-model"
    session = _session(ws, sid)
    _run_turn(runtime, session, prompt="go")
    return runtime, session, meter, prov


def test_the_provider_really_was_called_twice(tmp_path):
    _runtime_, _session_, _meter, prov = _two_call_turn(tmp_path)
    assert prov.calls == 2


def test_each_provider_call_is_charged_the_overhead_it_re_sent(tmp_path):
    runtime, session, meter, _prov = _two_call_turn(tmp_path)
    cpt = runtime.config.chars_per_token
    overhead_tokens = runtime.prompt_overhead_chars(session) // cpt
    assert overhead_tokens > 1_000, "the system prompt and tool schemas are thousands of tokens"
    charged_in = sum(c[1] for c in meter.charges)
    assert charged_in >= 2 * overhead_tokens, (
        f"two provider calls each re-send ~{overhead_tokens} tokens of overhead, but the cost meter was charged {charged_in}")
    assert runtime.telemetry.prompt_tokens_total == charged_in, "the totals and the meter agree"


def test_the_second_turn_pays_for_the_history_but_not_for_the_first_turns_calls_again(tmp_path):
    runtime, session, meter, _prov = _two_call_turn(tmp_path, "sp2")
    first = sum(c[1] for c in meter.charges)
    runtime._model = "test-model"
    runtime.core_provider = None
    # a plain one-call turn on the same session
    from tests.reliability.test_13h4_success_semantics import _DictProvider as P

    prov2 = P([_content_only("again")])
    runtime._get_core(session["id"]).provider = prov2
    _run_turn(runtime, session, prompt="and again")
    second = sum(c[1] for c in meter.charges) - first
    cpt = runtime.config.chars_per_token
    overhead_tokens = runtime.prompt_overhead_chars(session) // cpt
    assert second >= overhead_tokens, "one more call re-sends the overhead"
    assert second < first, "and is charged ONE call, not the previous turn's two calls again"


def test_a_turn_is_never_charged_less_than_before(tmp_path):
    runtime, _session_, meter, _prov = _two_call_turn(tmp_path, "sp3")
    assert all(c[1] >= 1 and c[2] >= 1 for c in meter.charges)
