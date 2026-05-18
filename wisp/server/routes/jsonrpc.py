"""JSON-RPC router.

Handles JSON-RPC requests.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/jsonrpc")
async def jsonrpc():
    return {"jsonrpc": "2.0", "result": None}
