# tests/test_contracts_tool.py
import pytest
from wisp.contracts.tool import ToolRequest, ToolResult, BLOCK_REASONS


def test_request_round_trip():
    r = ToolRequest(tool_call_id="c1", name="read_file",
                    args={"path": "a.py"}, idempotency_key="k1")
    assert ToolRequest.from_dict(r.to_dict()) == r
    assert r.version == 1


def test_result_denied_carries_block_reason():
    res = ToolResult(tool_call_id="c1", status="denied",
                     block_reason="danger", error="rm -rf / blocked")
    assert res.to_dict()["block_reason"] == "danger"
    assert res.block_reason in BLOCK_REASONS


def test_bad_status_rejected():
    with pytest.raises(ValueError):
        ToolResult(tool_call_id="c1", status="maybe")


def test_unknown_field_rejected():
    with pytest.raises(TypeError):
        ToolRequest(tool_call_id="c", name="n", args={}, bogus=1)


def test_from_dict_unknown_fields_rejected():
    with pytest.raises(ValueError, match="unknown Tool"):
        ToolRequest.from_dict({"tool_call_id": "c", "name": "n", "bogus": 1})
    with pytest.raises(ValueError, match="unknown Tool"):
        ToolResult.from_dict({"tool_call_id": "c", "status": "ok", "bogus": 1})
