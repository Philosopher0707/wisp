"""Session boot context — Turn-0 dynamic seed (GH#22).

KV-cache contract: the static system prompt is built once and cached by
(workspace, mtimes, role-variant); NOTHING here touches it. This module
assembles the dynamic half — project guidelines, git/sandbox posture,
memory bullets, repomap skeleton — as a single first message injected
only when a session has no messages yet (true Turn-0, or post-/clear).

Additive by design: the assembler test-suite pins the system prompt's
current structure, so splitting memory/git/repomap OUT of it would churn
dozens of goldens for no cache gain (they're mtime-cached already). The
new bytes this module adds are guidelines discovery (AGENTS.md/CLAUDE.md
were never loaded) in spec order.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BootContextAssembler",
    "BootPayload",
    "GUIDELINE_CANDIDATES",
    "BUDGET_CHARS",
    "TRUNCATION_NOTE",
    "GIT_TIMEOUT_S",
]

GUIDELINE_CANDIDATES = ("AGENTS.md", "CLAUDE.md", ".wisp/instructions.md")
# 2,000 tokens at the repo's chars_per_token=4 convention.
BUDGET_CHARS = 8000
TRUNCATION_NOTE = "\n\n[... Project instructions truncated at 2,000 token limit ...]"
GIT_TIMEOUT_S = 1.5
MEMORY_BULLETS = 8
REPOMAP_CHARS = 2000


def _est_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


def _clean_cut(text: str, budget: int) -> tuple[str, bool]:
    """Truncate at a sentence/heading boundary under budget.

    Returns (text, was_truncated). Never splits mid-line when avoidable:
    prefers the last blank line, then the last sentence end, then a hard
    cut — always with the truncation note appended.
    """
    if len(text) <= budget:
        return text, False
    window = text[:budget]
    for sep in ("\n\n", ". ", ".\n", "! ", "? ", "\n"):
        idx = window.rfind(sep)
        if idx > budget // 2:
            return window[:idx + len(sep)].rstrip() + TRUNCATION_NOTE, True
    return window.rstrip() + TRUNCATION_NOTE, True


@dataclass
class BootPayload:
    """Assembled Turn-0 seed plus discovery metadata for /doctor."""

    source: str | None  # guideline filename or None
    status: str  # "found" | "none"
    bytes: int = 0
    est_tokens: int = 0
    guidelines: str = ""
    posture: str = ""
    memory_bullets: list[str] = field(default_factory=list)
    repomap_skeleton: str = ""
    text: str = ""  # full seed message body ("" = nothing worth injecting)

    def doctor_status(self) -> dict[str, Any]:
        return {"source": self.source, "status": self.status,
                "bytes": self.bytes, "est_tokens": self.est_tokens}


class BootContextAssembler:
    """Discover-sanitize-assemble session boot context for one workspace."""

    def __init__(self, workspace: str | Path,
                 budget_chars: int = BUDGET_CHARS,
                 git_timeout_s: float = GIT_TIMEOUT_S,
                 memory_limit: int = MEMORY_BULLETS,
                 repomap_chars: int = REPOMAP_CHARS) -> None:
        self.workspace = Path(workspace)
        self.budget_chars = budget_chars
        self.git_timeout_s = git_timeout_s
        self.memory_limit = memory_limit
        self.repomap_chars = repomap_chars

    # ── 1. Guidelines ─────────────────────────────────────────────

    def discover(self) -> tuple[str | None, str]:
        """First existing candidate in preference order; never raises.

        Non-UTF8 bytes decode with replacement, broken symlinks and
        permission errors skip to the next candidate — boot never blocks.
        """
        for name in GUIDELINE_CANDIDATES:
            try:
                candidate = self.workspace / name
                if not candidate.is_file():
                    continue
                raw = candidate.read_bytes()
            except OSError:
                continue
            try:
                return name, raw.decode("utf-8")
            except UnicodeDecodeError:
                return name, raw.decode("utf-8", errors="replace")
        return None, ""

    # ── 2. Posture ────────────────────────────────────────────────

    def _git(self, *args: str) -> str | None:
        """git output, or None on failure. Empty output is VALID (e.g. a
        clean tree yields empty porcelain) — only rc/exceptions are None."""
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.workspace), *args],
                capture_output=True, text=True, timeout=self.git_timeout_s)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip()

    def git_posture(self) -> dict[str, str]:
        """Branch/hash/dirty triple; unknown/non-git fallbacks, never raises."""
        try:
            if self._git("rev-parse", "--git-dir") is None:
                return {"branch": "non-git", "commit": "unknown", "dirty": "unknown"}
            branch = self._git("rev-parse", "--abbrev-ref", "HEAD") or "unborn"
            commit = self._git("rev-parse", "--short", "HEAD") or "unknown"
            porcelain = self._git("status", "--porcelain")
            dirty = "unknown" if porcelain is None else ("dirty" if porcelain else "clean")
            return {"branch": branch, "commit": commit, "dirty": dirty}
        except Exception:
            logger.debug("git posture failed", exc_info=True)
            return {"branch": "unknown", "commit": "unknown", "dirty": "unknown"}

    def sandbox_posture(self) -> str:
        """Confinement boundary without probing the daemon (which blocks).

        shutil.which is instant; DockerSandbox.is_available() shells
        `docker info` (10s) — unacceptable at boot. Daemon presence is a
        runtime concern, not a boot fact.
        """
        import shutil

        try:
            return "docker" if shutil.which("docker") else "host-unconfined"
        except Exception:
            return "unknown"

    # ── 3. Memory ─────────────────────────────────────────────────

    def memory_bullets(self) -> list[str]:
        """Newest-first fact contents, workspace facts before global."""
        try:
            from wisp.memory import load_memory
        except Exception:
            return []
        try:
            data = load_memory() or {}
        except Exception:
            logger.debug("memory load failed at boot", exc_info=True)
            return []
        key = str(self.workspace.resolve()) if self.workspace.exists() else str(self.workspace)
        facts: list[dict] = []
        ws_facts = (data.get("workspace_facts") or {}).get(key, [])
        if isinstance(ws_facts, list):
            facts.extend([f for f in ws_facts if isinstance(f, dict)])
        glob = data.get("global_facts") or []
        if isinstance(glob, list):
            facts.extend([f for f in glob if isinstance(f, dict)])
        facts.sort(key=lambda f: str(f.get("added", "")), reverse=True)
        out = []
        for fact in facts[:max(0, self.memory_limit)]:
            content = str(fact.get("content", "") or "").strip()
            if content:
                out.append(content)
        return out

    # ── 4. RepoMap skeleton ───────────────────────────────────────

    def repomap_skeleton(self) -> str:
        """Classes/signatures-only skeleton, char-capped. Never raises."""
        if self.repomap_chars <= 0:
            return ""
        try:
            from wisp.repo_map import RepoMap
        except Exception:
            return ""
        try:
            rm = RepoMap(self.workspace)
            entries = rm.build(use_cache=True, fast_mode=True) or []
        except Exception:
            logger.debug("repomap build failed at boot", exc_info=True)
            return ""
        lines = []
        for entry in entries:
            name = str(getattr(entry, "name", "") or "")
            kind = str(getattr(entry, "kind", "") or "")
            path = str(getattr(entry, "path", "") or "")
            sig = str(getattr(entry, "signature", "") or "")
            if not name:
                continue
            line = f"{kind} {name}".strip()
            if sig and sig != name:
                line += f" {sig}"
            if path:
                line += f"  ({path})"
            lines.append(line)
            if sum(len(x) for x in lines) > self.repomap_chars:
                break
        text = "\n".join(lines)
        return text[:self.repomap_chars]

    # ── Assembly ──────────────────────────────────────────────────

    def assemble(self) -> BootPayload:
        """Build the Turn-0 payload in spec section order."""
        source, guidelines = self.discover()
        if guidelines and len(guidelines) > self.budget_chars:
            guidelines, _ = _clean_cut(guidelines, self.budget_chars)
        posture = self.git_posture()
        sandbox = self.sandbox_posture()
        bullets = self.memory_bullets()
        skeleton = self.repomap_skeleton()

        sections: list[str] = []
        if guidelines:
            sections.append(f"## Project guidelines ({source})\n{guidelines}")
        posture_text = (
            "## Workspace posture\n"
            f"- root: {self.workspace}\n"
            f"- git: {posture['branch']} @ {posture['commit']} [{posture['dirty']}]\n"
            f"- sandbox: {sandbox}")
        sections.append(posture_text)
        if bullets:
            sections.append("## Active memory\n" + "\n".join(f"- {b}" for b in bullets))
        if skeleton:
            sections.append("## Repo symbols (skeleton)\n" + skeleton)

        # Inject only when something beyond bare posture exists: guidelines,
        # memory, or symbols. A posture-only frame burns tokens every
        # session for near-zero signal (git state already rides the prompt).
        substantive = bool(guidelines or bullets or skeleton)
        text = ""
        if substantive:
            text = ("[Session boot context — workspace reality for this session. "
                    "The static instructions above still apply.]\n\n" + "\n\n".join(sections))
        raw_bytes = len(guidelines.encode("utf-8", "replace")) if guidelines else 0
        return BootPayload(
            source=source, status="found" if source else "none",
            bytes=raw_bytes, est_tokens=_est_tokens(guidelines),
            guidelines=guidelines, posture=posture_text,
            memory_bullets=bullets, repomap_skeleton=skeleton, text=text)

    def seed_message(self) -> dict[str, str] | None:
        """Turn-0 user-role message, or None when nothing warrants one."""
        payload = self.assemble()
        if not payload.text:
            return None
        return {"role": "user", "content": payload.text}
