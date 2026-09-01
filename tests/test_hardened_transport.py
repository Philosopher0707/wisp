"""Tests for wisp.core.transport module."""
from __future__ import annotations

import socket
import unittest.mock
from unittest.mock import MagicMock, patch

import pytest


class TestHttpxStreamingPost:
    def test_hardened_post_httpx_uses_stream_context_manager(self):
        pytest.importorskip("httpx")
        import httpx
        from wisp.core.transport import hardened_post
        
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__class__ = httpx.Client 
        
        mock_context_manager = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_context_manager.__enter__.return_value = mock_response
        mock_client.stream.return_value = mock_context_manager
        
        with patch('time.sleep'):
            res = hardened_post(mock_client, "http://test", stream=True)
            
        mock_client.stream.assert_called_once_with(
            "POST", "http://test", timeout=unittest.mock.ANY
        )
        assert res == mock_response

    def test_hardened_post_requests_uses_stream_kwarg(self):
        pytest.importorskip("requests")
        import requests
        from wisp.core.transport import hardened_post
        
        mock_session = MagicMock(spec=requests.Session)
        mock_session.__class__ = requests.Session
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response
        
        with patch('time.sleep'):
            res = hardened_post(mock_session, "http://test", stream=True)
            
        mock_session.post.assert_called_once_with(
            "http://test", stream=True, timeout=(15.0, 120.0)
        )
        assert res == mock_response


class TestConnectionClosureOnRetry:
    def test_hardened_post_closes_response_on_429_retry(self):
        pytest.importorskip("requests")
        import requests
        from wisp.core.transport import hardened_post
        
        mock_session = MagicMock(spec=requests.Session)
        mock_session.__class__ = requests.Session
        
        resp_429 = MagicMock()
        resp_429.status_code = 429
        
        resp_200 = MagicMock()
        resp_200.status_code = 200
        
        mock_session.post.side_effect = [resp_429, resp_200]
        
        with patch('time.sleep'):
            res = hardened_post(mock_session, "http://test")
            
        assert mock_session.post.call_count == 2
        resp_429.close.assert_called_once()
        assert res == resp_200

    def test_hardened_post_closes_response_on_502_retry(self):
        pytest.importorskip("requests")
        import requests
        from wisp.core.transport import hardened_post
        
        mock_session = MagicMock(spec=requests.Session)
        mock_session.__class__ = requests.Session
        
        resp_502 = MagicMock()
        resp_502.status_code = 502
        
        resp_200 = MagicMock()
        resp_200.status_code = 200
        
        mock_session.post.side_effect = [resp_502, resp_200]
        
        with patch('time.sleep'):
            res = hardened_post(mock_session, "http://test")
            
        assert mock_session.post.call_count == 2
        resp_502.close.assert_called_once()
        assert res == resp_200

    def test_hardened_get_closes_response_on_429_retry(self):
        pytest.importorskip("requests")
        import requests
        from wisp.core.transport import hardened_get
        
        mock_session = MagicMock(spec=requests.Session)
        mock_session.__class__ = requests.Session
        
        resp_429 = MagicMock()
        resp_429.status_code = 429
        
        resp_200 = MagicMock()
        resp_200.status_code = 200
        
        mock_session.get.side_effect = [resp_429, resp_200]
        
        with patch('time.sleep'):
            res = hardened_get(mock_session, "http://test")
            
        assert mock_session.get.call_count == 2
        resp_429.close.assert_called_once()
        assert res == resp_200


class TestTimeoutPropagation:
    def test_requests_session_gets_tuple_timeout(self):
        pytest.importorskip("requests")
        import requests
        from wisp.core.transport import HARDENED_TIMEOUT, hardened_post
        
        mock_session = MagicMock(spec=requests.Session)
        mock_session.__class__ = requests.Session
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response
        
        with patch('time.sleep'):
            hardened_post(mock_session, "http://test", timeout=HARDENED_TIMEOUT)
            
        _, kwargs = mock_session.post.call_args
        assert kwargs["timeout"] == (15.0, 120.0)

    def test_httpx_client_gets_httpx_timeout(self):
        pytest.importorskip("httpx")
        import httpx
        from wisp.core.transport import HARDENED_TIMEOUT, hardened_post
        
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__class__ = httpx.Client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
        
        with patch('time.sleep'):
            hardened_post(mock_client, "http://test", timeout=HARDENED_TIMEOUT)
            
        _, kwargs = mock_client.post.call_args
        assert isinstance(kwargs["timeout"], httpx.Timeout)
        assert kwargs["timeout"].connect == 15.0
        assert kwargs["timeout"].write == 60.0
        assert kwargs["timeout"].read == 120.0
        assert kwargs["timeout"].pool == 30.0


class TestTransientErrorFixes:
    def test_aiohttp_typo_fixed(self):
        from wisp.core.transport import is_transient_error
        err = Exception("aiohttp.writetimeout: write timed out")
        assert is_transient_error(err) is True

    def test_aionhttp_typo_no_longer_matches(self):
        from wisp.core.transport import is_transient_error
        err = Exception("aionhttp.writetimeout: bad")
        assert is_transient_error(err) is False

    def test_no_false_positive_for_value_error(self):
        from wisp.core.transport import is_transient_error
        err = ValueError("not transient")
        assert is_transient_error(err) is False

    def test_no_false_positive_for_key_error(self):
        from wisp.core.transport import is_transient_error
        err = KeyError("missing")
        assert is_transient_error(err) is False


class TestKeepAliveAdapter:
    def test_keepalive_adapter_creates_adapter(self):
        pytest.importorskip("requests")
        from requests.adapters import HTTPAdapter
        from wisp.core.transport import _KeepAliveAdapter
        
        adapter = _KeepAliveAdapter(
            socket_options=[(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)],
            pool_connections=5,
            pool_maxsize=5
        )
        assert isinstance(adapter, HTTPAdapter)

    def test_safe_socket_options_filters_bad_options(self):
        from wisp.core.transport import _safe_socket_options
        
        options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1), (999, 999, 1)]
        safe_opts = _safe_socket_options(options)
        
        assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in safe_opts
        assert (999, 999, 1) not in safe_opts


class TestHttpxTransportLimits:
    def test_httpx_client_transport_has_limits(self):
        pytest.importorskip("httpx")
        from wisp.core.transport import get_hardened_httpx_client
        
        client = get_hardened_httpx_client()
        assert client is not None
        assert client._transport is not None


class TestAsyncSleep:
    @pytest.mark.asyncio
    async def test_async_sleep_does_not_block(self):
        from wisp.core.transport import _async_sleep
        import asyncio
        
        async def run_sleep():
            await _async_sleep(0.01)
            
        await asyncio.wait_for(run_sleep(), timeout=0.5)
