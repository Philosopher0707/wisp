"""Configuration for Wisp — reads settings from environment, CLI args, and config files.

Settings are resolved with priority: env vars > config file > defaults.
Config file is stored at ~/.config/wisp/config.json.
"""

import hashlib
import os
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass
from wisp.infra.security import PermissionMode
from wisp.pathsec import resolve_contained


logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
# No hardcoded model id: a baked-in name rots the moment the daemon or
# gateway drops it, and then the agent comes online pointing at a model
# that does not exist. Empty means "unset" — provider_catalog resolves it
# to the first model the active provider actually serves.
DEFAULT_MODEL = ""
DEFAULT_MAX_CONTEXT_TOKENS = 256000
WISP_CONFIG_DIR = Path.home() / ".config" / "wisp"

# ── Schema definition ────────────────────────────────────────────────

SETTINGS_SCHEMA: dict[str, dict[str, Any]] = {
    "provider": {
        "type": str,
        "default": "ollama",
        "description": "Model provider backend: ollama, openai, nvidia, openrouter, mock",
        "env_var": "WISP_PROVIDER",
    },
    "ollama_url": {
        "type": str,
        "default": DEFAULT_OLLAMA_URL,
        "description": "Ollama API endpoint URL",
        "env_var": "WISP_OLLAMA_URL",
    },
    "api_key": {
        "type": str,
        "default": "",
        "description": "API key for cloud providers (OpenAI, Anthropic, etc.)",
        "env_var": "WISP_API_KEY",
    },
    "api_base": {
        "type": str,
        "default": "",
        "description": "Base URL for OpenAI-compatible API endpoints",
        "env_var": "WISP_API_BASE",
    },
    "model": {
        "type": str,
        "default": DEFAULT_MODEL,
        "description": "Model id (empty = first model the provider serves)",
        "env_var": "WISP_MODEL",
    },
    "temperature": {
        "type": float,
        "default": 0.2,
        "min": 0.0,
        "max": 2.0,
        "description": "Model temperature (0.0–2.0)",
        "env_var": "WISP_TEMPERATURE",
    },
    "max_tokens": {
        "type": (int, type(None)),
        "default": 131072,
        "description": "Max tokens per response (set to null/None for no limit)",
        "env_var": "WISP_MAX_TOKENS",
    },
    "skill_dirs": {
        "type": list,
        "default": [".agents/skills", ".warp/skills", ".claude/skills"],
        "description": "Directories to scan for skills",
        "env_var": "WISP_SKILL_DIRS",
    },
    "context_files": {
        "type": list,
        "default": ["CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"],
        "description": "Project context files to load and inject into system prompt",
        "env_var": "WISP_CONTEXT_FILES",
    },
    "workspace": {
        "type": (str, type(None)),
        "default": None,
        "description": "Working directory (None = current dir)",
        "env_var": "WISP_WORKSPACE",
    },
    "auto_approve": {
        "type": bool,
        "default": False,
        "description": "Auto-approve tool calls without prompting",
        "env_var": "WISP_AUTO_APPROVE",
    },
    "permission_mode": {
        "type": str,
        "default": PermissionMode.AUTO_EDIT,
        "description": "Permission level: full | ask_all | auto_edit | read_only",
        "env_var": "WISP_PERMISSION_MODE",
    },
    "capability_filtering": {
        "type": bool,
        "default": False,
        "description": "Filter provider-bound tool schemas by permission_mode (READ_ONLY sees the safe surface only). Visibility only; authorization unchanged. Rollback: set false for the exact legacy surface.",
        "env_var": "WISP_CAPABILITY_FILTERING",
    },
    "tool_profile": {
        "type": str,
        "default": "core",
        "description": "Built-in tools offered to the model: core (8 core tools + grep, glob, rewind) | full (every built-in). Offer only: hidden tools stay callable and role subagents with explicit tool lists are unaffected. Rollback: full.",
        "env_var": "WISP_TOOL_PROFILE",
    },
    "invariant_gates": {
        "type": str,
        "default": "enforce",
        "description": "Deterministic harness gates (paths, commands, secrets, dependency lock) applied before every tool call and to every tool result: enforce | observe (log, never block) | off. A typo means enforce. See docs/harness/invariant-gates.md.",
        "env_var": "WISP_INVARIANT_GATES",
    },
    "dependency_lock": {
        "type": str,
        "default": "locked",
        "description": "locked (default): no command or file write may add or change a dependency or manifest. `unlocked` is the only value that opens it; anything else stays locked. Set by the operator, never by a tool argument.",
        "env_var": "WISP_DEPENDENCY_LOCK",
    },
    "gate_write_roots": {
        "type": str,
        "default": "",
        "description": "Extra directories (os.pathsep-separated) the path gate treats as writable besides the workspace.",
        "env_var": "WISP_GATE_WRITE_ROOTS",
    },
    "unattended_auto_approve_tools": {
        "type": str,
        "default": "",
        "description": "Comma-separated tools a caller with NO human to ask (subagent, background agent, bench, ACP) may run without approval. Only READ- and NETWORK-class tools count (e.g. web_fetch,web_search); mutating, shell and MCP tools are ignored. Never applies when an approver is present, and never overrides a policy bundle's forced approval, read_only mode, or the dangerous-command guard. Default empty: nothing changes until you set it.",
        "env_var": "WISP_UNATTENDED_AUTO_APPROVE_TOOLS",
    },
    "show_thinking": {
        "type": bool,
        "default": True,
        "description": "Show model reasoning trace inline",
        "env_var": "WISP_SHOW_THINKING",
    },
    "show_tool_output": {
        "type": bool,
        "default": True,
        "description": "Show full tool output (when false, collapse to one-liners)",
        "env_var": "WISP_SHOW_TOOL_OUTPUT",
    },
    "diff_max_lines": {
        "type": int,
        "default": 50,
        "min": 0,
        "description": "Max diff lines rendered in the terminal for write/edit tools (0 = unlimited)",
        "env_var": "WISP_DIFF_MAX_LINES",
    },
    "compact_mode": {
        "type": bool,
        "default": False,
        "description": "Minimal rendering mode — no boxes, flat output",
        "env_var": "WISP_COMPACT_MODE",
    },
    "verification_loop": {
        "type": bool,
        "default": True,
        "description": "Require exit-0 verification (tests/linter) after code edits before a turn may complete",
        "env_var": "WISP_VERIFICATION_LOOP",
    },
    "env_context": {
        "type": bool,
        "default": True,
        "description": "Inject live environment facts (cwd, OS, git, package managers) into the system prompt",
        "env_var": "WISP_ENV_CONTEXT",
    },
    "log_format": {
        "type": str,
        "default": "text",
        "description": "Log output format: text (human-readable) or json (structured)",
        "env_var": "WISP_LOG_FORMAT",
    },
    "max_iterations": {
        "type": int,
        "default": 200,
        "min": 1,
        "max": 200,
        "description": "Max agent loop iterations per user turn",
        "env_var": "WISP_MAX_ITERATIONS",
    },
    "turn_timeout": {
        "type": int,
        "default": 7200,
        "min": 10,
        "max": 7200,
        "description": "Max seconds for a single agent turn before timeout",
        "env_var": "WISP_TURN_TIMEOUT",
    },
    "write_tools": {
        "type": list,
        "default": ["write_file", "edit_file", "run_bash", "git_commit", "git_push",
                     "gh_pr_create", "gh_pr_comment", "gh_pr_close", "gh_pr_merge", "git_sync_base", "spawn", "fanout", "spawn_background"],
        "description": "Tools that require approval in restricted permission modes",
        "env_var": "WISP_WRITE_TOOLS",
    },
    "max_reflections": {
        "type": int,
        "default": 3,
        "min": 0,
        "max": 10,
        "description": "Max repeated identical tool calls before stopping (0 = disabled)",
        "env_var": "WISP_MAX_REFLECTIONS",
    },
    "max_context_tokens": {
        "type": int,
        "default": DEFAULT_MAX_CONTEXT_TOKENS,
        "min": 1024,
        "description": "Context window size in tokens (auto-detected if not set)",
        "env_var": "WISP_MAX_CONTEXT_TOKENS",
    },
    "chars_per_token": {
        "type": int,
        "default": 4,
        "min": 1,
        "max": 10,
        "description": "Estimated chars per token for context budgeting",
        "env_var": "WISP_CHARS_PER_TOKEN",
    },
    "auto_compact": {
        "type": bool,
        "default": True,
        "description": "Automatically compact sessions when they grow too long",
        "env_var": "WISP_AUTO_COMPACT",
    },
    "compact_threshold_tokens": {
        "type": int,
        "default": 75,
        "min": 10,
        "max": 95,
        "description": "Token usage percentage (0-100) to trigger auto-compaction",
        "env_var": "WISP_COMPACT_THRESHOLD_TOKENS",
    },
    "compact_keep_recent": {
        "type": int,
        "default": 6,
        "min": 4,
        "max": 50,
        "description": "Number of recent messages to preserve during compaction (must be even to preserve turn symmetry)",
        "env_var": "WISP_COMPACT_KEEP_RECENT",
    },
    "circuit_breaker_failure_threshold": {
        "type": int,
        "default": 5,
        "min": 1,
        "max": 20,
        "description": "Consecutive provider failures before circuit opens",
        "env_var": "WISP_CB_FAILURE_THRESHOLD",
    },
    "circuit_breaker_success_threshold": {
        "type": int,
        "default": 2,
        "min": 1,
        "max": 10,
        "description": "Consecutive successes in half-open before circuit closes",
        "env_var": "WISP_CB_SUCCESS_THRESHOLD",
    },
    "circuit_breaker_recovery_timeout": {
        "type": float,
        "default": 30.0,
        "min": 1.0,
        "max": 300.0,
        "description": "Seconds before attempting recovery after circuit opens",
        "env_var": "WISP_CB_RECOVERY_TIMEOUT",
    },
    "graph_max_iterations": {
        "type": int,
        "default": 5,
        "min": 1,
        "max": 200,
        "description": "Max iterations for the agentic graph outer loop (spec default 5, reconciled with turn loop 200)",
        "env_var": "WISP_GRAPH_MAX_ITERATIONS",
    },
    "graph_sandbox_timeout": {
        "type": float,
        "default": 120.0,
        "min": 1.0,
        "max": 3600.0,
        "description": "Per-command timeout for the graph sandbox_executor node",
        "env_var": "WISP_GRAPH_SANDBOX_TIMEOUT",
    },
    "graph_oscillation_guard": {
        "type": bool,
        "default": True,
        "description": "Enable graph-level infinite-loop / oscillation detection",
        "env_var": "WISP_GRAPH_OSCILLATION_GUARD",
    },
    "autonomous": {
        "type": bool,
        "default": False,
        "description": "Fully autonomous coding agent — auto-approves safe writes/bash without human prompts (Cursor/Aider mode). Dangerous commands still blocked. (Legacy GraphRunner reference removed in 12.5A; canonical runtime is wisp/graph.)",
        "env_var": "WISP_AUTONOMOUS",
    },
    "durable_runs": {
        "type": bool,
        "default": True,
        "description": (
            "Migration P0 rollback flag: persist a durable RunRecord per turn and "
            "give BackgroundAgentManager its SQLiteRunStore (durable admission, "
            "leases, idempotency, crash recovery). Off restores the pre-migration "
            "in-memory-only behavior exactly."
        ),
        "env_var": "WISP_DURABLE_RUNS",
    },
    "session_event_fidelity": {
        "type": bool,
        "default": True,
        "description": (
            "Migration P0 rollback flag: journal assistant_message / tool_call / "
            "tool_result events into the append-only session event log so replay "
            "can reconstruct a turn. Off writes only user_message / error / done, "
            "as before."
        ),
        "env_var": "WISP_SESSION_EVENT_FIDELITY",
    },
    "turn_spans": {
        "type": bool,
        "default": True,
        "description": (
            "Migration P0 rollback flag: emit a trace span per turn and per tool "
            "call into the SQLite trace store. Off emits no spans, as before."
        ),
        "env_var": "WISP_TURN_SPANS",
    },
    "turn_journal": {
        "type": bool,
        "default": True,
        "description": (
            "Migration P1 rollback flag: journal each tool exchange the moment "
            "it closes, so a crash mid-turn leaves the completed exchanges on "
            "disk. Off writes the whole turn body once at turn end, as before."
        ),
        "env_var": "WISP_TURN_JOURNAL",
    },
    "proposal_boundary": {
        "type": bool,
        "default": True,
        "description": (
            "Migration P2 rollback flag: record a ToolRequest proposal and a "
            "ToolResult outcome for every tool call, including rejections. "
            "Off → no proposal/outcome records, as before."
        ),
        "env_var": "WISP_PROPOSAL_BOUNDARY",
    },
    "record_verdict": {
        "type": bool,
        "default": False,
        "description": (
            "Migration P3 stage 3a: record a completion verdict (PASS / FAIL / "
            "INCONCLUSIVE) against acceptance criteria at turn end. RECORDS "
            "only — the completion rule is unchanged. Defaults OFF because, "
            "unlike the other durable-record flags, it adds a record to the "
            "log of every existing caller."
        ),
        "env_var": "WISP_RECORD_VERDICT",
    },
    "task_graph": {
        "type": bool,
        "default": False,
        "description": (
            "Migration P4 rollback flag: materialize each turn as a task graph "
            "of AGENT nodes with persisted readiness, recorded as TASK_GRAPH + "
            "NODE_TRANSITION events. Off -> the message list remains "
            "authoritative and no graph is recorded. Defaults OFF."
        ),
        "env_var": "WISP_TASK_GRAPH",
    },
    "recovery_ladder": {
        "type": bool,
        "default": False,
        "description": (
            "Migration POST-M13 rollback flag (ADR-0035): consult P6's "
            "recovery ladder at the turn boundary and journal the rung it "
            "chooses (RECOVERY), plus ESCALATION when the ladder is exhausted. "
            "The name was reserved by ADR-0026's reversal condition. Defaults "
            "OFF, so the production path is byte-for-byte unchanged until the "
            "flag is explicitly enabled. This gates the RECOVERY track only — "
            "it does not change completion semantics, which is why it is "
            "separate from the completion-side flags."
        ),
        "env_var": "WISP_RECOVERY_LADDER",
    },
    "goal_state": {
        "type": bool,
        "default": False,
        "description": (
            "Migration POST-M13 (ADR-0035) completion-side flag: derive and "
            "record the goal state (GOAL_MET / GOAL_UNVERIFIED / "
            "GOAL_STAGNATED / GOAL_FAILED / ESCALATED_TO_HUMAN / CANCELLED) "
            "as a journal-only GOAL_STATE record carrying its own inputs. "
            "RECORDS only — nothing acts on the state, so turn completion is "
            "unchanged. Defaults OFF for the same reason as `record_verdict` "
            "and `task_graph`: it adds a record to the log of every existing "
            "caller. This is ADR-0035 clause 9's 'recorded first, enforced "
            "later' staging."
        ),
        "env_var": "WISP_GOAL_STATE",
    },
    "stagnation_gate": {
        "type": bool,
        "default": False,
        "description": (
            "Migration POST-M13 (ADR-0036) ENFORCEMENT flag: let M13's "
            "stagnation predicate withhold `done` at the engine's pre-`done` "
            "gate for a bounded number of replan interventions, then surrender "
            "honestly. Enforcement only — with this OFF, M13 still observes and "
            "the goal record still carries the predicate; only the intervention "
            "stops. Defaults OFF, so the production path is unchanged until it "
            "is explicitly enabled. Deliberately separate from "
            "`graph_oscillation_guard`, which disables the detector itself: "
            "recording and enforcing are different concerns (ADR-0002), so the "
            "rollback has two levels."
        ),
        "env_var": "WISP_STAGNATION_GATE",
    },
    "turn_criteria_source": {
        "type": bool,
        "default": False,
        "description": (
            "ADR-0053: let the TURN path's required-criteria set carry the "
            "objective's DECLARED criteria (ADR-0050) in addition to the "
            "verification floor guard's own criterion. Satisfies ADR-0051 R1's "
            "precondition — measured there, the turn's verdict was a pure "
            "projection of the floor guard (verdict == FAIL agreed with "
            "guard.rejection() 192/192 times), so an acceptance gate keyed on it "
            "was redundant or harmful. With this ON and a declaration at the head "
            "of the prompt, the verdict can be FAIL for a reason the floor guard "
            "does not enforce, and a declared failure lands GOAL_FAILED (row 3) "
            "where a floor-only verdict landed GOAL_UNVERIFIED. Defaults OFF, and "
            "it is deliberately NOT coupled to "
            "`WISP_CRITERIA_STRUCTURED_DECLARATION`, which gates the "
            "objective-level derivation: one flag per concern (ADR-0002)."
        ),
        "env_var": "WISP_TURN_CRITERIA_SOURCE",
    },
    "rest_approval": {
        "type": bool,
        "default": False,
        "description": (
            "ADR-0057: route a REST request for an executable-config action "
            "(`hooks.create`, `mcp.add_server`, `plugins.install`) through the "
            "WebSocket channel for a human decision. ADR-0055 measured that the "
            "agent has no operation for those three names and that REST and the "
            "agent agree on every route today; what REST lacks is the ability to "
            "ASK, so a REST caller got the agent's no-approver behaviour. With "
            "this ON, those actions in `auto_edit`/`ask_all` send a "
            "`tool_approval_request` frame to a connected client and wait, bounded "
            "by `REST_APPROVAL_TIMEOUT_S`. With NO client connected the request is "
            "DENIED — the brief's constraint is that it must not hang and must not "
            "silently allow. `full` does not ask (it relaxes approval); `read_only` "
            "denies outright. Defaults OFF, i.e. today's behaviour exactly."
        ),
        "env_var": "WISP_REST_APPROVAL",
    },
    "policy_bundle": {
        "type": str,
        "default": "",
        "description": (
            "ADR-0058: the path to a signed organization policy bundle "
            "(`bundle.json`, with its `.sig` sibling beside it). Read together "
            "with `policy_pubkey`, and the organization layer engages **iff this "
            "is non-empty** (R1). Defaults empty — with it unset no `wisp.policy` "
            "code runs and the runtime is byte-for-byte today's behaviour (R2). A "
            "bundle that is named but cannot be read, has no signature, or fails "
            "verification REFUSES TO BOOT (R3): an expected control that is "
            "silently not applied is the false-assurance failure mode the M4 "
            "finding names, so absence is a configuration and invalidity is an "
            "error. An expired-but-verifying bundle is served TRIMMED, not "
            "refused (R4)."
        ),
        "env_var": "WISP_POLICY_BUNDLE",
    },
    "policy_pubkey": {
        "type": str,
        "default": "",
        "description": (
            "ADR-0058: the organization's Ed25519 PUBLIC key, base64 (raw 32 "
            "bytes) — the KEY ITSELF, not a path to it. "
            "`wisp.policy.bundle.generate_keypair()` returns it; the private half "
            "is never Wisp's to hold (0600 file or OS keychain, M4 spec §5). "
            "Required by `policy_bundle`: a bundle named with no key, or a "
            "malformed one, fails verification and refuses to boot. Inert on its "
            "own — with `policy_bundle` empty there is no bundle for it to verify "
            "(R2)."
        ),
        "env_var": "WISP_POLICY_PUBKEY",
    },
    "acceptance_gate": {
        "type": bool,
        "default": False,
        "description": (
            "ADR-0054: the ACCEPTANCE GATE. Withholds `done` at the engine's "
            "pre-`done` gate — by ADR-0036's bounded delay-not-veto model — when "
            "the objective's DECLARED criteria (ADR-0050) are not satisfied. This "
            "is the consumer ADR-0053 §10 recorded as missing for "
            "`verdict_keys_on_declared`. DEPENDENT on `turn_criteria_source`: "
            "with the source off there are no declared criteria in the set, so the "
            "gate would withhold on a verdict the record does not carry. Defaults "
            "OFF, and ADR-0051 R2's measurement contract is NOT satisfied — the "
            "population it requires needs >= 2 capable models and this environment "
            "has exactly one (see ADR-0054)."
        ),
        "env_var": "WISP_ACCEPTANCE_GATE",
    },
    "tool_pool_size": {
        "type": int,
        "default": 8,
        "min": 1,
        "max": 64,
        "description": "Worker threads for local tool execution (bounded pool)",
        "env_var": "WISP_TOOL_POOL_SIZE",
    },
    "tool_pool_network_size": {
        "type": int,
        "default": 4,
        "min": 1,
        "max": 32,
        "description": "Worker threads for network-bound tools and MCP calls (isolated pool)",
        "env_var": "WISP_TOOL_POOL_NETWORK_SIZE",
    },
}


