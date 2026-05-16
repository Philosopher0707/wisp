"""Wisp tools package — domain-specific tool modules.

This package splits the monolithic wisp/tools.py into focused modules:
- filesystem: read, write, edit, list files
- bash: run shell commands
- web: fetch URLs, search the web
- git: status, diff, branch, commit, push, PR
- lsp: diagnostics, definition, references, hover, symbols
- memory: remember, recall
- search: symbol search, semantic codebase search
- plan: create plans, mark steps, update status
- diagnose: error diagnosis

All exports are re-exported from wisp.tools._legacy for backward compatibility.
In future refactors, _legacy will be replaced with direct imports from the
submodules above.
"""

# Re-export everything from the legacy module for backward compatibility
from wisp.tools._legacy import *  # noqa: F401,F403
from wisp.tools._legacy import _build_tool_metadata  # noqa: F401

# Also make submodules available for direct import
from wisp.tools import _utils
from wisp.tools import filesystem
from wisp.tools import bash
from wisp.tools import web
from wisp.tools import git
from wisp.tools import lsp
from wisp.tools import memory
from wisp.tools import search
from wisp.tools import plan
from wisp.tools import diagnose
