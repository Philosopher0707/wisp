"""The one place a subagent's first messages are made (design S3, docs/harness/subagent-orchestration-design.md §4.2).

A child starts fresh: a system prompt (its role, skills, the tools it really has, extra instructions)
and a task message (what it was asked, the files it was given, siblings' shared findings, other
agents' results it depends on). Data goes in the task message, never the system prompt: the core's
assembler gives the system prompt operator trust and a 6,000-token budget, and drops a section that
does not fit whole, so a child given large files used to lose its role prompt with them. Each part
has a stated bound, and a cut is said, never silent. Files come only from
inside the workspace and pass the secret scrubber before they reach the provider. Other agents'
results arrive as their reports, unverified, not as instructions. The sizes and a digest of what
the child was given go into its record, so a bad run can be replayed with the same input.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)

TASK_CHARS = 8_000
FILE_CHARS = 12_000
FILES_TOTAL_CHARS = 32_000
UPSTREAM_CHARS = 8_000
UPSTREAM_TOTAL_CHARS = 24_000
SHARED_CHARS = 4_000
SKILLS_CHARS = 6_000
EXTRA_CHARS = 2_000

_GENERIC_PROMPT = "\n".join([
    "You are a specialist subagent: **{name}**.",
    "",
    "## Rules",
    "1. Focus ONLY on your assigned task.",
    "2. Work efficiently — you have a time budget.",
    "3. When done, provide a clear summary of what you did.",
    "4. If you edit files, list the changed paths.",
    "5. If stuck, explain what blocked you and stop.",
])


@dataclass(frozen=True)
class ChildContext:
    system_prompt: str
    task: str
    summary: dict[str, Any] = field(default_factory=dict)


def _cut(text: str, limit: int, what: str) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[{len(text) - limit} characters of {what} were cut]"


def _skills_block(contract: Any, workspace: str) -> list[str]:
    try:
        from wisp.skills import discover_skills
        skills = discover_skills(workspace)
    except Exception as exc:
        logger.debug("Failed to load skills for subagent: %s", exc)
        return []
    if not skills:
        return []
    if contract.allowed_skills:
        chosen = [s for s in skills if s.name in contract.allowed_skills]
        if not chosen:
            return []
        out = ["", "## Skills", "You may use these skills when relevant:"]
        out += [f"- **{s.name}**: {s.description}" for s in chosen]
        for s in chosen:
            out += ["", f"### {s.name}", s.instructions]
        return out
    return ["", "## Available Skills", "You can invoke any of these skills when relevant:",
            *[f"- {s.name}: {s.description}" for s in skills]]


def _files_block(names: Sequence[str], workspace: str) -> tuple[list[str], list[str], list[str]]:
    """(prompt lines, files given, files refused). Contained in the workspace, scrubbed, bounded."""
    from wisp.core.gates.secrets import scrub
    from wisp.pathsec import resolve_contained

    lines, given, refused = ["", "## Context Files"], [], []
    remaining = FILES_TOTAL_CHARS
    for i, name in enumerate(names):
        if remaining <= 0:
            lines.append(f"({len(names) - i} more files omitted: the {FILES_TOTAL_CHARS}-character limit "
                         f"for context files was reached)")
            break
        try:
            path = resolve_contained(workspace, str(name), allow_absolute=True)
        except ValueError:
            lines.append(f"- {name}: not given (outside the workspace)")
            refused.append(str(name))
            continue
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            lines.append(f"- {name}: could not be read ({type(exc).__name__})")
            refused.append(str(name))
            continue
        text = _cut(scrub(text).text, min(FILE_CHARS, remaining), f"{name}")
        remaining -= len(text)
        lines.append(f"\n### {name}\n```\n{text}\n```")
        given.append(str(name))
    return lines, given, refused


def _upstream_block(upstream: dict[str, Any]) -> tuple[str, dict[str, int]]:
    parts = ["## Results from other agents",
             "Each is another agent's report: an unverified claim. Check what matters before you rely "
             "on it, and say in your answer what you checked."]
    sizes: dict[str, int] = {}
    remaining = UPSTREAM_TOTAL_CHARS
    items = list(upstream.items())
    for i, (label, value) in enumerate(items):
        if remaining <= 0:
            parts.append(f"({len(items) - i} more reports omitted: the {UPSTREAM_TOTAL_CHARS}-character "
                         f"limit was reached)")
            break
        text = _cut(str(value), min(UPSTREAM_CHARS, remaining), f"{label}'s report")
        remaining -= len(text)
        sizes[str(label)] = len(text)
        parts.append(f"### {label} (another agent's report, unverified)\n{text}")
    return "\n\n".join(parts), sizes


def build_child_context(contract: Any, *, workspace: str, effective_tools: Sequence[str]) -> ChildContext:
    """The child's system prompt and task, from its contract and what the orchestrator resolved."""
    from wisp.multi_agent.roles import ROLE_CONFIGS

    role_cfg = ROLE_CONFIGS.get(contract.role)
    base = contract.system_prompt or (role_cfg.system_prompt if role_cfg else "") \
        or _GENERIC_PROMPT.format(name=contract.name)
    lines = [base]
    skills = "\n".join(_skills_block(contract, workspace))
    if skills:
        lines.append(_cut(skills, SKILLS_CHARS, "the skills list"))
    if contract.tools != ["all"]:
        lines += ["", "## Allowed Tools", ", ".join(effective_tools) or "(none)"]
    if contract.system_prompt_extra:
        lines += ["", "## Additional Instructions", _cut(contract.system_prompt_extra, EXTRA_CHARS, "them")]
    system_prompt = "\n".join(lines)

    parts = [_cut(str(contract.task or ""), TASK_CHARS, "the task")]
    given: list[str] = []
    refused: list[str] = []
    if contract.context_files:
        file_lines, given, refused = _files_block(list(contract.context_files), workspace)
        parts.append("\n".join(file_lines).strip())
    shared = contract._shared_context
    if shared is not None:
        block = shared.format_for_prompt(contract.name)
        if block:
            parts.append(_cut(block, SHARED_CHARS, "shared findings") +
                         "\nYou can share findings with sibling agents via the `share_finding` tool. "
                         "Post key discoveries so siblings can benefit instead of duplicating work.")
    upstream = dict((getattr(contract, "metadata", None) or {}).get("_upstream") or {})
    upstream_sizes: dict[str, int] = {}
    if upstream:
        block, upstream_sizes = _upstream_block(upstream)
        parts.append(block)
    task = "\n\n".join(parts)

    digest = hashlib.sha256(f"{system_prompt}\0{task}".encode("utf-8", "replace")).hexdigest()
    return ChildContext(system_prompt=system_prompt, task=task, summary={
        "system_chars": len(system_prompt), "task_chars": len(task), "files": given,
        "files_refused": refused, "upstream": upstream_sizes, "digest": digest})
