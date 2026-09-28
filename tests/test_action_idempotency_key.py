"""Migration P1 — durable action idempotency keys.

The audit found no durable idempotency on the live turn path. These tests pin
the primitive that closes it: a canonical key stamped on both the `TOOL_CALL`
(intent, written before dispatch) and its `TOOL_RESULT` (resolution).

An action whose key appears as a call but never as a real result was
dispatched and never resolved. That is a *detectable* property, which is what
recovery needs — the honest response is to surface it, never to silently
repeat it.
"""

from __future__ import annotations

from wisp.core.action_key import action_key
from wisp.core.session import Session, SessionEvent


# ── Key stability ───────────────────────────────────────────────────────


class TestKeyStability:
    def test_same_call_yields_the_same_key(self):
        assert action_key("read_file", {"path": "a.py"}) == \
               action_key("read_file", {"path": "a.py"})

    def test_different_args_yield_different_keys(self):
        assert action_key("read_file", {"path": "a.py"}) != \
               action_key("read_file", {"path": "b.py"})

    def test_different_tools_yield_different_keys(self):
        assert action_key("read_file", {"path": "a.py"}) != \
               action_key("write_file", {"path": "a.py"})

    def test_key_order_does_not_matter(self):
        """Two spellings of the same invocation are the same invocation."""
        assert action_key("t", {"a": 1, "b": 2}) == \
               action_key("t", {"b": 2, "a": 1})

    def test_json_string_and_dict_agree(self):
        """The provider wire shape is a JSON string; the normalized shape is
        a dict. The same call must not acquire two identities."""
        assert action_key("read_file", '{"path": "a.py"}') == \
               action_key("read_file", {"path": "a.py"})

    def test_non_json_string_is_hashed_not_crashed(self):
        """An unparseable argument must not break the call."""
        key = action_key("t", "not json at all")
        assert isinstance(key, str) and len(key) == 32

    def test_unserializable_args_do_not_raise(self):
        key = action_key("t", {"s": {1, 2, 3}})
        assert isinstance(key, str) and len(key) == 32

    def test_key_is_short_and_hex(self):
        key = action_key("t", {"a": 1})
        assert len(key) == 32
        int(key, 16)  # raises if not hex


# ── Resolution tracking ─────────────────────────────────────────────────


class TestUnresolvedActions:
    def test_resolved_action_is_not_reported(self):
        s = Session(session_id="a")
        k = action_key("read_file", {"path": "a.py"})
        s.apply(SessionEvent.tool_call_event(1, "read_file", {"path": "a.py"},
                                             action_key=k))
        s.apply(SessionEvent.tool_result_event(2, "read_file", "body",
                                               action_key=k))
        assert s.unresolved_actions() == []

    def test_call_without_result_is_reported(self):
        """The crash-between-intent-and-result case."""
        s = Session(session_id="a")
        k = action_key("write_file", {"path": "a.py", "content": "x"})
        s.apply(SessionEvent.tool_call_event(1, "write_file",
                                             {"path": "a.py", "content": "x"},
                                             action_key=k))
        pending = s.unresolved_actions()
        assert len(pending) == 1
        assert pending[0]["action_key"] == k
        assert pending[0]["name"] == "write_file"
        assert pending[0]["arguments"] == {"path": "a.py", "content": "x"}

    def test_synthesized_placeholder_does_not_resolve(self):
        """A placeholder records that we never learned the outcome — which is
        precisely the state `unresolved_actions` reports. Treating it as
        resolution would hide the ambiguity and invite a blind repeat."""
        s = Session(session_id="a")
        k = action_key("write_file", {"path": "a.py"})
        s.apply(SessionEvent.tool_call_event(1, "write_file", {"path": "a.py"},
                                             action_key=k))
        s.apply(SessionEvent.tool_result_event(
            2, "write_file", "[no result recorded before turn ended]",
            synthesized=True, action_key=k))
        assert len(s.unresolved_actions()) == 1

    def test_two_calls_one_result_reports_the_other(self):
        s = Session(session_id="a")
        k1 = action_key("t", {"a": 1})
        k2 = action_key("t", {"a": 2})
        s.apply(SessionEvent.tool_call_event(1, "t", {"a": 1}, action_key=k1))
        s.apply(SessionEvent.tool_call_event(2, "t", {"a": 2}, action_key=k2))
        s.apply(SessionEvent.tool_result_event(3, "t", "ok", action_key=k1))
        pending = s.unresolved_actions()
        assert [p["action_key"] for p in pending] == [k2]

    def test_duplicate_calls_report_once(self):
        s = Session(session_id="a")
        k = action_key("t", {"a": 1})
        s.apply(SessionEvent.tool_call_event(1, "t", {"a": 1}, action_key=k))
        s.apply(SessionEvent.tool_call_event(2, "t", {"a": 1}, action_key=k))
        assert len(s.unresolved_actions()) == 1

    def test_report_follows_dispatch_order(self):
        s = Session(session_id="a")
        keys = [action_key("t", {"a": i}) for i in range(3)]
        for i, k in enumerate(keys):
            s.apply(SessionEvent.tool_call_event(i + 1, "t", {"a": i},
                                                 action_key=k))
        assert [p["action_key"] for p in s.unresolved_actions()] == keys

    def test_replay_rebuilds_the_unresolved_set(self):
        """The whole point: a fresh replay of the log knows what was left
        dangling, with no extra bookkeeping."""
        k = action_key("write_file", {"path": "a.py"})
        events = [
            SessionEvent.user_message(1, "do it"),
            SessionEvent.tool_call_event(2, "write_file", {"path": "a.py"},
                                         action_key=k),
        ]
        s = Session(session_id="a")
        s.replay(events)
        assert [p["action_key"] for p in s.unresolved_actions()] == [k]

    def test_legacy_events_without_keys_are_ignored(self):
        """Pre-migration logs carry no keys; they must not fabricate
        unresolved actions."""
        s = Session(session_id="a")
        s.apply(SessionEvent.tool_call_event(1, "read_file", {"path": "a.py"}))
        assert s.unresolved_actions() == []

    def test_replay_resets_the_unresolved_set(self):
        s = Session(session_id="a")
        s.apply(SessionEvent.tool_call_event(
            1, "t", {"a": 1}, action_key=action_key("t", {"a": 1})))
        assert len(s.unresolved_actions()) == 1
        s.replay([SessionEvent.user_message(1, "hi")])
        assert s.unresolved_actions() == []


# ── The keys survive the round trip through storage ─────────────────────


class TestKeysSurvivePersistence:
    def test_keys_survive_append_and_reload(self, tmp_path):
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.store import UnifiedStore

        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)
        k = action_key("write_file", {"path": "a.py"})

        repo.append_events("s1", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.tool_call_event(2, "write_file", {"path": "a.py"},
                                         action_key=k),
        ])
        reloaded = repo.load_session("s1")
        assert reloaded is not None
        assert [p["action_key"] for p in reloaded.unresolved_actions()] == [k]
        assert reloaded.tool_calls[0]["name"] == "write_file"

    def test_resolution_survives_the_round_trip(self, tmp_path):
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.store import UnifiedStore

        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)
        k = action_key("read_file", {"path": "a.py"})

        repo.append_events("s2", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.tool_call_event(2, "read_file", {"path": "a.py"},
                                         action_key=k),
            SessionEvent.tool_result_event(3, "read_file", "body",
                                           action_key=k),
        ])
        reloaded = repo.load_session("s2")
        assert reloaded.unresolved_actions() == []
        assert reloaded.messages[-1]["role"] == "tool"
