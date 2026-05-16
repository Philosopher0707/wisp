"""Parallel multi-agent orchestration system for Wisp.

.. deprecated::
    This module is deprecated. Use ``wisp.multi_agent.SubagentOrchestrator``
    for new code. These exports are kept as aliases for backward compatibility
    and will be removed in v2.0.
"""

import warnings

warnings.warn(
    "wisp.subagent_runner is deprecated. Use wisp.multi_agent.SubagentOrchestrator for new code.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export unified types from the new location
from wisp.multi_agent.task import SubagentContract as SubagentSpec, SubagentResult
from wisp.multi_agent.orchestrator import SubagentOrchestrator as SubagentRunner

__all__ = ["SubagentSpec", "SubagentResult", "SubagentRunner"]
