"""TDD for ProviderFactory integration into CompositionRoot.

Tests that CompositionRoot creates real providers via ProviderFactory.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestProviderFactoryIntegration:
    """CompositionRoot uses ProviderFactory to create providers."""

    def test_composition_root_uses_provider_factory(self):
        from wisp.composition import CompositionRoot
        from wisp.providers.factory import ProviderFactory

        config = MagicMock()
        config.db_path = "/tmp/test.db"
        config.permission_mode = "full"
        config.provider = "ollama"
        config.ollama_url = "http://localhost:11434"
        config.model = "qwen2.5-coder"
        config.workspace = "."

        with patch.object(ProviderFactory, "from_config") as mock_from_config:
            mock_provider = MagicMock()
            mock_from_config.return_value = mock_provider
            root = CompositionRoot(config)
            # The _create_core method should use the factory
            core = root._create_core()
            assert core is not None

    def test_create_core_uses_factory_when_provider_set(self):
        from wisp.composition import CompositionRoot
        from wisp.providers.factory import ProviderFactory

        config = MagicMock()
        config.db_path = "/tmp/test.db"
        config.permission_mode = "full"
        config.provider = "ollama"
        config.ollama_url = "http://localhost:11434"
        config.model = "qwen2.5-coder"
        config.workspace = "."

        root = CompositionRoot(config)
        with patch.object(ProviderFactory, "from_config") as mock_from_config:
            mock_provider = MagicMock()
            mock_from_config.return_value = mock_provider

            core = root._create_core()
            mock_from_config.assert_called_once_with(config)
            assert core.provider == mock_provider

    def test_create_core_falls_back_to_null_provider(self):
        from wisp.composition import CompositionRoot

        config = MagicMock()
        config.db_path = "/tmp/test.db"
        config.permission_mode = "full"
        config.provider = None
        config.workspace = "."

        root = CompositionRoot(config)
        core = root._create_core()
        assert core is not None
        # Should use NullProvider when no provider configured
        assert core.provider is not None
