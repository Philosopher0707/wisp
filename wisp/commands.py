"""Back-compat shim for slash commands.

All implementation moved to :mod:`wisp.repl.commands` (registry, dispatch,
and the thematic command modules). This module re-exports the same public
API — ``register``, ``lookup``, ``dispatch``, ``all_commands``, ``_REGISTRY``
and every ``cmd_*`` handler — so existing callers and tests keep working
unchanged.
"""

from wisp.repl.commands import (  # noqa: F401
    Command,
    _REGISTRY,
    all_commands,
    dispatch,
    lookup,
    register,
)
from wisp.repl.commands.agents import (  # noqa: F401
    cmd_agents,
    cmd_approve,
    cmd_spawn,
    cmd_swarm,
    cmd_thinking,
    _get_background_manager,
    _get_orchestrator,
    _print_subagent_progress,
)
from wisp.repl.commands.core import (  # noqa: F401
    cmd_clear,
    cmd_continue,
    cmd_drop,
    cmd_exit,
    cmd_help,
    cmd_metrics,
    cmd_new,
    cmd_save,
    cmd_session,
    cmd_tokens,
)
from wisp.repl.commands.files import (  # noqa: F401
    cmd_bash,
    cmd_grep,
    cmd_init,
    cmd_ls,
    cmd_read,
    cmd_workspace,
)
from wisp.repl.commands.provider import (  # noqa: F401
    cmd_model,
    cmd_provider,
    cmd_setup,
)
from wisp.repl.commands.session_cmds import (  # noqa: F401
    cmd_compact,
    cmd_sessions,
)
from wisp.repl.commands.skills import cmd_skill  # noqa: F401
from wisp.repl.commands.doctor import cmd_doctor  # noqa: F401