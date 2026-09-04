"""Phase 2.1 RED tests — ProviderTransport seam + owned session registry.

Target: providers depend on a Protocol seam, sessions have explicit
ownership, CompositionRoot closes what it owns. Backward compat:
existing factories (get_hardened_session/hardened_post) keep working.
"""

from __future__ import annotations


def test_transport_seam_module_exists_with_protocol():
    from wisp.core import transport_seam as seam

    assert hasattr(seam, "ProviderTransport")
    assert hasattr(seam, "SessionRegistry")
    # Protocol must declare post/get/close members.
    names = dir(seam.ProviderTransport)
    assert "post" in names and "get" in names and "close" in names


def test_session_registry_close_all_closes_each_once():
    from wisp.core.transport_seam import SessionRegistry

    closed = []

    class _Fake:
        def close(self):
            closed.append(self)

    reg = SessionRegistry()
    a, b = _Fake(), _Fake()
    reg.track(a)
    reg.track(b)
    reg.track(a)  # duplicate track must not double-close
    reg.close_all()
    assert closed.count(a) == 1 and closed.count(b) == 1
    # Second close_all is a safe no-op.
    reg.close_all()
    assert len(closed) == 2


def test_ollama_client_does_not_close_injected_session():
    from unittest.mock import MagicMock

    from wisp.ollama_client import OllamaClient

    cfg = MagicMock()
    cfg.ollama_url = "http://localhost:11434/"
    cfg.model = "m"
    cfg.temperature = 0.2
    cfg.max_tokens = 100
    injected = MagicMock()
    client = OllamaClient(cfg, session=injected)
    client.close()
    injected.close.assert_not_called()


def test_ollama_client_closes_owned_session():
    from unittest.mock import MagicMock, patch

    from wisp.ollama_client import OllamaClient

    cfg = MagicMock()
    cfg.ollama_url = "http://localhost:11434/"
    cfg.model = "m"
    cfg.temperature = 0.2
    cfg.max_tokens = 100
    owned = MagicMock()
    with patch("wisp.core.transport.get_hardened_session", return_value=owned):
        client = OllamaClient(cfg)
    client.close()
    owned.close.assert_called_once_with()


def test_composition_root_shutdown_closes_http_registry():
    import inspect

    import wisp.composition as comp

    src = inspect.getsource(comp.CompositionRoot.shutdown)
    assert "http" in src.lower() or "session_registry" in src.lower() or "close_all" in src
