"""Tests for readline Tab completion (wisp/repl/completion.py).

The completer state machine is exercised via SlashCompleter.complete_matches,
which is readline-independent; real readline binding is a no-op off-tty and is
only smoke-tested here (never expected to succeed under pytest).
"""

from unittest.mock import patch

from wisp.repl.completion import (
    SlashCompleter,
    command_completions,
    install_readline_completion,
    provider_completions,
    model_completions,
)


class TestCommandCompletions:
    def test_prefix_matches_names_and_aliases(self):
        names = command_completions("mo")
        assert "/model" in names
        assert "/help" not in names

    def test_empty_prefix_lists_everything(self):
        names = command_completions("")
        assert "/help" in names
        assert "/exit" in names

    def test_unknown_prefix_empty(self):
        assert command_completions("zzz") == []


class TestProviderCompletions:
    def test_lists_known_providers(self):
        assert "ollama" in provider_completions("")
        assert "openai" in provider_completions("o")

    def test_no_result_for_bogus(self):
        assert provider_completions("zzz") == []


class TestModelCompletions:
    @patch("wisp.repl.completion._safe_models")
    def test_compound_provider_model(self, safe_models):
        safe_models.return_value = ["llama3", "deepseek-v4"]
        out = model_completions("nvidia/ll")
        assert out == ["nvidia/llama3"]

    @patch("wisp.repl.completion._safe_models")
    def test_plain_prefix_without_slash(self, safe_models):
        safe_models.side_effect = (
            lambda prov, prefix="": [m for m in ["ollama-coder", "llama3"]
                                     if m.startswith(prefix)]
        )
        out = model_completions("o")
        # Providers whose name starts with "o" plus matching model names
        assert "ollama/" in out
        assert "ollama-coder" in out

    @patch("wisp.repl.completion._safe_models")
    def test_empty_prefix_offers_providers_and_some_models(self, safe_models):
        safe_models.return_value = ["llama3"]
        out = model_completions("")
        assert "openai/" in out
        assert "llama3" in out


class TestSlashCompleterStateMachine:
    def _completer(self):
        return SlashCompleter()

    def test_completes_command_word(self):
        c = self._completer()
        assert "/help" in c.complete_matches("/he")

    def test_non_command_text_returns_empty(self):
        c = self._completer()
        assert c.complete_matches("just some words") == []

    def test_provider_argument_completion(self):
        c = self._completer()
        matches = c.complete_matches("/provider oll")
        assert "ollama" in matches

    def test_model_argument_completion(self):
        c = self._completer()
        matches = c.complete_matches("/model ")
        assert any("/" in m for m in matches)  # provider/model compound or bare model

    def test_readline_protocol_state(self):
        c = self._completer()
        first = c.complete("/help", 0)
        assert first == "/help"
        # state advances through cached matches
        matches = c._matches
        assert len(matches) >= 1
        assert c.complete("/help", len(matches)) is None

    def test_unknown_command_no_argument_source(self):
        c = self._completer()
        assert c.complete_matches("/nonexistent a") == []

    def test_custom_completer_table_wins(self):
        table = {"provider": lambda prefix: ["custom-prov"]}
        c = SlashCompleter(table)
        assert c.complete_matches("/provider cust") == ["custom-prov"]


class TestInstallReadline:
    def test_noop_when_stdin_not_tty(self):
        assert install_readline_completion() is False