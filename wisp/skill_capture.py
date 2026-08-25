"""Skill capture — turn demonstrated workflows into reusable SKILL.md files.

Warp-compatible skills live at ``<workspace>/.agents/skills/<name>/SKILL.md``.
This module closes the loop: it records the agent's own tool-call sequences,
detects when a workflow repeats, and renders the proven steps as a skill the
agent (or any Warp-compatible host) can replay later.

The recorder is a process-wide singleton — the interactive REPL is the main
capture surface, and a global matches how stateless.py shares its assembler.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Argument keys whose VALUES are never worth copying into a skill
# (payloads, not parameters).
_VOLATILE_ARG_KEYS = {"content", "text", "old", "new", "error_output", "result"}
_MAX_DIGEST_VALUE = 60


def _digest_args(args: dict[str, Any] | None) -> dict[str, str]:
    """Compact args to a readable digest — keys kept, payloads elided."""
    out: dict[str, str] = {}
    for key, value in (args or {}).items():
        if key in _VOLATILE_ARG_KEYS:
            out[key] = f"<{len(str(value))} chars>"
            continue
        text = str(value)
        if len(text) > _MAX_DIGEST_VALUE:
            text = text[:_MAX_DIGEST_VALUE - 3] + "..."
        out[key] = text
    return out


@dataclass
class CapturedStep:
    tool: str
    args: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        if not self.args:
            return self.tool
        arg_str = ", ".join(f"{k}: {v}" for k, v in self.args.items())
        return f"{self.tool} ({arg_str})"


@dataclass
class SkillSuggestion:
    """A repeated tail sequence detected in the recorded history."""

    steps: list[CapturedStep]
    occurrences: int


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return slug or "captured-skill"


class SkillCapture:
    """Rolling recorder of tool-call sequences + repetition detector."""

    def __init__(self, maxlen: int = 200):
        self._steps: deque[CapturedStep] = deque(maxlen=maxlen)

    def record(self, tool: str, args: dict[str, Any] | None = None) -> None:
        if not tool:
            return
        # Subagent lifecycle/status chatter is bookkeeping, not workflow —
        # capturing it would bury the real steps.
        if tool.startswith("subagent_") or tool.startswith("orchestrate_"):
            return
        self._steps.append(CapturedStep(tool=tool, args=_digest_args(args)))

    def recent(self, n: int = 10) -> list[CapturedStep]:
        return list(self._steps)[-n:]

    def __len__(self) -> int:
        return len(self._steps)

    def suggest(self, max_window: int = 5, min_repeats: int = 2) -> SkillSuggestion | None:
        """Find the longest recent tail sequence that repeated often enough.

        Matching is on tool-name signatures (argument values legitimately
        vary between runs); the returned steps carry the tail's digests so
        the rendered skill shows realistic examples.
        """
        names = [s.tool for s in self._steps]
        total = len(names)
        if total < min_repeats * 2:
            return None

        for window in range(min(max_window, total // min_repeats), 1, -1):
            tail_sig = tuple(names[total - window:])
            occurrences = sum(
                1 for i in range(total - window + 1)
                if tuple(names[i:i + window]) == tail_sig
            )
            if occurrences >= min_repeats:
                return SkillSuggestion(
                    steps=list(self._steps)[total - window:],
                    occurrences=occurrences,
                )
        return None

    def render_skill(
        self,
        name: str,
        description: str,
        workspace: str,
        steps: list[CapturedStep] | None = None,
        merge: bool = True,
    ) -> tuple[Path, bool]:
        """Write a captured workflow as a Warp-compatible SKILL.md.

        With ``merge=True`` and an existing skill of the same slug, the
        capture count is bumped instead of creating sibling directories;
        a *differing* step sequence is appended as a variant. Returns
        ``(skill_file, merged)``.
        """
        slug = _slugify(name)
        steps = steps if steps is not None else list(self._steps)
        if not steps:
            raise ValueError("no recorded steps to render")

        skill_file = (
            Path(workspace).resolve() / ".agents" / "skills" / slug / "SKILL.md"
        )
        existing = parse_captured_skill(skill_file) if merge else None

        if existing is not None:
            body = _merge_into(existing, slug, description, steps)
            merged = True
        else:
            if skill_file.exists():
                # A skill we didn't capture — never overwrite foreign work;
                # capture under a fresh sibling slug instead.
                skills_root = skill_file.parent.parent
                n = 2
                while (skills_root / f"{slug}-{n}" / "SKILL.md").exists():
                    n += 1
                slug = f"{slug}-{n}"
                skill_file = skills_root / slug / "SKILL.md"
            body = _render_new(slug, description, steps)
            merged = False

        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(body, encoding="utf-8")
        logger.info("Captured skill %s at %s",
                    "merged into" if merged else "written to", skill_file)
        return skill_file, merged


_capture: SkillCapture | None = None


def _render_new(slug: str, description: str, steps: list[CapturedStep]) -> str:
    lines = [
        "---",
        f"name: {slug}",
        f"description: {description.strip() or f'Captured {slug} workflow'}",
        "triggers:",
        f"  - {slug}",
        "wisp_captures: 1",
        "---",
        "",
        f"# {slug}",
        "",
        f"Workflow captured by Wisp skill capture on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
        f"Follow these steps in order, adapting paths to the current task.",
        "",
        "## Steps",
        "",
    ]
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {step.describe()}")
    lines.append("")
    return "\n".join(lines)


def _extract_numbered_lines(section: str) -> list[str]:
    return [line.strip() for line in section.splitlines()
            if line.strip() and line.strip()[0].isdigit()]


def parse_captured_skill(skill_file: Path) -> dict[str, Any] | None:
    """Read a Wisp-captured SKILL.md back into structured form.

    Returns None when the file is missing or not Wisp-captured (no
    ``wisp_captures`` marker) so foreign skills are never rewritten.
    """
    if not skill_file.exists():
        return None
    try:
        import yaml  # type: ignore[import-untyped]

        content = skill_file.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        meta = yaml.safe_load(parts[1].strip())
        if not isinstance(meta, dict) or "wisp_captures" not in meta:
            return None

        body = parts[2]
        steps_section = body.split("## Steps", 1)[1] if "## Steps" in body else ""
        variants: list[list[str]] = []
        if "## Variants" in steps_section:
            primary_part, variants_part = steps_section.split("## Variants", 1)
            current: list[str] = []
            for line in variants_part.splitlines():
                stripped = line.strip()
                if stripped.startswith("- capture:"):
                    if current:
                        variants.append(current)
                    current = []
                elif stripped and stripped[0].isdigit() and current is not None:
                    # Variant lines are indented under "- capture:".
                    current.append(stripped)
            if current:
                variants.append(current)
        else:
            primary_part = steps_section

        return {
            "name": str(meta.get("name", "")),
            "description": str(meta.get("description", "")),
            "captures": int(meta.get("wisp_captures", 1)),
            "primary_steps": _extract_numbered_lines(primary_part),
            "variants": variants,
        }
    except Exception:
        logger.debug("Could not parse existing skill %s", skill_file, exc_info=True)
        return None


def _merge_into(
    existing: dict[str, Any],
    slug: str,
    description: str,
    steps: list[CapturedStep],
) -> str:
    """Bump captures; record genuinely different sequences as variants."""
    rendered = [f"{i}. {s.describe()}" for i, s in enumerate(steps, 1)]
    joined = "\n".join(rendered)
    same_as_primary = rendered == existing["primary_steps"]
    known_variants = ["\n".join(v) for v in existing["variants"]]
    variant_lines: list[str] | None = None
    if not same_as_primary and joined not in known_variants:
        variant_lines = rendered

    lines = [
        "---",
        f"name: {slug}",
        f"description: {(description.strip() or existing['description'])}",
        "triggers:",
        f"  - {slug}",
        f"wisp_captures: {existing['captures'] + 1}",
        "---",
        "",
        f"# {slug}",
        "",
        f"Workflow captured by Wisp skill capture on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} "
        f"({existing['captures'] + 1} captures). "
        f"Follow these steps in order, adapting paths to the current task.",
        "",
        "## Steps",
        "",
    ]
    lines.extend(existing["primary_steps"])
    lines.append("")
    if variant_lines or existing["variants"]:
        lines.append("## Variants")
        lines.append("")
        for v in existing["variants"]:
            lines.append("- capture:")
            lines.extend(f"{v_line}" for v_line in v)
            lines.append("")
        if variant_lines:
            lines.append("- capture:")
            lines.extend(f"{v_line}" for v_line in variant_lines)
            lines.append("")
    return "\n".join(lines)


def get_capture() -> SkillCapture:
    """Process-global recorder (matches the shared-assembler pattern)."""
    global _capture
    if _capture is None:
        _capture = SkillCapture()
    return _capture


def reset_capture() -> None:
    """Test hook — drop the singleton."""
    global _capture
    _capture = None
