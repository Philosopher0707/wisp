"""Environment variable scrubbing helpers for tool execution.

Two tiers:
- ``scrub_sensitive_env``: strict allow-list for *hook* subprocesses, which
  must be treated as untrusted (self-installable, full-lifetime execution).
- ``credential_free_env``: deny-list scrub for the agent's own bash tool —
  strips credential-bearing variables while keeping a usable POSIX
  environment (PATH/HOME/etc.), so LLM-generated commands cannot read
  API keys/cloud tokens from the environment.
"""

import os
import re

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

# Keys always stripped from agent bash subprocesses regardless of pattern.
_ALWAYS_STRIPPED_ENV_KEYS: frozenset[str] = frozenset({
    "WISP_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "DOCKER_CONFIG",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HOMEBREW_GITHUB_API_TOKEN",
    "KUBECONFIG",
    "SSH_AGENT_LAUNCHER",
    "SSH_AUTH_SOCK",
})

# Normalized (underscores removed, lowercased) substring match.
_CREDENTIAL_ENV_PATTERN = re.compile(
    r"(apikey|token|secret|passw|credential|privatekey|accesskey|signingkey|ssh)"
)


def scrub_sensitive_env(env: dict | None = None) -> dict[str, str]:
    """Return a copy of ``env`` with only safe variables preserved.

    If ``env`` is *None*, ``os.environ`` is used as the source.
    """
    src = env if env is not None else os.environ
    return {k: v for k, v in src.items() if k in _ALLOWED_ENV_KEYS}


def credential_free_env(env: dict | None = None) -> tuple[dict[str, str], int]:
    """Return ``(scrbed_env, stripped_count)`` with credential vars removed.

    Deny-list approach: keeps the general POSIX environment usable while
    dropping anything that looks like a credential (by exact name or by
    normalized pattern). If ``env`` is *None*, ``os.environ`` is used.
    """
    src = env if env is not None else os.environ
    out: dict[str, str] = {}
    stripped = 0
    for k, v in src.items():
        normalized = k.replace("_", "").replace("-", "").lower()
        if k in _ALWAYS_STRIPPED_ENV_KEYS or _CREDENTIAL_ENV_PATTERN.search(normalized):
            stripped += 1
            continue
        out[k] = v
    return out, stripped
