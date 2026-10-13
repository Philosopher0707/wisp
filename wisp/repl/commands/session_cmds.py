"""Session lifecycle commands: /compact, /sessions. Split from
wisp/commands.py (back-compat shim)."""

import logging

from wisp.colors import success, error, warning, info, dim, accent
from wisp.repl.commands import register

logger = logging.getLogger(__name__)


@register("compact", "Compact session history to save context", usage="/compact")
def cmd_compact(agent, args: str):
    if agent.session is None:
        print(warning("⚠ No active session to compact."))
        return

    msg_count = len(agent.messages)
    if msg_count <= 10:
        print(dim(f"Session has only {msg_count} messages — not enough to compact."))
        return

    print(info(f"Compacting session ({msg_count} messages)..."))

    # Use the runtime's Compactor (LLM summarization) if available.
    # AgentAdapter carries the REPL's event loop for synchronous compaction.
    loop = getattr(agent, '_loop', None)

    if hasattr(agent, 'runtime') and hasattr(agent.runtime, 'maybe_compact') and loop is not None \
            and isinstance(agent.session, dict):
        # The session itself, not a copy: the next turn runs on `agent.session`, so compacting a
        # copy left it with the whole history (and `agent.messages` on a detached list).
        try:
            before = len(agent.session.get("messages", []))
            result = loop.run_until_complete(
                agent.runtime.maybe_compact(agent.session, force=True),
            )
            agent.messages = agent.session["messages"]
            if result and result.get("compacted"):
                after = len(agent.messages)
                print(success(f"✓ Compacted: {before} → {after} messages ({before - after} removed)"))
                if result.get("summary"):
                    print(dim(f"  Summary: {result['summary'][:120]}..."))
            else:
                print(dim("Compaction skipped: nothing older than the recent messages to summarize."))
        except Exception as exc:
            logger.warning("Compaction failed, falling back to truncation: %s", exc, exc_info=True)
            print(warning(f"⚠ Compaction failed ({exc}); keeping the most recent messages instead."))
            _compact_truncate(agent)
    else:
        _compact_truncate(agent)


def _compact_truncate(agent):
    """Fallback compaction: keep the system messages and the most recent ones.

    The kept tail never starts on a tool result: one whose assistant tool call was cut away is
    a history strict providers reject, so the next turn failed after a fallback.
    """
    keep_recent = getattr(agent.config, 'compact_keep_recent', 10)
    messages = agent.session["messages"] if isinstance(agent.session, dict) else agent.messages
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    if len(rest) <= keep_recent:
        print(dim(f"Session has only {len(messages)} messages — not enough to compact."))
        return
    start = len(rest) - keep_recent
    while start < len(rest) and rest[start].get("role") == "tool":
        start += 1
    before = len(messages)
    messages[:] = system + rest[start:]
    agent.messages = messages
    print(success(f"✓ Truncated: {before} → {len(messages)} messages ({before - len(messages)} removed)"))


# ── Session control surface (REPL design R5) ─────────────────────────


def _fmt_k(n: int) -> str:
    if n >= 1024:
        return f"{n / 1024:.0f}k"
    return str(n)


@register("sessions", "List saved sessions", aliases=("ss",), usage="/sessions")
def cmd_sessions(agent, args: str):
    store = getattr(getattr(agent, "runtime", None), "store", None)
    if store is None:
        print(warning("No session store available."))
        return
    try:
        rows = store.list_sessions(limit=10)
    except Exception as e:
        print(error(f"✗ Could not list sessions: {e}"))
        return
    if not rows:
        print(info("No saved sessions yet."))
        return
    print(accent("Saved sessions (newest first):"))
    for r in rows:
        sid = str(r.get("id", "?"))
        short = sid.split("-")[0] if "-" in sid else sid[:8]
        title = r.get("title") or "(untitled)"
        model = r.get("model", "?")
        msgs = r.get("msg_count", 0)
        updated = str(r.get("updated_at", ""))[:16].replace("T", " ")
        print(f"  {short} · {model} · {msgs} msgs · {title}{dim(f' · {updated}')}")
    print(dim("Resume with: wisp repl --session <full-id>"))
