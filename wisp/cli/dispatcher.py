"""Decoupled slash-command dispatcher for the REPL.

Design (Phase: REPL composition-root refactor):
  - Commands interact with :class:`ReplContext` — a small public-surface
    record (runtime, transport, session, config, output sink) — instead of
    reaching into REPL-wrapper privates (``adapter._loop``,
    ``transport._render_event``, ``runtime._get_core``).
  - :class:`Dispatcher` maps input strings to handlers. Unknown commands
    are consumed with a hint (never fall through to the LLM). Handler
    exceptions are contained and rendered; only explicit exit propagates.
  - The legacy ``wisp.repl.commands`` registry stays authoritative for
    unmigrated commands: unknown-here names fall back to it via an
    adapter, so no command is lost in migration.

Strictly typed, no placeholders, stdlib + wisp.colors only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


class CommandResult(Enum):
    """Outcome of dispatching one input line."""

    CONSUMED = "consumed"  # handled; read the next line
    FOLLOWUP = "followup"  # run ctx.followup as a model prompt (see /expand)
    EXIT = "exit"  # leave the REPL
    PASSTHROUGH = "passthrough"  # not a slash command; run a turn


class RuntimeLike(Protocol):
    """Public runtime surface the dispatcher may use."""

    async def get_or_create_session(self, session_id: str, model: str, workspace: str) -> dict: ...
    def get_doctor_report(self) -> Any: ...


class TransportLike(Protocol):
    """Public transport surface the dispatcher may use."""

    def render(self, event: dict) -> None: ...


@dataclass
class ReplContext:
    """Everything a slash command may touch. No privates beyond this."""

    runtime: Any
    transport: Any
    session: dict
    config: Any
    out: list[str] = field(default_factory=list)
    followup: str = ""
    # Legacy-compat bridge: the transport-layer adapter handed to
    # unmigrated handlers. New handlers must ignore it.
    adapter: Any = None

    def emit(self, text: str) -> None:
        self.out.append(text)

    @property
    def session_id(self) -> str:
        return str(self.session.get("id", ""))


Handler = Callable[["ReplContext", str], "CommandResult"]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    usage: str = ""


class Dispatcher:
    """Name → handler router with legacy-registry fallback."""

    def __init__(self, legacy_dispatch: Callable[[str, Any], Any] | None = None) -> None:
        """Args:
            legacy_dispatch: ``fn(text, adapter)`` for unmigrated commands
                (e.g. ``wisp.commands.dispatch``). Follows the legacy
                contract: True consumed, str follow-up, False passthrough,
                ExitREPL to exit. None → thin-adapter registry fallback.
        """
        self._handlers: dict[str, Handler] = {}
        self._specs: dict[str, CommandSpec] = {}
        self._legacy_dispatch = legacy_dispatch
        self._register_builtins()

    def register(self, name: str, description: str, usage: str = "") -> Callable[[Handler], Handler]:
        """Decorator registering a handler under ``name`` (leading '/' optional)."""

        def decorator(fn: Handler) -> Handler:
            key = name.lstrip("/").split()[0]
            if key in self._handlers:
                raise ValueError(f"command /{key} already registered")
            self._handlers[key] = fn
            self._specs[key] = CommandSpec(name=key, description=description, usage=usage)
            return fn

        return decorator

    def names(self) -> list[str]:
        return sorted(self._handlers)

    def dispatch(self, ctx: ReplContext, text: str) -> CommandResult:
        """Route one input line. Never raises (except KeyboardInterrupt)."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return CommandResult.PASSTHROUGH
        body = stripped[1:].strip()
        if not body:
            return self._run_handler(ctx, "help", "")
        parts = body.split(maxsplit=1)
        name, args = parts[0], (parts[1] if len(parts) > 1 else "")
        handler = self._handlers.get(name)
        if handler is None:
            return self._dispatch_legacy(ctx, name, args)
        return self._run_handler(ctx, name, args, handler)

    def _run_handler(self, ctx: ReplContext, name: str, args: str, handler: Handler | None = None) -> CommandResult:
        fn = handler or self._handlers[name]
        try:
            result = fn(ctx, args.strip())
            return result if isinstance(result, CommandResult) else CommandResult.CONSUMED
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.exception("Slash command /%s failed", name)
            ctx.emit(f"Error: {exc}")
            return CommandResult.CONSUMED

    def _dispatch_legacy(self, ctx: ReplContext, name: str, args: str) -> CommandResult:
        """Fall back to unmigrated commands (strangler-fig pattern).

        Preferred path: the injected ``legacy_dispatch`` callable with the
        real transport-layer adapter, preserving byte-for-byte behavior of
        handlers that still need adapter internals. Without an adapter the
        thin read-only bridge is used; unknown names are consumed with a
        hint (they must never reach the LLM).
        """
        from wisp.exceptions import ExitREPL

        if self._legacy_dispatch is not None and ctx.adapter is not None:
            try:
                result = self._legacy_dispatch(f"/{name}" + (f" {args}" if args else ""), ctx.adapter)
            except ExitREPL:
                return CommandResult.EXIT
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                logger.exception("Legacy slash command /%s failed", name)
                ctx.emit(f"Error: {exc}")
                return CommandResult.CONSUMED
            self._sync_adapter(ctx)
            if isinstance(result, str) and result:
                ctx.followup = result
                return CommandResult.FOLLOWUP
            return CommandResult.CONSUMED
        try:
            from wisp.repl.commands import lookup as _legacy_lookup
        except ImportError:
            _legacy_lookup = None  # type: ignore[assignment]
        cmd = _legacy_lookup(name) if _legacy_lookup is not None else None
        if cmd is None:
            ctx.emit(f"Unknown command: /{name}. Type /help for available commands.")
            return CommandResult.CONSUMED
        try:
            adapter = _LegacyAdapter(ctx)
            result = cmd.handler(adapter, args.strip())
            ctx.session = adapter.session
            if isinstance(result, str) and result:
                ctx.followup = result
                return CommandResult.FOLLOWUP
            return CommandResult.CONSUMED
        except ExitREPL:
            return CommandResult.EXIT
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.exception("Legacy slash command /%s failed", name)
            ctx.emit(f"Error: {exc}")
            return CommandResult.CONSUMED

    @staticmethod
    def _sync_adapter(ctx: ReplContext) -> None:
        """Re-share adapter state commands may have rebound (frozen replace())."""
        adapter = ctx.adapter
        if adapter is None:
            return
        try:
            session = getattr(adapter, "session", None)
            if isinstance(session, dict) and session is not ctx.session:
                ctx.session = session
        except Exception:
            pass
        try:
            config = getattr(adapter, "config", None)
            if config is not None and config is not ctx.config:
                ctx.config = config
        except Exception:
            pass

    # ── Built-in commands (public interfaces only) ────────────────────

    def _register_builtins(self) -> None:
        @self.register("help", "Show available commands", usage="/help")
        def _help(ctx: ReplContext, args: str) -> CommandResult:
            lines = ["Available commands:"]
            for key in self.names():
                spec = self._specs[key]
                lines.append(f"  /{spec.name:<12} {spec.description}")
            lines.append("  (more: /doctor reports legacy commands via fallback)")
            ctx.emit("\n".join(lines))
            return CommandResult.CONSUMED

        @self.register("doctor", "Show pre-flight / subsystem health", usage="/doctor")
        def _doctor(ctx: ReplContext, args: str) -> CommandResult:
            report = ctx.runtime.get_doctor_report()
            if isinstance(report, dict):
                status = "healthy" if report.get("healthy") else "degraded"
                ctx.emit(f"Doctor: {report.get('passed', '?')}/{report.get('total', '?')} ok · {status}")
                for check in report.get("checks", [])[:10]:
                    ctx.emit(f"  {check}")
            else:
                ctx.emit(str(report))
            return CommandResult.CONSUMED

        @self.register("provider", "Show active provider and model", usage="/provider")
        def _provider(ctx: ReplContext, args: str) -> CommandResult:
            if isinstance(ctx.config, dict):
                provider = ctx.config.get("provider", "?")
                model = ctx.config.get("model", "?")
            else:
                provider = getattr(ctx.config, "provider", "?")
                model = getattr(ctx.config, "model", "?")
            ctx.emit(f"Provider: {provider} · model: {model}")
            return CommandResult.CONSUMED

        @self.register("model", "Show active model", usage="/model [name]")
        def _model(ctx: ReplContext, args: str) -> CommandResult:
            if args:
                ctx.emit("Model switching happens via the legacy /model handler.")
                return self._dispatch_legacy(ctx, "model", args)
            if isinstance(ctx.config, dict):
                ctx.emit(f"Model: {ctx.config.get('model', '?')}")
            else:
                ctx.emit(f"Model: {getattr(ctx.config, 'model', '?')}")
            return CommandResult.CONSUMED

        @self.register("expand", "Expand the last answer into a follow-up prompt", usage="/expand")
        def _expand(ctx: ReplContext, args: str) -> CommandResult:
            messages = ctx.session.get("messages", [])
            last_assistant = next(
                (m.get("content", "") for m in reversed(messages) if m.get("role") == "assistant"), ""
            )
            if not last_assistant:
                ctx.emit("Nothing to expand yet — ask something first.")
                return CommandResult.CONSUMED
            ctx.followup = (
                "Continue and expand on your previous answer with more detail, "
                "covering anything you left out."
            )
            return CommandResult.FOLLOWUP

        def _exit(ctx: ReplContext, args: str) -> CommandResult:
            return CommandResult.EXIT

        self.register("exit", "Leave the REPL", usage="/exit")(_exit)
        self.register("quit", "Leave the REPL")(_exit)


class _LegacyAdapter:
    """Minimal bridge letting legacy handlers run against a ReplContext.

    Exposes the attributes legacy handlers commonly read (session, config,
    runtime, messages). Anything deeper raises AttributeError loudly so
    private-reaching handlers are found, not silently accommodated.
    """

    def __init__(self, ctx: ReplContext) -> None:
        object.__setattr__(self, "_ctx", ctx)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        ctx = object.__getattribute__(self, "_ctx")
        if name == "session":
            return ctx.session
        if name == "config":
            return ctx.config
        if name == "runtime":
            return ctx.runtime
        if name == "messages":
            return ctx.session.get("messages", [])
        raise AttributeError(f"legacy adapter has no attribute {name!r} (private access denied)")

    def __setattr__(self, name: str, value: Any) -> None:
        ctx = object.__getattribute__(self, "_ctx")
        if name in ("session", "config"):
            # ReplContext is a mutable dataclass: plain assignment suffices.
            object.__setattr__(ctx, name, value)
            return
        raise AttributeError(f"legacy adapter is read-only for {name!r}")