def get_schema() -> dict[str, dict[str, Any]]:
    """Return a copy of the settings schema."""
    return dict(SETTINGS_SCHEMA)


def validate_config(config: dict[str, Any]) -> list[str]:
    """Validate config values against the schema.

    Returns a list of error messages (empty if valid).
    Unknown keys are reported as warnings, not errors.
    """
    errors: list[str] = []
    for key, value in config.items():
        if key not in SETTINGS_SCHEMA:
            errors.append(f"Unknown setting: '{key}'")
            continue

        schema = SETTINGS_SCHEMA[key]
        expected_type = schema["type"]

        # Check type
        if not isinstance(value, expected_type):
            type_name = _type_name(expected_type)
            errors.append(
                f"'{key}': expected {type_name}, got {type(value).__name__} ({value!r})"
            )
            continue

        # Check numeric range
        if isinstance(value, (int, float)):
            if "min" in schema and value < schema["min"]:
                errors.append(
                    f"'{key}': {value} is below minimum {schema['min']}"
                )
            if "max" in schema and value > schema["max"]:
                errors.append(
                    f"'{key}': {value} is above maximum {schema['max']}"
                )

    return errors


def _type_name(tp: type | tuple[type, ...]) -> str:
    """Get a human-readable type name, handling unions."""
    if isinstance(tp, tuple):
        return " or ".join(t.__name__ for t in tp if t is not type(None)) + " or None"
    return tp.__name__


