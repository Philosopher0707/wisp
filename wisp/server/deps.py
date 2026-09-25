"""FastAPI dependencies for Wisp server.

Extracted from monolithic server.py:
  - verify_api_key: API key authentication
  - rate_limiter: SQLite-backed rate limiting
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from fastapi import Header, HTTPException, Request

from wisp.infra.audit import audit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════════

_AUTH_KEY_GRACE_SECONDS = 86_400  # 24h default key rotation grace period


class _AuthConfig:
    """Runtime-mutable auth key with rotation support.

    Supports up to one previous key during rotation with a grace period.
    Keys are persisted to disk (~/.config/wisp/auth_keys.json) so
    rotation grace windows survive server restarts.
    """

    _persist_path: Path = Path.home() / ".config" / "wisp" / "auth_keys.json"

    def __init__(self):
        self._key: str = os.environ.get("WISP_API_KEY", "")
        self._no_auth: bool = False
        self._valid_keys: dict[str, float | None] = {}

        # Load persisted keys first; env overrides
        self._load_from_disk()

        if self._key:
            self._valid_keys.setdefault(self._key, None)
            logger.info("WISP_API_KEY set — server requires authentication")
        else:
            self._no_auth = True
            logger.info("WISP_API_KEY not set — authentication disabled (dev mode)")
        self._save_to_disk()

    # ── Persistence ─────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        if not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self._valid_keys = {k: v for k, v in data.items()}
            # Filter expired keys
            now = time.time()
            expired = [
                k for k, expiry in self._valid_keys.items()
                if expiry is not None and now > expiry
            ]
            for k in expired:
                del self._valid_keys[k]
            if expired:
                logger.info("Removed %d expired key(s) on load", len(expired))
        except Exception:
            logger.warning("Failed to load persisted keys, starting fresh")

    def _save_to_disk(self) -> None:
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(self._valid_keys, indent=2), encoding="utf-8"
            )
            self._persist_path.chmod(0o600)  # Owner read/write only
        except Exception:
            logger.warning("Failed to persist auth keys to disk")

    # ── Mutation ──────────────────────────────────────────────────

    def set_key(self, key: str) -> None:
        """Replace the current key immediately (no rotation grace)."""
        self._key = key
        self._no_auth = not bool(key)
        self._valid_keys = {key: None} if key else {}
        self._save_to_disk()
        audit.record("key_set", key="api_key")

    def rotate_key(self, new_key: str, grace_seconds: int = _AUTH_KEY_GRACE_SECONDS) -> None:
        """Rotate to a new key while keeping the current one valid for a grace period."""
        old_key = self._key
        now = time.time()
        # Update new key as primary (no expiry)
        self._key = new_key
        self._no_auth = not bool(new_key)
        # Set expiry for old key if it exists and differs
        if old_key and old_key != new_key:
            self._valid_keys[old_key] = now + grace_seconds
            logger.info("Key rotation: old key expires in %d seconds", grace_seconds)
        # Add new key with no expiry, or clear if empty
        if new_key:
            self._valid_keys[new_key] = None
        if not new_key:
            self._valid_keys.clear()
        self._save_to_disk()
        audit.record("key_rotation", key="api_key",
                      new_value=new_key[:4] + "..." if new_key else None)

    def disable(self) -> None:
        self._key = ""
        self._no_auth = True
        self._valid_keys.clear()
        logger.info("Auth disabled (no-auth mode)")
        audit.record("auth_disabled", key="api_key")
        self._save_to_disk()

    def _is_valid(self, candidate: str) -> bool:
        """Check if a candidate key is valid (current or within grace period)."""
        if self._no_auth:
            return True
        now = time.time()
        # Clean expired keys lazily
        expired = [
            k for k, expiry in self._valid_keys.items()
            if expiry is not None and now > expiry
        ]
        for k in expired:
            del self._valid_keys[k]
            logger.info("Rotated key expired and removed")
        if expired:
            self._save_to_disk()
        return candidate in self._valid_keys or candidate == self._key

    @property
    def key(self) -> str:
        return self._key

    @property
    def required(self) -> bool:
        return not self._no_auth and bool(self._key)

    # String back-compat
    def __str__(self) -> str:
        return self._key

    def __eq__(self, other):
        if isinstance(other, str):
            return self._key == other
        if isinstance(other, _AuthConfig):
            return self._key == other._key
        return NotImplemented

    def __bool__(self):
        return bool(self._key)


_auth = _AuthConfig()
API_KEY = _auth
API_KEY_STR = _auth.key


async def verify_api_key(
    x_api_key_header: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
):
    """API key verification via header only (query param removed — leaks to logs)."""
    if not _auth.required:
        return x_api_key_header or authorization or ""
    if authorization and authorization.lower().startswith("bearer "):
        auth_key = authorization[7:]
        if _auth._is_valid(auth_key):
            return auth_key
    if x_api_key_header and _auth._is_valid(x_api_key_header):
        return x_api_key_header
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ═══════════════════════════════════════════════════════════════════
# Rate Limiting
# ═══════════════════════════════════════════════════════════════════

class SQLiteRateLimiter:
    """Cross-process rate limiter backed by SQLite."""

    def __init__(self, *, db_path: Path, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self._disabled: bool = False
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            logger.warning("SQLiteRateLimiter: cannot create directory %s — rate limiting disabled", self.db_path.parent)
            self._disabled = True
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rate_limits (
                        client_ip TEXT PRIMARY KEY,
                        timestamps TEXT NOT NULL DEFAULT '[]',
                        updated_at REAL NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_rate_updated ON rate_limits(updated_at)"
                )
        except (sqlite3.OperationalError, PermissionError, OSError):
            logger.warning("SQLiteRateLimiter: cannot initialize DB — rate limiting disabled")
            self._disabled = True

    def _get_timestamps(self, client_ip: str) -> list[float]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT timestamps FROM rate_limits WHERE client_ip = ?",
                (client_ip,),
            ).fetchone()
        if row is None:
            return []
        try:
            return json.loads(row["timestamps"])
        except (json.JSONDecodeError, TypeError):
            return []

    def _set_timestamps(self, client_ip: str, timestamps: list[float]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rate_limits (client_ip, timestamps, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(client_ip) DO UPDATE SET
                    timestamps = excluded.timestamps,
                    updated_at = excluded.updated_at
                """,
                (client_ip, json.dumps(timestamps), time.time()),
            )

    def _prune_old(self, timestamps: list[float]) -> list[float]:
        cutoff = time.time() - self.window_seconds
        return [t for t in timestamps if t > cutoff]

    def is_allowed(self, client_ip: str) -> bool:
        if getattr(self, "_disabled", False):
            return True
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT timestamps FROM rate_limits WHERE client_ip = ?",
                    (client_ip,),
                ).fetchone()
                timestamps = []
                if row is not None:
                    try:
                        timestamps = json.loads(row["timestamps"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                timestamps = self._prune_old(timestamps)
                if len(timestamps) >= self.max_requests:
                    conn.execute("ROLLBACK")
                    return False
                timestamps.append(time.time())
                conn.execute(
                    """
                    INSERT INTO rate_limits (client_ip, timestamps, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(client_ip) DO UPDATE SET
                        timestamps = excluded.timestamps,
                        updated_at = excluded.updated_at
                    """,
                    (client_ip, json.dumps(timestamps), time.time()),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    async def __call__(self, request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not self.is_allowed(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")


# ═══════════════════════════════════════════════════════════════════
# Lazy singletons (avoid import-time side effects in sandboxed/tests)
# ═══════════════════════════════════════════════════════════════════

_auth_instance: _AuthConfig | None = None
_rate_limiter_instance: SQLiteRateLimiter | None = None


def get_auth() -> _AuthConfig:
    """Return the singleton _AuthConfig, creating it on first use."""
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = _AuthConfig()
    return _auth_instance


def get_rate_limiter() -> SQLiteRateLimiter:
    """Return the singleton SQLiteRateLimiter, creating it on first use."""
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = SQLiteRateLimiter(
            db_path=Path.home() / ".config" / "wisp" / "rate_limits.db",
            max_requests=30,
            window_seconds=60,
        )
    return _rate_limiter_instance


class _LazyAuthProxy:
    """Proxy that delays _AuthConfig instantiation until first attribute access."""

    def __getattr__(self, name: str):
        return getattr(get_auth(), name)

    def __setattr__(self, name: str, value) -> None:
        if name.startswith("__"):
            super().__setattr__(name, value)
        else:
            setattr(get_auth(), name, value)

    def __delattr__(self, name: str) -> None:
        if name.startswith("__"):
            super().__delattr__(name)
        else:
            delattr(get_auth(), name)


class _LazyRateLimiterProxy:
    """Proxy that delays SQLiteRateLimiter instantiation until first call."""

    async def __call__(self, request: Request) -> None:
        return await get_rate_limiter()(request)


# Public module-level names (backward compatible)
_auth = _LazyAuthProxy()
RATE_LIMITER = _LazyRateLimiterProxy()


# ── REST tool policy gate (C3b) ──────────────────────────────────────

def request_policy(request: Request):
    """Policy for REST tool execution: the server root's configured mode.

    Falls back to a default policy when the route is mounted without a
    composition root (tests, embeddings). Never raises.
    """
    from wisp.infra.security import SecurityPolicy

    try:
        state = getattr(getattr(request, "app", None), "state", None)
        mode = getattr(getattr(getattr(state, "root", None), "config", None),
                       "permission_mode", None)
        if mode is not None:
            return SecurityPolicy(permission_mode=mode)
    except Exception:
        pass
    return SecurityPolicy()


def require_tool_allowed(request: Request, action_name: str, args: dict,
                         workspace: str | Path) -> None:
    """Fail-closed policy gate for REST tool execution. Raises 403.

    REST has no human to approve, so approval-required verdicts deny —
    same as ApprovalGate with no handler. Call BEFORE any side effect.

    **What that sentence covers, and what it does not (ADR-0055).** It
    describes the verdict this gate *consumes*: `SecurityPolicy.check()`.
    For the executable-config routes (`hooks.create`, `mcp.add_server`,
    `plugins.install`) `SecurityPolicy` reports no approval requirement in
    any mode, so the clause cannot fire for them — and it does not need to,
    because **the agent has no equivalent operation**: those three names are
    not agent tools and have no row in `TOOL_RISK_TABLE`. Measured, the two
    paths reach the same outcome on **every** route in **every** mode once
    the agent is driven under REST's own condition (no approver); see
    `scripts/authorization_parity_measurement.py`. The control on those
    routes is therefore the API key, the `read_only` denial and the
    protected-path guard below — **not** an approval prompt.

    Two checks, in order:

    1. **Protected-path guard** — a non-read action whose target lies in a
       directory Wisp itself executes from (`.wisp/hooks`) is refused. The
       agent tool path has enforced this since M2, but REST did not: the
       policy engine is mode-based and has no argument scan, so
       `POST /api/files` could write a hook that the equivalent
       `write_file` tool call was refused. The predicate is the canonical
       `wisp.pathsec.is_protected_path`, so all three paths agree.
    2. **Policy verdict** — the session's permission mode.

    Order matters only for the message: the guard is unconditional, so it
    denies even in `full` mode.
    """
    from wisp.core.contracts import ToolRisk, risk_for_tool
    from wisp.infra.security import Action, Context
    from wisp.pathsec import PATH_BEARING_ARGS, is_protected_path

    args = dict(args or {})
    if risk_for_tool(action_name) != ToolRisk.READ and any(
        is_protected_path(str(value))
        for key, value in args.items()
        if key in PATH_BEARING_ARGS and value
    ):
        logger.warning("rest_tool_gate_protected_path action=%s args=%s",
                       action_name, sorted(args))
        raise HTTPException(
            status_code=403,
            detail="Blocked by the protected-path guard: refusing to mutate a "
                   "directory Wisp executes from (.wisp/hooks)",
        )

    policy = request_policy(request)
    decision = policy.check(
        Action(name=action_name, args=args),
        Context(workspace=Path(workspace)),
    )
    logger.info("rest_tool_gate action=%s allowed=%s approval_required=%s reason=%s",
                action_name, decision.allowed, decision.approval_required,
                (decision.reason or "")[:200])
    if not decision.allowed or decision.approval_required:
        raise HTTPException(
            status_code=403,
            detail=f"Blocked by server policy ({decision.reason or action_name}); "
                   f"no approver is present over REST",
        )


# ── REST approval through the WebSocket channel (ADR-0057) ───────────

def configured_permission_mode(request: Request) -> str:
    """The mode the REST gate is evaluating under — the same source as `request_policy`."""
    try:
        state = getattr(getattr(request, "app", None), "state", None)
        mode = getattr(getattr(getattr(state, "root", None), "config", None),
                       "permission_mode", None)
        if mode is not None:
            return str(getattr(mode, "value", mode))
    except Exception:
        pass
    return "auto_edit"


def rest_approval_enabled(request: Request) -> bool:
    """True when `WISP_REST_APPROVAL` is on for this server root."""
    try:
        root = request.app.state.root
    except Exception:
        return False
    return bool(getattr(getattr(root, "config", None), "rest_approval", False))


def approval_bridge(request: Request):
    """The root's `ApprovalBridge`, or None when the root has none (tests, embeddings)."""
    try:
        root = request.app.state.root
    except Exception:
        return None
    return getattr(root, "approval_bridge", None)


async def require_rest_approval(request: Request, action_name: str, args: dict,
                                workspace: str | Path) -> None:
    """Ask a connected client to approve an executable-config action. Raises 403 on deny.

    **A separate, async companion to `require_tool_allowed`, which keeps its signature
    and its behaviour.** Routes call this *after* the policy gate, so a mode that denies
    outright (`read_only`) never prompts.

    Flag-gated: with `WISP_REST_APPROVAL` **OFF** (the default) this returns immediately
    and every caller sees today's behaviour exactly. ON, an action in
    `approval_bridge.REST_APPROVAL_ACTIONS` in a mode in `REST_APPROVAL_MODES` is asked
    over the WebSocket channel. **No connected client ⇒ 403** — the no-client case must
    not hang and must not silently allow.
    """
    from wisp.server.approval_bridge import action_requires_rest_approval

    if not rest_approval_enabled(request):
        return
    if not action_requires_rest_approval(action_name, configured_permission_mode(request)):
        return

    bridge = approval_bridge(request)
    if bridge is None:
        logger.warning("rest_approval_no_bridge action=%s", action_name)
        raise HTTPException(
            status_code=403,
            detail="Blocked: this action requires approval over the WebSocket channel, "
                   "and this server has no approval bridge",
        )

    approved = await bridge.request_approval(
        name=action_name,
        arguments=dict(args or {}),
        reason=f"{action_name} registers something Wisp will execute",
    )
    logger.info("rest_approval action=%s approved=%s", action_name, approved)
    if not approved:
        raise HTTPException(
            status_code=403,
            detail=f"Blocked: {action_name} requires a human approval and none was "
                   f"given (no client connected, declined, or timed out after "
                   f"{bridge.timeout_s:.0f}s)",
        )

# Lazy exports for API_KEY compatibility
API_KEY = _auth


class _APIKeyStr:
    def __str__(self):
        return get_auth().key

    def __repr__(self):
        return repr(get_auth().key)


API_KEY_STR = _APIKeyStr()
