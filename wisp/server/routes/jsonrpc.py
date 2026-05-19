"""JSON-RPC router.

Handles JSON-RPC requests.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.post("/api/jsonrpc", dependencies=[Depends(verify_api_key)])
async def jsonrpc():
    return {"jsonrpc": "2.0", "result": None}
