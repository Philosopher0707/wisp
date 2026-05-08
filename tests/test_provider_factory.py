"""Tests for provider factory and core provider wiring."""

from unittest.mock import patch

from wisp.config import WispConfig
from wisp.core.agent import WispAgentCore
from wisp.providers import get_provider
from wisp.providers.ollama import OllamaProvider


class FakeProvider:
    def __init__(self, config):
        self.config = config
        self.model = config.model
        self.stream_response = None

    def check_health(self):
        return True

    def list_models(self):
        return [{"name": self.model}]

    def get_context_length(self):
        return 128000

    def generate(self, system_prompt, messages, tools=None):
        return {"message": {"content": "ok"}}

    def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
        return iter(())


def test_get_provider_returns_ollama_provider():
    cfg = WispConfig()
    provider = get_provider(cfg)
    assert isinstance(provider, OllamaProvider)


def test_core_exposes_provider_and_client_alias():
    cfg = WispConfig()
    cfg.workspace = "/tmp"
    with patch("wisp.core.agent.get_provider", return_value=FakeProvider(cfg)):
        core = WispAgentCore(config=cfg)
    assert core.provider is core.client
    assert core.provider.model == cfg.model
