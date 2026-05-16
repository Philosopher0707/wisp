"""Search tools for Wisp — symbol search and semantic codebase search.

Provides code index search (regex-based) and semantic search (embedding-based).
"""

import logging

from wisp.tools._utils import (
    _validate_string,
    _validate_int,
)

logger = logging.getLogger(__name__)


def tool_search_symbols(query: str, workspace: str = ".", max_results: int = 20) -> str:
    """Search the code index for symbols matching a query.

    Builds a lightweight index of function/class/struct definitions in the
    workspace and searches it for the given query. Results include file path,
    line number, and symbol kind.
    """
    _validate_string(query, "query", 200)
    max_results = _validate_int(max_results, "max_results", 1, 100)

    from wisp.code_index import build_index, search_symbols

    index = build_index(workspace)
    if index.total_symbols == 0:
        return "(no symbols found — no source files indexed)"

    results = search_symbols(index, query, max_results=max_results)

    if not results:
        return f"(no symbols matching '{query}' — {index.total_symbols} symbols indexed)"

    lines = [f"Found {len(results)} symbol(s) matching '{query}':", ""]
    for sym in results:
        parent_info = f" (in {sym.parent})" if sym.parent else ""
        lines.append(f"  {sym.kind:12s} {sym.name}{parent_info}")
        lines.append(f"  {'':12s} 📍 {sym.file}:{sym.line}")
        lines.append("")

    if len(results) == max_results:
        lines.append(f"... and more (showing top {max_results})")

    return "\n".join(lines)


def tool_search_codebase(query: str, top_k: int = 5, workspace: str = ".") -> str:
    """Semantic search over the codebase using embedding similarity."""
    try:
        from wisp.semantic_index import SemanticIndex
        index = SemanticIndex(workspace)
        results = index.search(query, top_k=top_k)
        if not results:
            return f"No semantically relevant code found for: {query}"
        lines = [f"Semantic search results for '{query}':"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n{i}. {r.file_path}:{r.start_line}-{r.end_line} "
                         f"(score: {r.score:.3f})"
                         f"{' [' + r.symbol_name + ']' if r.symbol_name else ''}")
            content_lines = r.content.split("\n")[:4]
            for cl in content_lines:
                lines.append(f"   | {cl[:120]}")
        return "\n".join(lines)
    except ImportError:
        return "Semantic index module not available. Install: pip install wisp[semantic]"
    except Exception as e:
        return f"Semantic search error: {e}"
