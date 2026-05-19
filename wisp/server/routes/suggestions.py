"""Suggestions router.

Handles suggestions.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_app_suggestion_watcher = None


def _get_suggestion_watcher():
    global _app_suggestion_watcher
    if _app_suggestion_watcher is None:
        from wisp.suggestion_watcher import SuggestionWatcher
        _app_suggestion_watcher = SuggestionWatcher(str(WORKSPACE_ROOT))
    return _app_suggestion_watcher


@router.get("/api/suggestions", dependencies=[Depends(verify_api_key)])
async def get_suggestions():
    """Return files changed since last poll with diagnostic counts."""
    try:
        from wisp.lsp.manager import get_lsp_manager
        lsp = get_lsp_manager(str(WORKSPACE_ROOT))
        watcher = _get_suggestion_watcher()
        suggestions = await asyncio.to_thread(watcher.get_suggestions, lsp)
        return {
            "suggestions": [
                {
                    "path": s.path,
                    "mtime": s.mtime,
                    "diagnostic_count": s.diagnostic_count,
                    "severities": s.severities,
                }
                for s in suggestions
            ]
        }
    except Exception as e:
        logger.warning("Failed to get suggestions: %s", e)
        return {"suggestions": []}
