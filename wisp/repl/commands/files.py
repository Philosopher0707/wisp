"""File/workspace commands: /bash, /workspace, /grep, /ls, /read, /init.
Split from wisp/commands.py (back-compat shim)."""

import logging
import subprocess
from pathlib import Path

from wisp.colors import success, error, warning, info, dim, accent
from wisp.repl.commands import register

logger = logging.getLogger(__name__)


@register("bash", "Run a bash command directly", aliases=("!", "sh"), usage="/bash <command>")
def cmd_bash(agent, args: str):
    if not args:
        print(info("Usage: /bash <command>"))
        return
    from wisp.tools import tool_run_bash, check_dangerous_command

    reason = check_dangerous_command(args)
    if reason:
        import sys
        if not sys.stdin.isatty():
            print(warning(f"⚠️  Blocked dangerous command ({reason})"))
            return
        try:
            print(warning(f"     ⚠️  DANGEROUS: {reason}"))
            choice = input("     Type 'yes' to approve bash: ").strip().lower()
            if choice != "yes":
                print(dim("  ⏭  Skipped"))
                return
        except (KeyboardInterrupt, EOFError, OSError):
            print()
            return

    ws = agent.config.workspace or "."
    try:
        result = tool_run_bash(args, ws)
        print(result)
    except Exception as e:
        print(error(f"✗ {e}"))


@register("workspace", "Change working directory", aliases=("cd", "w"), usage="/workspace <dir>")
def cmd_workspace(agent, args: str):
    if not args:
        print(f"Current workspace: {accent(agent.config.workspace or '.')}")
        return
    new_ws = args.strip()
    path = Path(new_ws).expanduser()
    if not path.exists():
        print(error(f"✗ Path does not exist: {path}"))
        return
    if not path.is_dir():
        print(error(f"✗ Not a directory: {path}"))
        return
    agent.config = agent.config.replace(workspace=str(path.resolve()))
    # Invalidate system prompt cache because skill discovery is workspace-relative
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()
    print(success(f"✓ Workspace: {agent.config.workspace}"))


