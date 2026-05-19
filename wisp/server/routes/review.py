"""Review router.

Handles code review operations.
"""

import asyncio
import json
import logging
import re
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class PRReviewRequest(BaseModel):
    base_branch: str = Field(default="main")
    head_branch: str | None = None
    pr_number: int | None = None
    model: str | None = None


class DiffReviewRequest(BaseModel):
    target: str = Field(default="uncommitted", description="uncommitted | staged | <commit_sha>")


class BestOfNRequest(BaseModel):
    n: int = Field(default=3, ge=2, le=5)
    models: list[str] = Field(default=["claude-sonnet-4-6", "claude-opus-4-7"])
    prompt: str | None = None


def _extract_files_from_diff(diff_text: str) -> list[str]:
    """Extract unique file paths from a git diff output."""
    files: list[str] = []
    for line in diff_text.split("\n"):
        if line.startswith("+++ ") and line != "+++ /dev/null":
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path and path not in files:
                files.append(path)
    return files


def _extract_json(text: str) -> str | None:
    """Extract a JSON object from text that may contain surrounding content."""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        return fenced.group(1).strip()
    brace_start = text.find("{")
    if brace_start == -1:
        return None
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]
    return None


async def _run_agent_headless(prompt: str, model: str | None = None, permission_mode: str = "read_only", root=None) -> dict:
    """Run agent headlessly and return result."""
    from wisp.entry import run_headless
    return await run_headless(
        prompt=prompt,
        model=model,
        permission_mode=permission_mode,
        root=root,
    )


@router.post("/api/review/pr", dependencies=[Depends(verify_api_key)])
async def review_pr(req: PRReviewRequest, request: Request):
    """Review a PR by diffing base vs head and running the agent."""
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository in workspace")

    base = req.base_branch
    head = req.head_branch

    if req.pr_number is not None and not head:
        head = f"pull/{req.pr_number}/head"
    elif not head:
        raise HTTPException(status_code=400, detail="Either pr_number or head_branch is required")

    try:
        proc = subprocess.run(
            ["git", "diff", f"{base}...{head}"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"git diff failed: {proc.stderr}")
        diff_text = proc.stdout
        if not diff_text.strip():
            return {"summary": "No changes to review.", "issues": [], "approval": "approve", "files_reviewed": []}
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="git diff timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    files_reviewed = _extract_files_from_diff(diff_text)

    review_prompt = f"""Review the following code diff. Find bugs, security issues, style problems,
and suggest improvements. Be concise and actionable.

## Git diff ({base}...{head})
```diff
{diff_text[:15000]}
```

## Instructions
1. Identify critical bugs (logic errors, data corruption, security vulnerabilities)
2. Identify warnings (code smells, performance issues, missing error handling)
3. Identify informational notes (style improvements, better patterns)
4. Provide a final verdict: approve, request_changes, or comment

## Response format
Return your review as JSON:
```json
{{
  "summary": "1-2 sentence overview",
  "issues": [
    {{"severity": "critical|warning|info", "file": "path", "line": int, "message": "...", "suggestion": "..."}}
  ],
  "approval": "approve|request_changes|comment",
  "files_reviewed": ["path1", "path2"]
}}
```
"""

    result = await _run_agent_headless(
        prompt=review_prompt,
        model=req.model,
        permission_mode="read_only",
        root=request.app.state.root if hasattr(request.app.state, "root") else None,
    )

    try:
        content = result.get("content", "")
        json_match = _extract_json(content)
        if json_match:
            parsed = json.loads(json_match)
            result["review"] = parsed
    except Exception:
        logger.warning("Could not parse structured review from agent output")

    result["files_reviewed"] = files_reviewed
    return result


@router.post("/api/review/diff", dependencies=[Depends(verify_api_key)])
async def review_diff(req: DiffReviewRequest, request: Request):
    """Review uncommitted changes, staged changes, or a specific commit diff."""
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository in workspace")

    diff_text = ""
    target_desc = req.target

    try:
        if req.target == "uncommitted":
            proc = subprocess.run(
                ["git", "diff"],
                cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
            )
            diff_text = proc.stdout
        elif req.target == "staged":
            proc = subprocess.run(
                ["git", "diff", "--staged"],
                cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
            )
            diff_text = proc.stdout
        elif req.target:
            proc = subprocess.run(
                ["git", "diff", f"{req.target}^!"],
                cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
            )
            diff_text = proc.stdout
            if not diff_text.strip():
                proc = subprocess.run(
                    ["git", "show", req.target],
                    cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
                )
                diff_text = proc.stdout

        if not diff_text.strip():
            return {"summary": "No changes to review.", "issues": [], "files_reviewed": []}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="git command timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    files_reviewed = _extract_files_from_diff(diff_text)

    review_prompt = f"""Review the following code diff. Find bugs, security issues, style problems,
and suggest improvements. Be concise and actionable.

## Diff ({target_desc})
```diff
{diff_text[:15000]}
```

## Response format
Return your review as JSON:
```json
{{
  "summary": "1-2 sentence overview",
  "issues": [
    {{"severity": "critical|warning|info", "file": "path", "line": int, "message": "...", "suggestion": "..."}}
  ],
  "approval": "approve|request_changes|comment"
}}
```
"""

    result = await _run_agent_headless(
        prompt=review_prompt,
        permission_mode="read_only",
        root=request.app.state.root if hasattr(request.app.state, "root") else None,
    )

    try:
        content = result.get("content", "")
        json_match = _extract_json(content)
        if json_match:
            parsed = json.loads(json_match)
            result["review"] = parsed
    except Exception:
        logger.warning("Could not parse structured review from agent output")

    result["files_reviewed"] = files_reviewed
    return result


@router.post("/api/review/best-of-n", dependencies=[Depends(verify_api_key)])
async def review_best_of_n(req: BestOfNRequest, request: Request):
    """Run N parallel reviews with different models and compare results."""
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository in workspace")

    try:
        proc = subprocess.run(
            ["git", "diff"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
        )
        diff_text = proc.stdout
        if not diff_text.strip():
            return {"diff": "", "reviews": [], "message": "No changes to review."}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="git diff timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    files_reviewed = _extract_files_from_diff(diff_text)

    review_prompt = req.prompt or f"""Review the following code diff. Find bugs, security issues, style problems,
and suggest improvements.

```diff
{diff_text[:10000]}
```

Be concise and return a JSON object with: summary, issues (list of {{severity, file, line, message, suggestion}}), and approval.
"""

    root = request.app.state.root if hasattr(request.app.state, "root") else None

    async def _review_with_model(model: str) -> dict:
        try:
            result = await _run_agent_headless(
                prompt=review_prompt,
                model=model,
                permission_mode="read_only",
                root=root,
            )
            content = result.get("content", "")
            json_match = _extract_json(content)
            if json_match:
                try:
                    result["review"] = json.loads(json_match)
                except json.JSONDecodeError:
                    result["review"] = {"raw_content": content[:2000]}
            else:
                result["review"] = {"raw_content": content[:2000]}
            return result
        except Exception as e:
            return {"model": model, "ok": False, "error": str(e)}

    models_to_use = req.models[:req.n]
    tasks = [_review_with_model(m) for m in models_to_use]
    reviews = await asyncio.gather(*tasks)

    return {
        "diff": diff_text[:5000],
        "files_reviewed": files_reviewed,
        "models_used": models_to_use,
        "reviews": reviews,
    }
