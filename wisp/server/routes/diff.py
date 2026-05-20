"""Diff router.

Handles diff and inline editing.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT
from wisp.server.routes.files import _resolve_path

logger = logging.getLogger(__name__)

router = APIRouter()


class DiffRequest(BaseModel):
    path: str = Field(..., min_length=1)
    new_content: str


class InlineEditRequest(BaseModel):
    path: str = Field(..., description="File path relative to workspace")
    selection: str = Field(..., min_length=1, description="Selected code to replace")
    instruction: str = Field(..., min_length=1, description="Natural language edit instruction")
    model: str | None = None


@router.post("/api/diff", dependencies=[Depends(verify_api_key)])
async def create_diff(req: DiffRequest):
    target = _resolve_path(req.path)
    from wisp.diff import generate_diff_string
    if target.is_file():
        try:
            old_content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
        result = generate_diff_string(old_content, req.new_content)
        return {
            "diff": result.diff,
            "is_new": False,
            "path": req.path,
            "first_changed_line": result.first_changed_line,
        }
    else:
        result = generate_diff_string("", req.new_content)
        return {
            "diff": result.diff,
            "is_new": True,
            "path": req.path,
            "first_changed_line": result.first_changed_line,
        }


@router.post("/api/edit/inline", dependencies=[Depends(verify_api_key)])
async def inline_edit(req: InlineEditRequest, request: Request):
    """Inline edit — replace a selection based on natural language instruction."""
    target = _resolve_path(req.path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        file_content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")

    if req.selection not in file_content:
        raise HTTPException(status_code=400, detail="Selection not found in file")

    edit_prompt = f"""Rewrite the selected code according to the instruction.

## File: {req.path}
```{file_content[:8000]}
```

## Selected code:
```{req.selection}
```

## Instruction:
{req.instruction}

Return ONLY the replacement code for the selection. No explanation, no markdown fences.
"""

    from wisp.entry import run_headless
    result = await run_headless(
        prompt=edit_prompt,
        model=req.model,
        permission_mode="read_only",
        root=request.app.state.root if hasattr(request.app.state, "root") else None,
    )

    replacement = result.get("content", "").strip()
    # Strip markdown fences if present
    if replacement.startswith("```"):
        lines = replacement.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        replacement = "\n".join(lines).strip()

    return {
        "ok": result.get("ok", False),
        "path": req.path,
        "replacement": replacement,
        "prompt": edit_prompt,
    }
