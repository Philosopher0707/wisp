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

# Security: strict allowlist for git refs, branch names, and commit SHAs
# Refs must be alphanumeric plus safe delimiters. Reject any shell metacharacters.
_SAFE_GIT_REF_RE = re.compile(r"^[a-zA-Z0-9._@\-/:]+$")
_MAX_GIT_REF_LEN = 128


def _validate_git_ref(ref: str, field_name: str) -> str:
    """Validate a git ref/branch name before passing to git commands.

    Raises ValueError if the ref contains unsafe characters or is too long.
    """
    if not ref or not isinstance(ref, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(ref) > _MAX_GIT_REF_LEN:
        raise ValueError(f"{field_name} too long (max {_MAX_GIT_REF_LEN})")
    ref = ref.strip()
    # Reject refs that look like shell injection attempts, option switches, or pipes
    unsafe = {"--", "|", "&", ";", "$(", "`", "$", "\n", "\r", "\x00"}
    for bad in unsafe:
        if bad in ref:
            raise ValueError(f"{field_name} contains unsafe characters")
    # Reject absolute paths (could be used to escape the repo)
    if ref.startswith("/"):
        raise ValueError(f"{field_name} cannot be an absolute path")
    if not _SAFE_GIT_REF_RE.match(ref):
        raise ValueError(f"{field_name} contains invalid characters")
    return ref


def _git_ref_exists(ref: str, cwd: str) -> bool:
    """Verify a ref resolves to an actual object in the git repo."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception:
        return False


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

    # ── Security: sanitize all git refs before passing to subprocess ──
    try:
        base = _validate_git_ref(req.base_branch, "base_branch")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    head: str | None = None
    if req.pr_number is not None:
        head = f"pull/{req.pr_number}/head"
    elif req.head_branch:
        try:
            head = _validate_git_ref(req.head_branch, "head_branch")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    if not head:
        raise HTTPException(status_code=400, detail="Either pr_number or head_branch is required")

    # Verify refs exist before running diff
    for ref in (base, head):
        if not _git_ref_exists(ref, str(WORKSPACE_ROOT)):
            raise HTTPException(status_code=400, detail=f"Git ref '{ref}' not found")

    try:
        proc = subprocess.run(
            ["git", "diff", "--", f"{base}...{head}"],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
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
    except Exception:
        logger.exception("PR review failed")
        raise HTTPException(status_code=500, detail="Review failed")

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

    # Validate target before running git commands
    if req.target not in ("uncommitted", "staged"):
        try:
            target_desc = _validate_git_ref(req.target, "target")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    try:
        if req.target == "uncommitted":
            proc = subprocess.run(
                ["git", "diff", "--"],
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
            if not _git_ref_exists(target_desc, str(WORKSPACE_ROOT)):
                raise HTTPException(status_code=400, detail=f"Git ref '{target_desc}' not found")
            proc = subprocess.run(
                ["git", "diff", "--", f"{target_desc}^!"],
                cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
            )
            diff_text = proc.stdout
            if not diff_text.strip():
                proc = subprocess.run(
                    ["git", "show", "--", target_desc],
                    cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
                )
                diff_text = proc.stdout

        if not diff_text.strip():
            return {"summary": "No changes to review.", "issues": [], "files_reviewed": []}

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="git command timed out")
    except Exception:
        logger.exception("Diff review failed")
        raise HTTPException(status_code=500, detail="Review failed")

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
    except Exception:
        logger.exception("Best-of-N review failed")
        raise HTTPException(status_code=500, detail="Review failed")

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
        except Exception:
            logger.exception("Review model %s failed", model)
            return {"model": model, "ok": False, "error": "Review failed"}

    models_to_use = req.models[:req.n]
    tasks = [_review_with_model(m) for m in models_to_use]
    reviews = await asyncio.gather(*tasks)

    return {
        "diff": diff_text[:5000],
        "files_reviewed": files_reviewed,
        "models_used": models_to_use,
        "reviews": reviews,
    }
