"""CLI doctor command — spec deliverable shim.

Spec lists `wisp/cli/commands/doctor.py` as the REPL handler location,
but the live REPL registry lives in `wisp.repl.commands`. This file is the
spec-compliant entry point that re-exports the same handler so both
import paths work.

Importing this module has the same side-effect as importing
`wisp.repl.commands.doctor`: it registers `/doctor` (alias `/check`).
"""

from __future__ import annotations

# Re-export the REPL implementation — single source of truth
from wisp.repl.commands.doctor import cmd_doctor  # noqa: F401

__all__ = ["cmd_doctor"]
