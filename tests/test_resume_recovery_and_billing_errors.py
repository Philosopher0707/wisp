"""Resuming a session whose last turn did not finish, and a provider that refuses on billing.

Seen live:

    wisp repl -S 1f8affa9-…
    wisp ❯ continue the task
    [WARNING] wisp.core.runtime: Session 1f8affa9-… has incomplete turn — replaying

…and then every turn ended with no reply.

**1. The recovery said "replaying" and did not.** The session's journal diverged from its replay
digest, so `load_session` raised `ReplayDivergence`, and the runtime's `except Exception: pass`
kept the saved history while the log claimed a replay. On a journal with a *gap*, it did the
opposite of safe: it replaced a good saved transcript with the gapped replay. That is a
provider-invalid history, and it is exactly what `SessionRepository.reconstruction_source`
refuses (M4). The recovery now uses the journal only when it can be trusted (it replays, has no
gap, and has a turn body), keeps the saved transcript otherwise, and says which one it used and
why.

**2. A 402 read like a crash.** The turns were empty because OpenRouter refused every request:
*"API error 402: Prompt tokens limit exceeded: 23791 > 8517"*, the API key's usage limit. The
message now keeps the provider's own words and adds what to do: retrying fails the same way;
raise the limit, or shrink the prompt.

**Observation points are production** (F96): the real `AgentRuntime` (via `CompositionRoot`)
over a real SQLite session journal, with only the LLM scripted; and the real
`OpenRouterProvider.generate_stream_events` with only the HTTP response faked.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from wisp.core.session import SessionEvent

SAVED = "SAVED-TRANSCRIPT-MARKER"
JOURNAL = "JOURNAL-TRANSCRIPT-MARKER"


def _root(tmp_path, monkeypatch):
    from wisp.providers.mock import MockProvider

    class Recording(MockProvider):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.seen: list[list[dict]] = []

        def generate_stream_events(self, system_prompt, messages, tools=None, **kw):
            self.seen.append(json.loads(json.dumps(messages, default=str)))
            return super().generate_stream_events(system_prompt, messages, tools, **kw)

    provider = Recording(responses=["Resumed."])
    import wisp.provider_catalog as pc
    from wisp.providers.factory import ProviderFactory
    monkeypatch.setattr(pc, "resolve_selection", lambda cfg: SimpleNamespace(
        status="ok", suggested=None, provider="mock", detail="", model="mock-model",
        alternatives=[]))
    monkeypatch.setattr(ProviderFactory, "from_config", lambda self, cfg: provider)
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig
    ws = tmp_path / "ws"
    ws.mkdir()
    root = CompositionRoot(WispConfig().replace(workspace=str(ws), provider="mock",
                                                model="mock-model", auto_approve=True))
    assert root.runtime.session_repo is not None, "floor: the journal must be wired"
    return root, provider, str(ws)


async def _resume(root, ws, sid, journal: list[SessionEvent]) -> None:
    repo = root.runtime.session_repo
    for ev in journal:
        repo.append_event(sid, ev)
    assert not repo.was_last_turn_complete(sid), "floor: the last turn must be unfinished"
    session = await root.runtime.get_or_create_session(sid, model="mock-model", workspace=ws)
    session["messages"] = [{"role": "user", "content": "build it"},
                           {"role": "assistant", "content": SAVED}]
    [ev async for ev in root.runtime.run_turn(session, "continue the task")]


def _history_sent(provider) -> str:
    assert provider.seen, "floor: the provider must have been called"
    return json.dumps(provider.seen[0])


def _recovery_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "wisp.core.runtime"
            and "did not finish" in r.getMessage() or "incomplete turn" in r.getMessage()]


class TestResumeAfterAnUnfinishedTurn:
    @pytest.mark.asyncio
    async def test_a_diverged_journal_keeps_the_saved_transcript_and_says_so(
            self, tmp_path, monkeypatch, caplog) -> None:
        root, provider, ws = _root(tmp_path, monkeypatch)
        caplog.set_level(logging.INFO, logger="wisp.core.runtime")
        await _resume(root, ws, "s-div", [
            SessionEvent.user_message(0, "build it"),
            SessionEvent.assistant_message(1, JOURNAL),
            SessionEvent.replay_digest_event(2, "0" * 32),   # not what replay produces
            SessionEvent.done(3, turns=1),
            SessionEvent.user_message(4, "continue the task"),
        ])
        assert SAVED in _history_sent(provider)
        logs = " | ".join(_recovery_logs(caplog))
        assert "saved transcript" in logs and "replay" in logs.lower(), logs
        assert "— replaying" not in logs, "the log must not claim a replay that did not happen"

    @pytest.mark.asyncio
    async def test_a_gapped_journal_does_not_replace_the_saved_transcript(
            self, tmp_path, monkeypatch, caplog) -> None:
        root, provider, ws = _root(tmp_path, monkeypatch)
        caplog.set_level(logging.INFO, logger="wisp.core.runtime")
        await _resume(root, ws, "s-gap", [
            SessionEvent.user_message(0, "build it"),
            SessionEvent.assistant_message(1, JOURNAL),
            # sequence 2 lost (ADR-0004 permits a durable write to fail silently)
            SessionEvent.user_message(3, "continue the task"),
        ])
        sent = _history_sent(provider)
        assert SAVED in sent, "a gapped replay must not replace the saved transcript"
        assert JOURNAL not in sent
        assert "gap" in " | ".join(_recovery_logs(caplog))

    @pytest.mark.asyncio
    async def test_a_healthy_journal_is_still_replayed(self, tmp_path, monkeypatch, caplog) -> None:
        root, provider, ws = _root(tmp_path, monkeypatch)
        caplog.set_level(logging.INFO, logger="wisp.core.runtime")
        await _resume(root, ws, "s-ok", [
            SessionEvent.user_message(0, "build it"),
            SessionEvent.assistant_message(1, JOURNAL),
            SessionEvent.user_message(2, "continue the task"),
        ])
        sent = _history_sent(provider)
        assert JOURNAL in sent and SAVED not in sent
        assert "journal" in " | ".join(_recovery_logs(caplog))


_OPENROUTER_402 = json.dumps({"error": {
    "message": "Prompt tokens limit exceeded: 23791 > 8517. To increase, visit "
               "https://openrouter.ai/workspaces/default/keys/<key> and adjust the key's total limit",
    "code": 402}})


class TestABillingRefusalSaysWhatToDo:
    def _stream(self, status: int, body: str):
        from wisp.providers.openrouter import OpenRouterProvider
        provider = OpenRouterProvider(model="stealth/space-bunny-alpha", api_key="sk-test")
        resp = MagicMock()
        resp.status_code = status
        resp.text = body
        with patch("requests.post", return_value=resp):
            events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "hi"}]))
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1, "floor: exactly one error event"
        return errors[0]

    def test_402_keeps_the_providers_words_and_adds_the_remedy(self) -> None:
        err = self._stream(402, _OPENROUTER_402)
        msg = err["message"]
        assert err["status"] == 402
        assert "Prompt tokens limit exceeded: 23791 > 8517" in msg
        low = msg.lower()
        assert "limit" in low and "credit" in low
        assert "/compact" in msg
        assert "retrying will fail the same way" in low

    def test_other_errors_are_unchanged(self) -> None:
        err = self._stream(500, "upstream exploded")
        assert err["message"] == "API error 500: upstream exploded"
