"""Protocol-first app-server methods for Wisp clients."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from wisp.config import WispConfig
from wisp.runtime_protocol import JsonRpcError, JsonRpcRequest, JsonRpcResponse
from wisp.supervisor import WispSupervisor


class WispAppServer:
    """Minimal request handler for app-style runtime methods."""

    def __init__(self, supervisor: WispSupervisor | None = None):
        self.supervisor = supervisor or WispSupervisor()

    async def handle_request(
        self,
        request: JsonRpcRequest,
        config: WispConfig | None = None,
    ) -> JsonRpcResponse:
        """Handle one JSON-RPC request against the runtime."""
        try:
            if request.method == "threads.list":
                return JsonRpcResponse(
                    id=request.id,
                    result={"threads": [self._serialize(item) for item in self.supervisor.list_threads()]},
                )

            if request.method == "threads.create":
                workspace = self._require_param(request, "workspace")
                title = request.params.get("title")
                thread = self.supervisor.create_thread(workspace=workspace, title=title)
                return JsonRpcResponse(id=request.id, result={"thread": self._serialize(thread)})

            if request.method == "runs.execute":
                prompt = self._require_param(request, "prompt")
                effective_config = config or WispConfig()
                if "workspace" in request.params:
                    effective_config = effective_config.replace(workspace=request.params["workspace"])
                if "model" in request.params:
                    effective_config = effective_config.replace(model=request.params["model"])

                thread, run, events = await self.supervisor.execute_prompt(
                    effective_config,
                    prompt,
                    thread_id=request.params.get("thread_id"),
                    title=request.params.get("title"),
                )
                return JsonRpcResponse(
                    id=request.id,
                    result={
                        "thread": self._serialize(thread),
                        "run": self._serialize(run),
                        "events": [self._serialize(event) for event in events],
                    },
                )

            if request.method == "runs.events":
                run_id = self._require_param(request, "run_id")
                events = self.supervisor.read_run_events(run_id)
                return JsonRpcResponse(
                    id=request.id,
                    result={"events": [self._serialize(event) for event in events]},
                )

            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=-32601, message=f"Method not found: {request.method}"),
            )
        except ValueError as exc:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=-32602, message=str(exc)),
            )

    def _require_param(self, request: JsonRpcRequest, name: str):
        value = request.params.get(name)
        if value in (None, ""):
            raise ValueError(f"Missing required param: {name}")
        return value

    def _serialize(self, value):
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return value.to_dict()
        if is_dataclass(value):
            return asdict(value)
        return value
