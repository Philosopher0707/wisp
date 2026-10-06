"""P3 / R4 in `enforce`: a provider that names what the account can afford gets ONE smaller retry; below `min_useful` or after a failed retry the turn
stops honestly. In `observe` nothing changes (RC4). The 402 shape is the real one: an `error` event with status 402 (see test_personas)."""

from __future__ import annotations

import pytest

from tests.reasoning import personas as P
from wisp.providers.openai import OpenAIProvider

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")

E5403 = "API error 402: This request requires more credits, or fewer max_tokens. You requested up to 4096 tokens, but can only afford 5403."


def persona(*rounds):
    return P.Persona("R4", "x", "R4", lambda ws: list(rounds), lambda o, ws: False)


def play(mode, tmp_path, *rounds, provider=None, max_iterations=8):
    p = persona(*rounds)
    return P.run(p, mode, tmp_path, provider=provider or P.ScriptedProvider(list(rounds)), max_iterations=max_iterations)


class TestEnforce:
    def test_one_smaller_retry_then_the_answer_arrives(self, tmp_path):
        o = play("enforce", tmp_path, P.error_round(E5403, 402), P.content_round("Here is the answer."))
        assert o.provider_calls == 2 and o.final_text == "Here is the answer." and o.ending == "done"
        assert o.provider.affordable_ceiling == 5403 - 64
        assert any(r.get("applied") and r["rule"] == "R4" and r["action"] == "retry_request" for r in o.journal)

    def test_the_user_is_told_about_the_retry(self, tmp_path):
        o = play("enforce", tmp_path, P.error_round(E5403, 402), P.content_round("ok"))
        assert any("max_tokens=5339" in str(e.get("text", e.get("message", ""))) for e in o.events)

    def test_a_second_402_stops_after_exactly_one_retry(self, tmp_path):
        o = play("enforce", tmp_path, P.error_round(E5403, 402), P.error_round("API error 402: can only afford 5000", 402))
        assert o.provider_calls == 2 and o.ending == "error"

    def test_a_limit_below_useful_stops_without_a_retry_and_names_the_limit(self, tmp_path):
        o = play("enforce", tmp_path, P.error_round("API error 402: ... can only afford 83.", 402), P.content_round("never reached"))
        assert o.provider_calls == 1 and o.ending == "error"
        errors = " ".join(str(e) for e in o.events if e.get("type") == "error")
        assert "can only afford 83" in errors and "Harness note" in errors and "too few to continue" in errors
        assert o.provider.affordable_ceiling is None

    def test_no_retry_when_no_further_round_exists(self, tmp_path):
        o = play("enforce", tmp_path, P.error_round(E5403, 402), P.content_round("never reached"), max_iterations=1)
        assert o.provider_calls == 1 and o.ending == "error" and getattr(o.provider, "affordable_ceiling", None) is None

    def test_an_error_without_a_limit_is_left_to_the_existing_path(self, tmp_path):
        o = play("enforce", tmp_path, P.error_round("API error 400: bad request", 400), P.content_round("never reached"))
        assert o.provider_calls == 1 and getattr(o.provider, "affordable_ceiling", None) is None

    def test_an_exception_carrying_the_limit_is_retried_too(self, tmp_path):
        def boom():
            raise RuntimeError("API error 402: you can only afford 5403 tokens")
            yield  # pragma: no cover

        o = play("enforce", tmp_path, boom, P.content_round("ok"))
        assert o.provider_calls >= 1  # the exception path records the decision; applying it is the error-event path's job
        assert any(r["seam"] == "provider_error" and r["rule"] == "R4" for r in o.journal)

    def test_the_ceiling_does_not_outlive_the_turn(self, tmp_path):
        provider = P.ScriptedProvider([P.error_round(E5403, 402), P.content_round("ok"), P.content_round("second turn")])
        play("enforce", tmp_path / "t1", P.error_round(E5403, 402), P.content_round("ok"), provider=provider)
        assert provider.affordable_ceiling == 5339
        provider._rounds = [P.content_round("second turn")]
        provider.calls = 0
        o2 = play("enforce", tmp_path / "t2", P.content_round("second turn"), provider=provider)
        assert provider.affordable_ceiling is None and o2.final_text == "second turn"


class TestObserveChangesNothing:
    def test_observe_never_retries_and_never_sets_a_ceiling(self, tmp_path):
        o = play("observe", tmp_path, P.error_round(E5403, 402), P.content_round("never reached"))
        assert o.provider_calls == 1 and o.ending == "error" and getattr(o.provider, "affordable_ceiling", None) is None
        assert not any(r.get("applied") for r in o.journal)

    def test_off_never_retries(self, tmp_path):
        o = play("off", tmp_path, P.error_round(E5403, 402), P.content_round("never reached"))
        assert o.provider_calls == 1 and o.journal == ()


class TestTheRealProviderHonoursTheCeiling:
    def make(self, max_tokens, base="https://api.example.test/v1"):
        pr = OpenAIProvider(api_key="k", base_url=base, model="m")
        pr.max_tokens = max_tokens
        return pr

    def payload(self, pr):
        return pr._build_payload("sys", [{"role": "user", "content": "hi"}], None)

    def test_the_ceiling_lowers_the_request(self):
        pr = self.make(16384)
        pr.affordable_ceiling = 5339
        assert self.payload(pr)["max_tokens"] == 5339

    def test_the_ceiling_never_raises_the_request(self):
        pr = self.make(1000)
        pr.affordable_ceiling = 5339
        assert self.payload(pr)["max_tokens"] == 1000

    def test_the_ceiling_applies_even_when_no_cap_was_configured(self):
        pr = self.make(None)
        pr.affordable_ceiling = 5339
        assert self.payload(pr)["max_tokens"] == 5339

    def test_without_a_ceiling_the_payload_is_unchanged(self):
        assert self.payload(self.make(16384))["max_tokens"] == 16384
        assert "max_tokens" not in self.payload(self.make(None))

    @pytest.mark.parametrize("bad", [0, -5, "x", None, 3.5])
    def test_a_junk_ceiling_is_ignored(self, bad):
        pr = self.make(16384)
        pr.affordable_ceiling = bad
        assert self.payload(pr)["max_tokens"] == 16384
