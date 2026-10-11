"""Dashboard command: /dashboard serves the local read-only dashboard from this session, with the Live tab listing every session running right now.
Split from wisp/commands.py (back-compat shim)."""

import logging

from wisp.colors import dim, error, info, success
from wisp.repl.commands import register

logger = logging.getLogger(__name__)


@register("dashboard", "Serve the local dashboard (accuracy, live sessions) from this session", usage="/dashboard [stop|status]")
def cmd_dashboard(agent, args: str):
    import wisp.dashboard.embed as embed

    sub = (args or "").strip().lower()
    if sub == "stop":
        print(success("✓ Dashboard stopped.") if embed.stop() else dim("This session is not serving a dashboard."))
        return
    if sub == "status":
        st = embed.status()
        print(info(f"Serving at {embed.url(st['port'])}") if st["running"] else dim("Not serving from this session. /dashboard starts it."))
        return
    if sub:
        print(error("Usage: /dashboard [stop|status]"))
        return
    result = embed.start()
    if result.get("error"):
        print(error(f"✗ Dashboard not started: {result['error']}"))
    elif result["started"]:
        print(success(f"✓ Dashboard live at {embed.url(result['port'])}"))
        print(dim("  Read-only, this machine only. The Live tab lists the sessions running now, with the model each one got from its environment."))
    else:
        print(info(f"Dashboard already running ({result['owner']}): {embed.url(result['port'])}"))
