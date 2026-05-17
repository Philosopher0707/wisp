"""Environment variable scrubbing helpers for tool execution.

Provides a centralized allow-list for what the hook subprocess may see,
preventing credential leakage when arbitrary hook scripts run.
"""

import os

# Keys that a hook subprocess is *allowed* to see.  Everything else is
# stripped so that a compromised / self-installed hook cannot exfiltrate
# API keys, cloud credentials, tokens, or HOME-based paths.
_ALLOWED_ENV_KEYS: frozenset[str] = frozenset({
    # Standard POSIX / cross-platform
    "PATH",
    "SHELL",
    "TERM",
    "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION",
    "COLORTERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PWD",
    "OLDPWD",
    "TMPDIR",
    "TEMP",
    "TMP",
    # Wisp-specific hooks need *only* these four ( injected by HookManager )
    "WISP_HOOK_EVENT",
    "WISP_TOOL_NAME",
    "WISP_WORKSPACE",
    "WISP_SESSION_ID",
})


def scrub_sensitive_env(env: dict | None = None) -> dict[str, str]:
    """Return a copy of ``env`` with only safe variables preserved.

    If ``env`` is *None*, ``os.environ`` is used as the source.
    """
    src = env if env is not None else os.environ
    return {k: v for k, v in src.items() if k in _ALLOWED_ENV_KEYS}