@register("grep", "Search files with grep", aliases=("g", "search"), usage="/grep <pattern> [path]")
def cmd_grep(agent, args: str):
    if not args:
        print(info("Usage: /grep <pattern> [path]"))
        return
    # Last whitespace-separated token is the target path, everything before is the pattern
    parts = args.rsplit(maxsplit=1)
    if len(parts) == 1:
        pattern = parts[0]
        target = "."
    else:
        # If the last token looks like a file path (contains . / or exists), treat it as path
        candidate_path = parts[1]
        ws = agent.config.workspace or "."
        full_path = Path(ws) / candidate_path
        if full_path.exists() or "/" in candidate_path or "." in candidate_path:
            pattern = parts[0]
            target = candidate_path
        else:
            # Last token is part of the pattern
            pattern = args
            target = "."
    ws = agent.config.workspace or "."
    try:
        from wisp.tools._utils import _resolve_path
        target_path = _resolve_path(target, ws)
    except Exception as e:
        # Same containment policy as /read — absolute or traversal paths
        # must not let /grep search outside the workspace.
        print(error(f"✗ {e}"))
        return
    if not target_path.exists():
        print(error(f"✗ Path not found: {target_path}"))
        return
    try:
        result = subprocess.run(
            ["grep", "-r", "-n", "--color=never", pattern, str(target_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = result.stdout.splitlines()
        if not lines:
            print(dim("(no matches)"))
            return
        for line in lines[:200]:
            print(line)
        if len(lines) > 200:
            print(dim(f"... and {len(lines) - 200} more matches"))
    except subprocess.TimeoutExpired:
        print(error("✗ grep timed out after 30s"))
    except Exception as e:
        print(error(f"✗ grep failed: {e}"))


@register("ls", "List files in a directory", aliases=("files", "dir"), usage="/ls [path] [pattern]")
def cmd_ls(agent, args: str):
    from wisp.tools import tool_list_files
    ws = agent.config.workspace or "."
    parts = args.split(maxsplit=1) if args else []
    path = parts[0] if parts else "."
    pattern = parts[1] if len(parts) > 1 else "*"
    try:
        result = tool_list_files(path, ws, pattern)
        print(result)
    except Exception as e:
        print(error(f"✗ {e}"))


@register("read", "Read a file", aliases=("cat",), usage="/read <file> [offset] [limit]")
def cmd_read(agent, args: str):
    from wisp.tools import tool_read_file
    ws = agent.config.workspace or "."
    parts = args.split()
    if not parts:
        print(info("Usage: /read <file> [offset] [limit]"))
        return
    path = parts[0]
    offset = int(parts[1]) if len(parts) > 1 else 0
    limit = int(parts[2]) if len(parts) > 2 else 2000
    try:
        result = tool_read_file(path, ws, offset, limit)
        print(result)
    except Exception as e:
        print(error(f"✗ {e}"))


@register("init", "Generate wisp.md for this codebase", aliases=(), usage="/init [overwrite]")
def cmd_init(agent, args: str):
    """Analyze the current workspace and generate a wisp.md file.

    The generated file includes project overview, architecture, key files,
    conventions, and dependencies — giving Wisp instant context whenever
    it enters this project.
    """
    ws = Path(agent.config.workspace or ".").resolve()
    wisp_md = ws / "wisp.md"

    if wisp_md.exists() and "overwrite" not in args.lower():
        print(warning(f"⚠ {wisp_md.name} already exists."))
        print(dim("   Run '/init overwrite' to regenerate."))
        return

    print(info(f"🔍 Analyzing {ws.name}…"))

    # ── Gather project metadata ──
    from wisp.project_context import detect_project_context
    ctx = detect_project_context(str(ws))

    # ── Gather file structure ──
    top_files = []
    top_dirs = []
    for item in sorted(ws.iterdir()):
        if item.name.startswith(".") and item.name not in (".github", ".vscode"):
            continue
        if item.is_file():
            top_files.append(item.name)
        elif item.is_dir():
            top_dirs.append(item.name + "/")

    # ── Gather source file stats ──
    from wisp.code_index import build_index
    index = build_index(str(ws))

    # ── Find key source files (entry points, main modules) ──
    key_files = []
    for fname in top_files:
        if fname.lower() in ("readme.md", "readme.rst", "readme.txt"):
            key_files.append((fname, "Project documentation"))
        elif fname.lower() in ("main.py", "app.py", "index.js", "main.rs", "main.go"):
            key_files.append((fname, "Application entry point"))
        elif fname in ("pyproject.toml", "package.json", "cargo.toml", "go.mod", "setup.py"):
            key_files.append((fname, "Project configuration"))
        elif fname in ("dockerfile", "docker-compose.yml", "compose.yaml"):
            key_files.append((fname, "Docker configuration"))
        elif fname in ("makefile", "justfile"):
            key_files.append((fname, "Build automation"))
        elif fname in ("requirements.txt", "poetry.lock", "yarn.lock", "cargo.lock"):
            key_files.append((fname, "Dependency lock file"))

    # ── Find test directories / CI / symbols ──
    test_dirs = [d for d in top_dirs if "test" in d.lower() or "spec" in d.lower()]
    ci_dirs = [d for d in top_dirs if d in (".github/", ".gitlab/", ".circleci/")]

    top_symbols = []
    for file_symbols in index.symbols.values():
        for sym in file_symbols:
            if sym.kind in ("class", "function", "struct", "trait", "interface"):
                top_symbols.append(sym)
    top_symbols.sort(key=lambda s: (s.file, s.line))
    top_symbols = top_symbols[:30]

    # ── Build wisp.md content ──
    lines: list[str] = [f"# {ctx.project_name or ws.name}", "", "## Overview", ""]
    if ctx.project_name:
        lines.append(f"**Project:** {ctx.project_name}")
    if ctx.language:
        ver = f" {ctx.language_version}" if ctx.language_version else ""
        lines.append(f"**Language:** {ctx.language}{ver}")
    if ctx.framework:
        lines.append(f"**Framework:** {ctx.framework}")
    if ctx.build_system:
        lines.append(f"**Build system:** {ctx.build_system}")
    lines.append("")

    if key_files:
        lines += ["## Key Files", ""]
        lines += [f"- **{fname}** — {desc}" for fname, desc in key_files]
        lines.append("")

    lines += ["## File Structure", ""]
    if top_dirs:
        lines.append(f"**Directories:** {', '.join(top_dirs)}")
    if top_files:
        lines.append(f"**Files:** {', '.join(top_files[:20])}"
                     + ("…" if len(top_files) > 20 else ""))
    lines.append("")

    lines.append("## Architecture")
    lines.append("")
    lines.append(f"{len(index.symbols)} files indexed, {index.total_symbols} symbols.")
    if top_symbols:
        lines.append("")
        lines.append("Top-level symbols:")
        lines += [f"- `{s.file.split('/')[-1]}:{s.line}` — {s.kind} `{s.name}`" for s in top_symbols]
        lines.append("")

    if test_dirs:
        lines += ["## Tests", "", f"**Directories:** {', '.join(test_dirs)}", ""]
    if ci_dirs:
        lines += ["## CI / CD", "", f"**Config directories:** {', '.join(ci_dirs)}", ""]
    if ctx.has_docker:
        lines += ["## Docker", "", "This project includes Docker configuration.", ""]

    lines.append("## Conventions")
    lines.append("")
    if ctx.language == "Python":
        lines.append("- Follow PEP 8 style guidelines")
        if "pytest" in str(ctx.test_framework).lower():
            lines.append("- Use pytest for testing")
    elif ctx.language in ("JavaScript", "TypeScript"):
        lines.append("- Follow the project's ESLint / Prettier configuration")
    elif ctx.language == "Rust":
        lines.append("- Follow Rust naming conventions and `cargo fmt`")
    elif ctx.language == "Go":
        lines.append("- Follow Go conventions: `gofmt`, `golint`")
    lines += ["- Prefer targeted edits over full file rewrites",
              "- Run tests after making changes", ""]

    lines += ["## Wisp Agent Notes", "",
              "This file was auto-generated by `/init`. Update it as the project evolves.",
              "- Use `search_symbols` to find functions/classes quickly",
              "- Use `read_file` with offset/limit for large files",
              "- Use `run_bash` for build/test commands"]
    if ctx.build_system:
        lines.append(f"- Build/test via: {ctx.build_system}")
    lines.append("")

    content = "\n".join(lines)

    try:
        wisp_md.write_text(content, encoding="utf-8")
        print(success(f"✓ Created {wisp_md.name} ({len(content)} chars)"))
        print(dim(f"   {len(top_dirs)} dirs, {len(top_files)} files, "
                  f"{index.total_symbols} symbols analyzed."))
    except Exception as e:
        print(error(f"✗ Failed to write {wisp_md.name}: {e}"))