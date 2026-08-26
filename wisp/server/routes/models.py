"""Models router.

Handles model listing.
"""

import asyncio
import logging
import subprocess

from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException

from wisp.server.deps import verify_api_key, RATE_LIMITER

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/models", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def list_models() -> dict[str, Any]:
    """List available Ollama models (local + cloud)."""
    # Both lookups block (subprocess + requests); run them off the event
    # loop so a slow Ollama can't freeze every other connection.
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                models = [line.split()[0] for line in lines[1:] if line.strip()]
                return {"models": models}
    except Exception:
        pass

    try:
        resp = await asyncio.to_thread(
            requests.get, "http://localhost:11434/api/tags", timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        return {"models": models}
    except Exception as e:
        # SECURITY (audit P2 #38): log the real error server-side, send a
        # generic message to the client to avoid leaking backend details.
        logger.warning("Ollama models lookup failed: %s", e)
        raise HTTPException(status_code=503, detail="Ollama backend unavailable")
