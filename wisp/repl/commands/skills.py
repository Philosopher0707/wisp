"""Skill commands: /skill (list, load, suggest, save captured workflows).
Split from wisp/commands.py (back-compat shim)."""

import logging

from wisp.colors import success, error, warning, info, dim, accent
from wisp.repl.commands import register

logger = logging.getLogger(__name__)


@register("skill", "Load or list skills (suggest/save capture workflows)",
          aliases=("s",), usage="/skill [name | suggest | save <name>]")
def cmd_skill(agent, args: str):
    from wisp.skills import discover_skills, find_skill

    ws = agent.config.workspace or "."

    # ── Capture subcommands ─────────────────────────────────────────
    parts = args.split(maxsplit=2)
    sub = parts[0] if parts else ""
    if sub == "suggest":
        from wisp.skill_capture import get_capture
        capture = get_capture()
        suggestion = capture.suggest()
        if suggestion is None:
            print(dim("No repeated workflow detected yet. Run a procedure "
                      "twice, then check again."))
            return
        print(accent(f"Repeated workflow detected ({suggestion.occurrences}x):"))
        for i, step in enumerate(suggestion.steps, 1):
            print(f"  {i}. {step.describe()}")
        print(dim("Save it: /skill save <name>"))
        return

    if sub == "save":
        if len(parts) < 2:
            print(info("Usage: /skill save <name> [description]"))
            return
        from wisp.skill_capture import get_capture
        capture = get_capture()
        name = parts[1].strip()
        description = parts[2].strip() if len(parts) > 2 else f"Captured {name} workflow"
        try:
            path, merged = capture.render_skill(name, description, ws)
        except ValueError as e:
            print(error(str(e)))
            return
        except OSError as e:
            print(error(f"Could not write skill file: {e}"))
            return
        if merged:
            print(success(f"✓ Skill merged: {path}"))
        else:
            print(success(f"✓ Skill saved: {path}"))
        print(dim(f"Load it with /skill {path.parent.name}"))
        return

    if not args or not args.strip():
        skills = discover_skills(ws)
        if not skills:
            print(dim("No skills found."))
            return
        active = getattr(agent, "_active_skill", None)
        for sk in skills:
            marker = accent(" → ") if active == sk.name else "   "
            print(f"{marker}{accent(sk.name)}: {sk.description}")
        return

    name = args.strip()
    skill = find_skill(name, ws)
    if skill is None:
        print(warning(f"⚠ Skill '{name}' not found."))
        return

    agent._active_skill = name
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()

    print(success(f"✓ Skill loaded: {skill.name}"))
