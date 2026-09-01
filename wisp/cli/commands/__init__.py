"""CLI commands package — re-exports REPL doctor for spec compliance."""

from __future__ import annotations

# Ensure /doctor is registered even if only wisp.cli is imported
try:
    from wisp.repl.commands.doctor import cmd_doctor  # noqa: F401
except Exception:
    pass
