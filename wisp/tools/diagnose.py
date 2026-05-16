"""Error diagnosis tool for Wisp.

Analyzes test output, tracebacks, and command errors to provide
structured diagnosis with root cause and fix suggestions.
"""

import logging

from wisp.tools._utils import _validate_string

logger = logging.getLogger(__name__)


def tool_diagnose(error_output: str, workspace: str = ".") -> str:
    """Diagnose an error from test output, traceback, or command output.

    Use when tests fail, code crashes, or tools return errors.
    Returns a structured diagnosis with error type, location, root cause, and fix suggestion.
    """
    from wisp.error_diagnosis import diagnose
    diag = diagnose(error_output, workspace)
    return diag.format()
