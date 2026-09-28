"""Every path an app serves, whichever way FastAPI stores included routers.

FastAPI 0.140+ keeps an included router as one `_IncludedRouter` entry in
`app.routes` instead of copying its routes in, so `r.path for r in app.routes`
no longer sees them. Walking the nesting works on both shapes.
"""
from __future__ import annotations

from typing import Any, Iterable


def route_paths(routes: Iterable[Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            paths += route_paths(included.routes, prefix + route.include_context.prefix)
        elif hasattr(route, "path"):
            paths.append(prefix + route.path)
    return paths