# ── File I/O ─────────────────────────────────────────────────────────


def _restrict_secret_file(path: Path) -> None:
    """chmod 0600 best-effort: config/.env files can carry API keys.

    Never raises — a restrictive umask may already handle it, and a
    permission failure must not break the save itself.
    """
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def get_config_path() -> Path:
    """Return path to wisp config file, creating dir if needed."""
    WISP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return WISP_CONFIG_DIR / "config.json"


def load_config() -> dict[str, Any]:
    """Load config from ~/.config/wisp/config.json.

    A missing file is first-boot and returns {} silently. A file that
    EXISTS but fails to parse, or isn't a JSON object, raises ValueError
    instead of silently discarding every persisted setting (permission_mode
    included) back to defaults — every caller of get_setting()/WispConfig()
    now gets the same loud-refusal wisp.entry.check_config_file already
    gave the run/repl/tui path alone.
    """
    cfg_path = get_config_path()
    if not cfg_path.exists():
        return {}
    try:
        text = cfg_path.read_text()
    except OSError as exc:
        raise ValueError(f"Cannot read config file {cfg_path}: {exc}") from exc
    try:
        loaded: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Config file {cfg_path} is corrupt ({exc}); refusing to load it as empty"
        ) from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file {cfg_path} must hold a JSON object")
    return loaded


