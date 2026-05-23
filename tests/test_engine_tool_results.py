"""Tests for tool result normalization in WispAgentCore."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def core():
    from wisp.core.engine import WispAgentCore
    from wisp.infra.security import SecurityPolicy, PermissionMode
    from wisp.infra.extensions import ExtensionHost

    return WispAgentCore(
        provider=MagicMock(),
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
    )


class TestToolResultNormalization:
    """Tool results are normalized to standard schema."""

    def test_string_result(self, core):
        result = core._normalize_tool_result("hello")
        assert result["status"] == "ok"
        assert result["data"] == "hello"

    def test_dict_result(self, core):
        result = core._normalize_tool_result({"key": "value"})
        assert result["status"] == "ok"
        assert result["data"] == {"key": "value"}

    def test_list_result(self, core):
        result = core._normalize_tool_result([1, 2, 3])
        assert result["status"] == "ok"
        assert result["data"] == [1, 2, 3]

    def test_none_result(self, core):
        result = core._normalize_tool_result(None)
        assert result["status"] == "ok"
        assert result["data"] == ""

    def test_bytes_result(self, core):
        result = core._normalize_tool_result(b"hello")
        assert result["status"] == "ok"
        assert result["data"] == "hello"
        assert result["metadata"]["was_bytes"] is True

    def test_path_result(self, core):
        from pathlib import Path
        result = core._normalize_tool_result(Path("/tmp/test"))
        assert result["status"] == "ok"
        assert result["data"] == "/tmp/test"
        assert result["metadata"]["is_path"] is True

    def test_exception_result(self, core):
        result = core._normalize_tool_result(ValueError("bad"))
        assert result["status"] == "error"
        assert "bad" in result["data"]
        assert result["metadata"]["exception_type"] == "ValueError"

    def test_standard_schema_preserved(self, core):
        original = {"status": "ok", "data": "test", "metadata": {"extra": "info"}}
        result = core._normalize_tool_result(original)
        assert result["status"] == "ok"
        assert result["data"] == "test"
        assert result["metadata"] == {"extra": "info"}

    def test_error_tuple(self, core):
        result = core._normalize_tool_result(("error", "something failed"))
        assert result["status"] == "error"
        assert "something failed" in result["data"]

    def test_custom_object(self, core):
        class Custom:
            def __str__(self):
                return "custom-value"
        result = core._normalize_tool_result(Custom())
        assert result["status"] == "ok"
        assert result["data"] == "custom-value"
        assert result["metadata"]["original_type"] == "Custom"


class TestSerializeValue:
    """Value serialization for JSON compatibility."""

    def test_string(self, core):
        assert core._serialize_value("hello") == "hello"

    def test_int(self, core):
        assert core._serialize_value(42) == 42

    def test_path(self, core):
        from pathlib import Path
        assert core._serialize_value(Path("/tmp")) == "/tmp"

    def test_bytes(self, core):
        assert core._serialize_value(b"hello") == "hello"

    def test_dict(self, core):
        from pathlib import Path
        result = core._serialize_value({"path": Path("/tmp")})
        assert result["path"] == "/tmp"

    def test_list(self, core):
        from pathlib import Path
        result = core._serialize_value([Path("/tmp"), 42])
        assert result == ["/tmp", 42]

    def test_nested(self, core):
        from pathlib import Path
        result = core._serialize_value({
            "items": [{"path": Path("/tmp")}],
            "count": 1,
        })
        assert result["items"][0]["path"] == "/tmp"
        assert result["count"] == 1
