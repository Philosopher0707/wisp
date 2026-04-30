"""Tests for config.py — defaults, env var override, config file loading."""

import os
import pytest
from wisp.config import WispConfig, get_setting, load_config, save_config


class TestGetSetting:

    def test_uses_default_when_nothing_set(self):
        assert get_setting("nonexistent", "default_val") == "default_val"

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("WISP_MODEL", "env-model")
        assert get_setting("model", "default") == "env-model"

    def test_env_var_takes_precedence(self, monkeypatch, tmp_path):
        import wisp.config as cfg_mod
        config_file = tmp_path / "config.json"
        config_file.write_text('{"model": "file-model"}')

        monkeypatch.setattr(cfg_mod, "get_config_path", lambda: config_file)
        monkeypatch.setenv("WISP_MODEL", "env-model")
        assert get_setting("model", "default") == "env-model"


class TestWispConfig:

    def test_defaults_are_sane(self):
        cfg = WispConfig()
        assert cfg.ollama_url == "http://localhost:11434"
        assert cfg.temperature == 0.2
        assert cfg.max_tokens == 16384
        assert cfg.max_context_tokens == 128000
        assert cfg.chars_per_token == 4
        assert cfg.auto_approve is True
        assert cfg.show_thinking is False

    def test_auto_approve_respects_env(self, monkeypatch):
        monkeypatch.setenv("WISP_AUTO_APPROVE", "false")
        assert WispConfig().auto_approve is False

    def test_show_thinking_respects_env(self, monkeypatch):
        monkeypatch.setenv("WISP_SHOW_THINKING", "true")
        assert WispConfig().show_thinking is True

    def test_model_from_env(self, monkeypatch):
        monkeypatch.setenv("WISP_MODEL", "my-custom-model")
        assert WispConfig().model == "my-custom-model"

    def test_ollama_url_from_env(self, monkeypatch):
        monkeypatch.setenv("WISP_OLLAMA_URL", "http://other:11434")
        assert WispConfig().ollama_url == "http://other:11434"


class TestConfigFile:

    def test_save_and_load(self, tmp_path, monkeypatch):
        import wisp.config as cfg_mod
        cfg_path = tmp_path / "config.json"
        monkeypatch.setattr(cfg_mod, "get_config_path", lambda: cfg_path)
        save_config({"model": "saved-model"})
        loaded = load_config()
        assert loaded["model"] == "saved-model"
