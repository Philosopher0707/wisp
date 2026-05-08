"""Shared runtime protocol types for app-server and TUI surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class JsonRpcRequest:
    """Minimal JSON-RPC 2.0 request envelope."""

    id: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonRpcRequest":
        return cls(
            id=str(data["id"]),
            method=data["method"],
            params=data.get("params", {}),
        )


@dataclass(frozen=True)
class JsonRpcError:
    """JSON-RPC error payload."""

    code: int
    message: str
    data: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


@dataclass(frozen=True)
class JsonRpcResponse:
    """Minimal JSON-RPC 2.0 response envelope."""

    id: str
    result: Optional[dict[str, Any]] = None
    error: Optional[JsonRpcError] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        else:
            payload["result"] = self.result or {}
        return payload


@dataclass(frozen=True)
class AppEvent:
    """Structured event emitted to terminal and remote clients."""

    event: str
    thread_id: str = ""
    run_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppEvent":
        return cls(
            event=data["event"],
            thread_id=data.get("thread_id", ""),
            run_id=data.get("run_id", ""),
            payload=data.get("payload", {}),
        )
