"""JSON-RPC router.

Handles JSON-RPC requests.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_app_server = None


def _get_app_server():
    global _app_server
    if _app_server is None:
        from wisp.server import WispAppServer
        _app_server = WispAppServer()
    return _app_server


class JsonRpcRequest(BaseModel):
    jsonrpc: str = Field(default="2.0")
    method: str = Field(..., min_length=1)
    params: dict | list | None = None
    id: str | int | None = None


@router.post("/api/jsonrpc", dependencies=[Depends(verify_api_key)])
async def jsonrpc(req: JsonRpcRequest):
    """JSON-RPC 2.0 endpoint for app-style clients."""
    from wisp.config import WispConfig
    config = WispConfig()
    config.workspace = str(WORKSPACE_ROOT)

    server = _get_app_server()
    # Convert Pydantic model to dict for the handler
    request_dict = req.model_dump()
    response = await server.handle_request(request_dict, config=config)
    return response
