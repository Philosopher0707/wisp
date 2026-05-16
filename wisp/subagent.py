"""Subagent spawning — delegate tasks to child agents with contracts and timeouts.

.. deprecated::
    This module is deprecated. Use ``wisp.multi_agent.SubagentOrchestrator``
    for new code. These exports are kept as aliases for backward compatibility
    and will be removed in v2.0.
"""

import warnings

warnings.warn(
    "wisp.subagent is deprecated. Use wisp.multi_agent.SubagentOrchestrator for new code.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export unified types from the new location
from wisp.multi_agent.task import SubagentContract as SubagentTask, SubagentResult
from wisp.multi_agent.orchestrator import SubagentOrchestrator as SubagentRunner

# Backward compatibility aliases
SubagentContract = SubagentTask

__all__ = ["SubagentContract", "SubagentTask", "SubagentResult", "SubagentRunner"]
