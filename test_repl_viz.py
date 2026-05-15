#!/usr/bin/env python3
"""Quick visual test for the new REPL input rendering."""
import sys
sys.path.insert(0, "/Users/philosopher/Documents/wisp")

from wisp.transport.cli import (
    _continuation_prompt,
    _box,
    dim, info, success, error, accent, warning,
)

# ── 1. Launch banner mock ──
ws = "/Users/philosopher/Documents/wisp"
banner_lines = [
    f"  Model:      llama3.2",
    f"  Session:    a1b2c3d4-e5f6",
    f"  Workspace:  {ws}",
]
banner_lines.append("")
banner_lines.append("  /help for commands  ·  Ctrl+C/D to exit")
print(_box("\n".join(banner_lines), title="🔮 Wisp"))
print()

# ── 2. Session resume banner mock ──
resume_lines = [
    f"  Title:      Fix auth bug",
    f"  Model:      llama3.2",
    f"  Session:    a1b2c3d4-e5f6",
    f"  Messages:   12",
    f"  Workspace:  {ws}",
    f"  Last:       Fix the authentication flow in the login handler...",
    "",
    "  /help for commands  ·  Ctrl+C/D to exit",
]
print(_box("\n".join(resume_lines), title="📋 Continuing Session"))
print()

# ── 3. Single line input ──
print("➜ write a fibonacci function")
print()

# ── 4. Multiline input ──
print("➜ def fibonacci(n):")
print(_continuation_prompt(1) + "    if n <= 1:")
print(_continuation_prompt(2) + "    return n")
print(_continuation_prompt(1) + "    return fibonacci(n-1) + fibonacci(n-2)")
print(dim("  📝 4 lines"))
print()

# ── 5. Deep nesting ──
print("➜ def outer():")
print(_continuation_prompt(1) + "    def inner():")
print(_continuation_prompt(2) + "        if True:")
print(_continuation_prompt(3) + "            print('hello')")
print(dim("  📝 4 lines"))
print()

# ── 6. Paste a code block ──
print("➜ import os")
print(_continuation_prompt(0) + "import sys")
print(_continuation_prompt(0) + "from pathlib import Path")
print(_continuation_prompt(0) + "")
print(_continuation_prompt(0) + "def main():")
print(_continuation_prompt(1) + "    path = Path.cwd()")
print(_continuation_prompt(1) + "    for f in path.iterdir():")
print(_continuation_prompt(2) + "        if f.is_file():")
print(_continuation_prompt(3) + "            print(f.name)")
print(dim("  📝 10 lines"))