def save_config(config: dict[str, Any]) -> None:
    """Save config to ~/.config/wisp/config.json.

    Validates the config against the schema before saving.
    Raises ValueError if validation fails.
    """
    errors = validate_config(config)
    if errors:
        raise ValueError(
            "Cannot save config with invalid values:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    cfg_path = get_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, indent=2) + "\n")
    _restrict_secret_file(cfg_path)


def get_setting(key: str, default: Any = None) -> Any:
    """Resolve a setting: env var > config file > default."""
    config = load_config()
    env_key = f"WISP_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    return config.get(key, default)


def safe_getcwd() -> str:
    """``os.getcwd()`` that never raises.

    ``getcwd()`` fails with ``PermissionError`` when the process's working
    directory was deleted out from under it, or when macOS revokes disk
    access (TCC) to the launch directory — a hard crash at startup that
    must never happen. Falls back to ``$HOME``, then the temp dir.
    """
    try:
        return os.getcwd()
    except OSError:
        logging.getLogger(__name__).warning(
            "os.getcwd() failed (deleted cwd or revoked disk access) — "
            "falling back to $HOME", exc_info=True,
        )
    # Try PWD env var (shell's logical path) — handles lowercase
    # `~/documents/edac` → canonical `~/Documents/edac` TCC mismatch on
    # case-insensitive APFS. Resolve via realpath if possible.
    pwd_env = os.environ.get("PWD", "")
    if pwd_env:
        try:
            p = Path(pwd_env)
            if p.exists():
                try:
                    return str(p.resolve())
                except OSError:
                    pass
            # Heuristic: APFS case-insensitive but TCC is case-sensitive —
            # retry with canonical `Documents`
            if "/documents/" in pwd_env:
                cand = pwd_env.replace("/documents/", "/Documents/")
                if Path(cand).is_dir():
                    try:
                        return str(Path(cand).resolve())
                    except OSError:
                        return cand
                # Also handle `/documents` at end (e.g. `~/documents`)
                if pwd_env.endswith("/documents"):
                    cand2 = pwd_env[:-10] + "/Documents"
                    if Path(cand2).is_dir():
                        return cand2
            if pwd_env and Path(pwd_env).is_dir():
                return pwd_env
        except Exception:
            pass
    home = os.environ.get("HOME") or ""
    if home and Path(home).is_dir():
        return home
    return tempfile.gettempdir()



# ── Resolved config ──────────────────────────────────────────────────


def _parse_bool(value: Any, default: bool) -> bool:
    """Parse a boolean setting, falling back to default on error."""
    if isinstance(value, bool):
        return value
    try:
        return str(value).lower() == "true"
    except Exception:
        return default


def _parse_int(value: Any, default: int, min_val: int | None = None, max_val: int | None = None) -> int:
    """Parse an integer setting, falling back to default on error."""
    if isinstance(value, int):
        result = value
    else:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
    if min_val is not None and result < min_val:
        return min_val
    if max_val is not None and result > max_val:
        return max_val
    return result


def _parse_float(value: Any, default: float, min_val: float | None = None, max_val: float | None = None) -> float:
    """Parse a float setting, falling back to default on error."""
    if isinstance(value, float):
        result = value
    elif isinstance(value, int):
        result = float(value)
    else:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
    if min_val is not None and result < min_val:
        return min_val
    if max_val is not None and result > max_val:
        return max_val
    return result


def _parse_permission_mode(value: Any, default: "PermissionMode") -> "PermissionMode":
    """Parse a permission_mode setting, falling back to default on error.

    The one typed setting that wasn't already wrapped this way — a bad
    WISP_PERMISSION_MODE used to raise straight out of WispConfig() for
    every subcommand that doesn't go through entry.validate_env_config()
    (run/repl/tui only). That pre-flight still catches it early there;
    this makes every other subcommand degrade instead of crash.
    """
    if isinstance(value, PermissionMode):
        return value
    try:
        return PermissionMode(value)
    except ValueError:
        logger.warning(
            "Invalid permission_mode %r; falling back to %s", value, default.value
        )
        return default


@dataclass(frozen=True, init=False)
class WispConfig:
    """Resolved Wisp configuration — immutable.

    Reads from env vars, config file, and defaults (in priority order).
    Use config.replace(key=value) to create a modified copy.

    Fields are declared so mypy can verify every consumer; __init__ assigns
    them via object.__setattr__ because the dataclass is frozen.
    """

    # ── Provider ──────────────────────────────────────────────────
    provider: str
    ollama_url: str
    api_key: str
    api_base: str
    model: str
    temperature: float
    max_tokens: Optional[int]

    # ── Workspace & skills ────────────────────────────────────────
    skill_dirs: list[str]
    workspace: str
    context_files: list[str]
    loaded_context: str
    _context_mtimes: dict[str, float]
    _last_workspace_for_context: Optional[str]

    # ── Rendering & interaction ───────────────────────────────────
    auto_approve: bool
    show_thinking: bool
    show_tool_output: bool
    diff_max_lines: int
    compact_mode: bool
    verification_loop: bool

    # ── Prompt architecture ───────────────────────────────────────
    env_context: bool

    # ── Turn loop limits ──────────────────────────────────────────
    max_iterations: int
    turn_timeout: int
    max_reflections: int
    tool_timeout: int
    max_context_tokens: int
    _context_tokens_explicit: bool

    # ── Agentic graph loop (outer) ────────────────────────────────
    graph_max_iterations: int
    graph_sandbox_timeout: float
    graph_oscillation_guard: bool
    autonomous: bool
    chars_per_token: int

    # ── Durable runtime layer (migration P0) ──────────────────────
    # Each flag gates one independent concern so a partial rollback is
    # possible; all default ON because every write is additive and
    # best-effort (see WISP_ARCHITECTURE_DECISIONS.md ADR-0002/0004).
    # Consumers read these with getattr(config, name, True) so test
    # doubles predating the flags keep working — the same convention
    # `thin_tools` uses (core/stateless.py:1189).
    durable_runs: bool
    session_event_fidelity: bool
    turn_spans: bool
    turn_journal: bool
    proposal_boundary: bool
    record_verdict: bool
    task_graph: bool
    #: Migration POST-M13 (ADR-0035). Reserved by ADR-0026's reversal condition;
    #: this is its first implementation. Gates the RECOVERY track only.
    recovery_ladder: bool
    #: Migration POST-M13 (ADR-0035) completion side. Records the derived goal
    #: state; nothing acts on it. Defaults OFF like `record_verdict`.
    goal_state: bool
    #: Migration POST-M13 (ADR-0036). Enforcement only — M13 keeps observing
    #: and recording when this is OFF; only the replan intervention stops.
    stagnation_gate: bool
    #: ADR-0053. The turn path's criteria set gains the objective's declared
    #: criteria. Defaults OFF: with it off the verdict site is today's code.
    turn_criteria_source: bool
    #: ADR-0054. The acceptance gate: withhold `done` on an unsatisfied declared
    #: criterion. Dependent on `turn_criteria_source`; defaults OFF.
    acceptance_gate: bool
    #: ADR-0057. Route REST requests for executable-config actions through the
    #: WebSocket channel for a human decision. Defaults OFF: with it off the REST
    #: gate is today's code exactly.
    rest_approval: bool
    #: ADR-0058. The organization policy bundle: a path to `bundle.json` plus its
    #: `.sig` sibling. The organization layer engages iff this is non-empty;
    #: defaults empty, i.e. no bundle is loaded and the runtime is today's.
    policy_bundle: str
    #: ADR-0058. The organization's Ed25519 public key, base64 — the key itself,
    #: not a path. Required by `policy_bundle`; inert on its own.
    policy_pubkey: str

    # ── Modes & permissions ───────────────────────────────────────
    permission_mode: PermissionMode | str
    tool_profile: str
    invariant_gates: str
    dependency_lock: str
    reasoning_core: str
    reasoning_core_rules: str
    reasoning_journal: str
    gate_write_roots: tuple[str, ...]
    unattended_auto_approve_tools: tuple[str, ...]
    capability_filtering: bool
    plan_mode: bool
    plan_context: Optional[str]

    # ── Compaction ────────────────────────────────────────────────
    auto_compact: bool
    compact_threshold_tokens: int
    compact_keep_recent: int
    compaction_model: str

    # ── Circuit breaker ───────────────────────────────────────────
    circuit_breaker_failure_threshold: int
    circuit_breaker_success_threshold: int
    circuit_breaker_recovery_timeout: float

    # ── Concurrency & subagents ───────────────────────────────────
    thread_pool_size: int
    tool_pool_size: int
    tool_pool_network_size: int
    subagent_pool_size: int
    subagent_token_budget: int
    max_subagent_timeout: int
    max_subagent_depth: int
    max_subagent_branching: int
    write_tools: list[str]
    subagent_models: list[str]
    _subagent_depth: int
    _subagent_branch_count: int

    def __init__(self) -> None:
        object.__setattr__(self, "provider", get_setting("provider", "ollama"))
        object.__setattr__(self, "ollama_url", get_setting("ollama_url", DEFAULT_OLLAMA_URL))
        object.__setattr__(self, "api_key", get_setting("api_key", ""))
        object.__setattr__(self, "api_base", get_setting("api_base", ""))
        object.__setattr__(self, "model", get_setting("model", DEFAULT_MODEL))
        object.__setattr__(self, "temperature", float(get_setting("temperature", "0.2")))
        raw_max_tokens = get_setting("max_tokens", 131072)
        object.__setattr__(self, "max_tokens",
            int(raw_max_tokens) if raw_max_tokens is not None else None
        )
        raw_skill_dirs = get_setting(
            "skill_dirs",
            [
                ".agents/skills",
                ".warp/skills",
                ".claude/skills",
            ],
        )
        # Ensure it's a list (env vars come as strings, config file may have list)
        if isinstance(raw_skill_dirs, str):
            object.__setattr__(self, "skill_dirs",
                [d.strip() for d in raw_skill_dirs.split(",") if d.strip()]
            )
        elif isinstance(raw_skill_dirs, list):
            object.__setattr__(self, "skill_dirs", raw_skill_dirs)
        else:
            object.__setattr__(self, "skill_dirs",
                [".agents/skills", ".warp/skills", ".claude/skills"]
            )
        # Lazy default: get_setting's default arg must NOT call os.getcwd()
        # eagerly — when the env/config pins a workspace we must never touch
        # the filesystem, and getcwd() itself can raise PermissionError.
        ws_setting = get_setting("workspace")
        object.__setattr__(self, "workspace",
            ws_setting if ws_setting else safe_getcwd())
        # Auto-approve tool calls (default FALSE — require explicit opt-in).
        # When false, every mutating tool call triggers an approval_request
        # event and the transport layer must confirm before execution.
        object.__setattr__(self, "auto_approve",
            _parse_bool(get_setting("auto_approve", "false"), False)
        )
        # Show reasoning trace inline
        object.__setattr__(self, "show_thinking",
            _parse_bool(get_setting("show_thinking", "true"), True)
        )
        # Show full tool output (when false, collapse to one-liners)
        object.__setattr__(self, "show_tool_output",
            _parse_bool(get_setting("show_tool_output", "true"), True)
        )
        # Max diff lines in terminal for write/edit tools (0 = unlimited).
        # Default 50 preserves the historical cap; the transport reads it
        # via getattr(config, "diff_max_lines", 50) so older doubles keep it.
        object.__setattr__(self, "diff_max_lines",
            _parse_int(get_setting("diff_max_lines", "50"), 50, 0)
        )
        # Minimal rendering mode — no boxes, flat output, good for pipes/narrow terminals
        object.__setattr__(self, "compact_mode",
            _parse_bool(get_setting("compact_mode", "false"), False)
        )
        # Verification loop: a turn that edited code must see an exit-0
        # verification command (tests/linter) before it may complete.
        object.__setattr__(self, "verification_loop",
            _parse_bool(get_setting("verification_loop", "true"), True)
        )
        # Inject a '## Environment' block (cwd, OS, shell, git state, package
        # managers, suggested verification commands) into the system prompt
        object.__setattr__(self, "env_context",
            _parse_bool(get_setting("env_context", "true"), True)
        )
        # Max agent loop iterations per user turn
        object.__setattr__(self, "max_iterations",
            _parse_int(get_setting("max_iterations", "200"), 200, 1, 200)
        )
        # Max seconds for a single agent turn before timeout
        object.__setattr__(self, "turn_timeout",
            _parse_int(get_setting("turn_timeout", "7200"), 7200, 10, 7200)
        )
        # Session-wide subagent token ceiling; admission refuses new
        # children once spent (0 = unlimited)
        object.__setattr__(self, "subagent_token_budget",
            _parse_int(get_setting("subagent_token_budget", "2000000"),
                       2000000, 0, 100_000_000)
        )
        # Max repeated identical tool calls before stopping (0 = disabled)
        object.__setattr__(self, "max_reflections",
            _parse_int(get_setting("max_reflections", "3"), 3, 0, 10)
        )
        # Per-tool timeout in seconds (prevents stuck tools from hanging the agent)
        object.__setattr__(self, "tool_timeout",
            _parse_int(get_setting("tool_timeout", "300"), 300, 10, 3600)
        )
        # Context window guard: trim oldest messages when estimated tokens exceed this
        raw_ctx = get_setting("max_context_tokens")
        object.__setattr__(self, "max_context_tokens",
            _parse_int(raw_ctx, DEFAULT_MAX_CONTEXT_TOKENS, 1024)
        )
        # Track whether user explicitly set context window (disables auto-detection)
        object.__setattr__(self, "_context_tokens_explicit", raw_ctx is not None)
        # Permissions: full (all allowed) | ask_all (ask for writes) | auto_edit (ask for bash only) | read_only (no writes)
        object.__setattr__(self, "permission_mode",
            _parse_permission_mode(
                get_setting("permission_mode", PermissionMode.AUTO_EDIT.value),
                PermissionMode.AUTO_EDIT,
            )
        )
        # Capability filtering (13-I2): host-owned visibility partition.
        # OFF preserves the exact legacy provider surface; ON filters
        # provider-bound schemas by permission_mode. Authorization untouched.
        # Conservative default OFF — rollout is explicit and reversible.
        object.__setattr__(self, "capability_filtering",
            _parse_bool(get_setting("capability_filtering", "false"), False)
        )
        # Tool profile: which built-in tools are OFFERED (not which are callable). Unknown values fail to the
        # smaller surface, so a typo can never widen what the model sees.
        from wisp.tools.profile import DEFAULT_PROFILE, parse_profile

        object.__setattr__(self, "tool_profile", parse_profile(get_setting("tool_profile", DEFAULT_PROFILE)))
        # Harness gates: a typo or an unknown value is the strictest setting (enforce / locked), never a weaker one.
        from wisp.core.gates.gate import parse_lock, parse_mode

        object.__setattr__(self, "invariant_gates", parse_mode(get_setting("invariant_gates", "enforce")).value)
        # The reasoning core: a typo is `observe` (record only), never `enforce` and never silently `off`.
        from wisp.core.reasoning.decision import parse_mode as _parse_reasoning_mode

        object.__setattr__(self, "reasoning_core", _parse_reasoning_mode(get_setting("reasoning_core", "observe")).value)
        # Per-rule overrides, `R1=enforce,R4=enforce`: each heuristic is flipped on its own. Setting nothing enforces R1, R4 and R5 (owner decisions,
        # 2026-10-07 and 2026-10-10); ANY explicit `reasoning_core` or `reasoning_core_rules` (even empty) replaces that default, which is the kill switch.
        object.__setattr__(self, "reasoning_journal", str(get_setting("reasoning_journal", "") or ""))
        from wisp.core.reasoning.decision import (
            DEFAULT_ENFORCED_RULES,
            parse_modes as _parse_reasoning_modes,
            render_modes as _render_reasoning_modes,
        )

        explicit_core = get_setting("reasoning_core", None)
        explicit_rules = get_setting("reasoning_core_rules", None)
        rules = explicit_rules if explicit_rules is not None else ("" if explicit_core is not None else DEFAULT_ENFORCED_RULES)
        object.__setattr__(self, "reasoning_core_rules", _render_reasoning_modes(_parse_reasoning_modes(
            get_setting("reasoning_core", "observe"), rules)))
        object.__setattr__(self, "dependency_lock", "locked" if parse_lock(get_setting("dependency_lock", "locked")) else "unlocked")
        object.__setattr__(self, "gate_write_roots", tuple(
            r.strip() for r in str(get_setting("gate_write_roots", "") or "").split(os.pathsep) if r.strip()))
        object.__setattr__(self, "unattended_auto_approve_tools", tuple(
            t.strip() for t in str(get_setting("unattended_auto_approve_tools", "") or "").split(",") if t.strip()))
        # Plan mode: agent plans only, no tool execution
        object.__setattr__(self, "plan_mode",
            _parse_bool(get_setting("plan_mode", "false"), False)
        )
        # Plan context: approved plan injected into system prompt
        object.__setattr__(self, "plan_context", None)
        # Tokens per character estimate for context budget (4 is conservative for code/text)
        object.__setattr__(self, "chars_per_token",
            _parse_int(get_setting("chars_per_token", "4"), 4, 1, 10)
        )
        # Auto-compaction settings
        object.__setattr__(self, "auto_compact",
            _parse_bool(get_setting("auto_compact", "true"), True)
        )
        object.__setattr__(self, "compact_threshold_tokens",
            _parse_int(get_setting("compact_threshold_tokens", "75"), 75, 10, 95)
        )
        object.__setattr__(self, "compact_keep_recent",
            _parse_int(get_setting("compact_keep_recent", "6"), 6, 4, 50)
        )
        object.__setattr__(self, "compaction_model",
            get_setting("compaction_model", "") or ""
        )
        # Circuit breaker settings
        object.__setattr__(self, "circuit_breaker_failure_threshold",
            _parse_int(get_setting("circuit_breaker_failure_threshold", "5"), 5, 1, 20)
        )
        object.__setattr__(self, "circuit_breaker_success_threshold",
            _parse_int(get_setting("circuit_breaker_success_threshold", "2"), 2, 1, 10)
        )
        object.__setattr__(self, "circuit_breaker_recovery_timeout",
            _parse_float(get_setting("circuit_breaker_recovery_timeout", "30.0"), 30.0, 1.0, 300.0)
        )
        # Concurrency limits
        object.__setattr__(self, "thread_pool_size", _parse_int(
            get_setting("thread_pool_size", "8"), 8, 1, 64
        ))
        object.__setattr__(self, "tool_pool_size", _parse_int(
            get_setting("tool_pool_size", "8"), 8, 1, 64
        ))
        object.__setattr__(self, "tool_pool_network_size", _parse_int(
            get_setting("tool_pool_network_size", "4"), 4, 1, 32
        ))
        object.__setattr__(self, "subagent_pool_size", _parse_int(
            get_setting("subagent_pool_size", "4"), 4, 1, 32
        ))
        object.__setattr__(self, "max_subagent_timeout", _parse_int(
            get_setting("max_subagent_timeout", "600"), 600, 30, 3600
        ))
        object.__setattr__(self, "max_subagent_depth", _parse_int(
            get_setting("max_subagent_depth", "2"), 2, 0, 10
        ))
        object.__setattr__(self, "max_subagent_branching", _parse_int(
            get_setting("max_subagent_branching", "3"), 3, 1, 20
        ))
        # Write-classification tools requiring approval in restricted modes
        raw_write_tools = get_setting(
            "write_tools",
            ["write_file", "edit_file", "run_bash", "git_commit", "git_push",
             "gh_pr_create", "gh_pr_comment", "gh_pr_close", "gh_pr_merge", "git_sync_base", "spawn", "fanout", "spawn_background"],
        )
        if isinstance(raw_write_tools, str):
            object.__setattr__(self, "write_tools",
                [t.strip() for t in raw_write_tools.split(",") if t.strip()]
            )
        elif isinstance(raw_write_tools, list):
            object.__setattr__(self, "write_tools", raw_write_tools)
        else:
            object.__setattr__(self, "write_tools",
                ["write_file", "edit_file", "run_bash", "git_commit",
                 "gh_pr_create", "git_push", "gh_pr_comment", "gh_pr_close", "gh_pr_merge", "git_sync_base", "spawn", "fanout",
                 "spawn_background"]
            )
        # Subagent model fallback priority (tried in order for local subagent execution)
        raw_subagent_models = get_setting(
            "subagent_models",
            ["llama3.2", "llama3.1", "qwen2.5", "phi4", "gemma2"],
        )
        if isinstance(raw_subagent_models, str):
            object.__setattr__(self, "subagent_models",
                [m.strip() for m in raw_subagent_models.split(",") if m.strip()]
            )
        elif isinstance(raw_subagent_models, list):
            object.__setattr__(self, "subagent_models", raw_subagent_models)
        else:
            object.__setattr__(self, "subagent_models",
                ["llama3.2", "llama3.1", "qwen2.5", "phi4", "gemma2"]
            )
        # Context files
        raw_context_files = get_setting(
            "context_files",
            ["wisp.md", "CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"],
        )
        if isinstance(raw_context_files, str):
            object.__setattr__(self, "context_files",
                [f.strip() for f in raw_context_files.split(",") if f.strip()]
            )
        elif isinstance(raw_context_files, list):
            object.__setattr__(self, "context_files", raw_context_files)
        else:
            object.__setattr__(self, "context_files",
                ["wisp.md", "CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"]
            )
        object.__setattr__(self, "loaded_context", "")
        object.__setattr__(self, "_context_mtimes", {})
        object.__setattr__(self, "_last_workspace_for_context", None)
        # Subagent depth/branch tracking for propagation
        object.__setattr__(self, "_subagent_depth", 0)
        object.__setattr__(self, "_subagent_branch_count", 0)
        # Agentic graph loop (outer) — reconciles spec default 5 with turn loop 200
        object.__setattr__(self, "graph_max_iterations",
            _parse_int(get_setting("graph_max_iterations", "5"), 5, 1, 200)
        )
        object.__setattr__(self, "graph_sandbox_timeout",
            _parse_float(get_setting("graph_sandbox_timeout", "120"), 120, 1.0, 3600.0)
        )
        object.__setattr__(self, "graph_oscillation_guard",
            _parse_bool(get_setting("graph_oscillation_guard", "true"), True)
        )
        object.__setattr__(self, "autonomous",
            _parse_bool(get_setting("autonomous", "false"), False)
        )
        # ── Durable runtime layer (migration P0) ──────────────────
        # Off → exactly the pre-migration behavior (no durable rows
        # written, no spans emitted, no tool events journaled).
        object.__setattr__(self, "durable_runs",
            _parse_bool(get_setting("durable_runs", "true"), True)
        )
        object.__setattr__(self, "session_event_fidelity",
            _parse_bool(get_setting("session_event_fidelity", "true"), True)
        )
        object.__setattr__(self, "turn_spans",
            _parse_bool(get_setting("turn_spans", "true"), True)
        )
        object.__setattr__(self, "turn_journal",
            _parse_bool(get_setting("turn_journal", "true"), True)
        )
        object.__setattr__(self, "proposal_boundary",
            _parse_bool(get_setting("proposal_boundary", "true"), True)
        )
        object.__setattr__(self, "record_verdict",
            _parse_bool(get_setting("record_verdict", "false"), False)
        )
        object.__setattr__(self, "task_graph",
            _parse_bool(get_setting("task_graph", "false"), False)
        )
        object.__setattr__(self, "recovery_ladder",
            _parse_bool(get_setting("recovery_ladder", "false"), False)
        )
        object.__setattr__(self, "goal_state",
            _parse_bool(get_setting("goal_state", "false"), False)
        )
        object.__setattr__(self, "stagnation_gate",
            _parse_bool(get_setting("stagnation_gate", "false"), False)
        )
        object.__setattr__(self, "turn_criteria_source",
            _parse_bool(get_setting("turn_criteria_source", "false"), False)
        )
        object.__setattr__(self, "acceptance_gate",
            _parse_bool(get_setting("acceptance_gate", "false"), False)
        )
        object.__setattr__(self, "rest_approval",
            _parse_bool(get_setting("rest_approval", "false"), False)
        )
        object.__setattr__(self, "policy_bundle", get_setting("policy_bundle", ""))
        object.__setattr__(self, "policy_pubkey", get_setting("policy_pubkey", ""))

    def load_context_files(self) -> str:
        """Load and concatenate context files from workspace root.

        Searches for files listed in ``context_files``, plus additional
        locations under ``.wisp/`` (rules.md, conventions.md) and user-home
        config (``~/.config/wisp/CLAUDE.md``).

        Returns concatenated content for injection into system prompt.
        Caches result -- re-reads if workspace changes or file mtimes change.
        """
        workspace = self.workspace or safe_getcwd()
        ws_path = Path(workspace).resolve()

        # Check if cache is valid
        if self._last_workspace_for_context == str(ws_path) and self.loaded_context:
            # Verify no files changed
            stale = False
            for fpath, cached_mtime in list(self._context_mtimes.items()):
                try:
                    current_mtime = Path(fpath).stat().st_mtime
                    if current_mtime != cached_mtime:
                        stale = True
                        break
                except OSError:
                    stale = True
                    break
            if not stale:
                return self.loaded_context

        found_files: list[Path] = []
        mtimes: dict[str, float] = {}

        # 1. Search workspace root for each file in context_files list.
        # `context_files` is env/config-controlled (WISP_CONTEXT_FILES); an
        # absolute or `..`-traversal entry would otherwise read arbitrary
        # files and inject their content into the system prompt sent to the
        # provider. resolve_contained rejects both before any read happens.
        for fname in self.context_files:
            try:
                contained = resolve_contained(str(ws_path), fname, allow_absolute=False)
            except ValueError:
                logger.warning(
                    "context_files entry %r is outside the workspace; skipped", fname
                )
                continue
            candidate = Path(contained)
            if candidate.is_file():
                found_files.append(candidate)

        # 2. Also check .wisp/ directory for convention files
        wisp_dir = ws_path / ".wisp"
        for extra in ("rules.md", "conventions.md"):
            candidate = wisp_dir / extra
            if candidate.is_file() and candidate not in found_files:
                found_files.append(candidate)

        # 3. User home config CLAUDE.md
        user_claude = Path.home() / ".config" / "wisp" / "CLAUDE.md"
        if user_claude.is_file():
            found_files.append(user_claude)

        # 4. Read and concatenate
        blocks: list[str] = []
        for file_path in found_files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                rel = (
                    file_path.relative_to(ws_path)
                    if str(file_path).startswith(str(ws_path))
                    else file_path
                )
                blocks.append(f"## Project Context: {rel}\n{content}\n---\n")
                mtimes[str(file_path)] = file_path.stat().st_mtime
            except Exception as e:
                logger.warning("Failed to read context file %s: %s", file_path, e)

        if blocks:
            object.__setattr__(self, "loaded_context",
                "## Project Context\n\n" + "\n".join(blocks)
            )
        else:
            object.__setattr__(self, "loaded_context", "")

        object.__setattr__(self, "_context_mtimes", mtimes)
        object.__setattr__(self, "_last_workspace_for_context", str(ws_path))
        logger.debug("Loaded %d context file(s) for workspace %s", len(found_files), ws_path)
        return self.loaded_context

    def validate_or_raise(self) -> None:
        """Validate this config instance, raising ValueError on failure."""
        errors = self.validate()
        if errors:
            raise ValueError("Invalid config:\n" + "\n".join(f"  - {e}" for e in errors))

    def validate(self) -> list[str]:
        """Validate this config instance against the schema.

        Returns a list of error messages (empty if valid).
        """
        errors: list[str] = []

        # Temperature
        if not (0.0 <= self.temperature <= 2.0):
            errors.append(
                f"temperature: {self.temperature} is out of range [0.0, 2.0]"
            )

        # Max iterations
        if not (1 <= self.max_iterations <= 200):
            errors.append(
                f"max_iterations: {self.max_iterations} is out of range [1, 200]"
            )

        # Max reflections
        if not (0 <= self.max_reflections <= 10):
            errors.append(
                f"max_reflections: {self.max_reflections} is out of range [0, 10]"
            )

        # Max context tokens
        if self.max_context_tokens < 1024:
            errors.append(
                f"max_context_tokens: {self.max_context_tokens} is below minimum 1024"
            )

        # Chars per token
        if not (1 <= self.chars_per_token <= 10):
            errors.append(
                f"chars_per_token: {self.chars_per_token} is out of range [1, 10]"
            )

        # Compact threshold
        if not (10 <= self.compact_threshold_tokens <= 95):
            errors.append(
                f"compact_threshold_tokens: {self.compact_threshold_tokens} is out of range [10, 95]"
            )

        # Compact keep recent
        if self.compact_keep_recent < 4:
            errors.append(
                f"compact_keep_recent: {self.compact_keep_recent} is below minimum 4"
            )
        if self.compact_keep_recent > 50:
            errors.append(
                f"compact_keep_recent: {self.compact_keep_recent} exceeds maximum 50"
            )

        # Circuit breaker
        if not (1 <= self.circuit_breaker_failure_threshold <= 20):
            errors.append(
                f"circuit_breaker_failure_threshold: {self.circuit_breaker_failure_threshold} is out of range [1, 20]"
            )
        if not (1 <= self.circuit_breaker_success_threshold <= 10):
            errors.append(
                f"circuit_breaker_success_threshold: {self.circuit_breaker_success_threshold} is out of range [1, 10]"
            )
        if not (1.0 <= self.circuit_breaker_recovery_timeout <= 300.0):
            errors.append(
                f"circuit_breaker_recovery_timeout: {self.circuit_breaker_recovery_timeout} is out of range [1.0, 300.0]"
            )

        # Graph loop
        if not (1 <= self.graph_max_iterations <= 200):
            errors.append(f"graph_max_iterations: {self.graph_max_iterations} is out of range [1, 200]")
        if not (1.0 <= self.graph_sandbox_timeout <= 3600.0):
            errors.append(f"graph_sandbox_timeout: {self.graph_sandbox_timeout} is out of range [1.0, 3600.0]")

        # Permission mode
        valid_modes = {m.value for m in PermissionMode}
        if self.permission_mode not in valid_modes:
            errors.append(
                f"permission_mode: '{self.permission_mode}' is not one of {[m.value for m in PermissionMode]}"
            )

        # Provider
        if not self.provider:
            errors.append("provider: cannot be empty")

        # Model: EMPTY IS LEGAL here — the selection contract
        # (provider_catalog.resolve_selection) fills it from the live
        # listing at core-build time. Rejecting empty would force every
        # entrypoint to hardcode a model id, which is exactly how stale
        # defaults were born.

        return errors

    def replace(self, **kwargs: Any) -> "WispConfig":
        """Return a new WispConfig with specified fields replaced.

        Deep-copies mutable containers (dict, list, set) so mutations
        on the new config don't leak back to the original.
        """
        import copy
        new = object.__new__(WispConfig)
        for attr in dir(self):
            if not attr.startswith('__') and not callable(getattr(self, attr, None)):
                try:
                    val = getattr(self, attr)
                    if isinstance(val, (dict, list, set)):
                        val = copy.deepcopy(val)
                    object.__setattr__(new, attr, val)
                except Exception:
                    pass
        # Apply overrides
        for key, value in kwargs.items():
            object.__setattr__(new, key, value)
        return new

    def fingerprint(self) -> str:
        """Return a stable hash of config fields that affect core behavior.

        Changing any of these fields should invalidate the core cache
        because they affect system prompts, tool schemas, or provider.
        """
        parts = [
            str(self.provider),
            str(self.model),
            str(self.temperature),
            str(self.ollama_url),
            str(self.api_base or ""),
            str(self.permission_mode.value if hasattr(self.permission_mode, "value") else self.permission_mode),
            str(self.autonomous),
            str(self.capability_filtering),
            str(self.show_thinking),
            str(self.workspace or ""),
        ]
        data = "|".join(parts)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def __repr__(self) -> str:
        return (
            f"WispConfig(provider={self.provider}, ollama_url={self.ollama_url}, "
            f"api_base={self.api_base}, model={self.model}, "
            f"workspace={self.workspace})"
        )
