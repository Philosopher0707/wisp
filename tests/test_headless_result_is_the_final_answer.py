"""`--print`'s `content` is the turn's final answer, not every draft the harness sent back (baseline D1).

A round the harness rejects (the verification floor, the reasoning core's R1) is followed by a
`system` event and another round. The headless result joined every `content` event, so a model
nudged five times answered with its draft six times. Each round's text is still shown in the REPL
as it happens; the JSON answer is the last round, with any note the harness appended to it.
"""

from __future__ import annotations

from wisp.transport.headless import HeadlessTransport


def _result(events):
    t = HeadlessTransport()
    t.events = list(events)
    return t.collect_result()


def test_rejected_drafts_are_not_part_of_the_answer():
    out = _result([
        {"type": "content", "text": "Done. All tests pass."},
        {"type": "system", "message": "[SYSTEM] Verification loop: ...", "level": "warning"},
        {"type": "content", "text": "Done. All tests pass."},
        {"type": "system", "message": "Harness note: not backed", "level": "warning"},
        {"type": "content", "text": "Done. All tests pass."},
        {"type": "content", "text": "\n\nHarness note: the completion claim above is not backed"},
        {"type": "done", "turns": 3},
    ])
    assert out["content"] == "Done. All tests pass.\n\nHarness note: the completion claim above is not backed"


def test_text_before_a_tool_call_is_not_the_answer():
    out = _result([
        {"type": "content", "text": "Let me read it."},
        {"type": "tool_call", "name": "read_file", "arguments": {}},
        {"type": "tool_result", "name": "read_file", "result": "x"},
        {"type": "content", "text": "It says x."},
        {"type": "done", "turns": 2},
    ])
    assert out["content"] == "It says x."


def test_a_streamed_answer_is_joined_without_separators():
    out = _result([{"type": "content", "text": "He"}, {"type": "content", "text": "llo"}, {"type": "done", "turns": 1}])
    assert out["content"] == "Hello"


def test_a_system_event_after_the_answer_does_not_erase_it():
    out = _result([{"type": "content", "text": "answer"}, {"type": "system", "message": "note", "level": "info"},
                   {"type": "done", "turns": 1}])
    assert out["content"] == "answer"


def test_iterations_come_from_the_done_event():
    assert _result([{"type": "content", "text": "a"}, {"type": "done", "turns": 4}])["iterations"] == 4
