"""Tree-sitter based code index — accurate symbol extraction using real parsers.

Uses tree-sitter grammars for accurate syntax-aware symbol extraction.
Falls back to regex-based extraction when tree-sitter is not available.

Requires: pip install tree-sitter tree-sitter-python tree-sitter-rust
          tree-sitter-javascript tree-sitter-typescript tree-sitter-go
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import tree-sitter
_HAVE_TREE_SITTER = False
try:
    import tree_sitter
    _HAVE_TREE_SITTER = True
except ImportError:
    logger.info("tree-sitter not installed, falling back to regex-based code index")
    tree_sitter = None  # type: ignore

# Try to import language grammars
_LANGUAGE_PARSERS: dict[str, object] = {}
if _HAVE_TREE_SITTER:
    _TS_LANG_MODULES = {
        "python": ("tree_sitter_python", "language"),
        "rust": ("tree_sitter_rust", "language"),
        "javascript": ("tree_sitter_javascript", "language"),
        "typescript": ("tree_sitter_typescript", "language_typescript"),
        "go": ("tree_sitter_go", "language"),
        "ruby": ("tree_sitter_ruby", "language"),
    }
    for lang_name, (module_name, attr_name) in _TS_LANG_MODULES.items():
        try:
            mod = __import__(module_name)
            lang_func = getattr(mod, attr_name)
            # Wrap the PyCapsule in a tree_sitter.Language object
            lang_capsule = lang_func()
            _LANGUAGE_PARSERS[lang_name] = tree_sitter.Language(lang_capsule)
            logger.debug("Loaded tree-sitter grammar for %s", lang_name)
        except ImportError:
            logger.debug("tree-sitter grammar for %s not available", lang_name)

# ── Re-export from code_index for fallback ───────────────────────────

from wisp.code_index import (
    Symbol,
    CodeIndex,
    search_symbols,       # noqa: F401
    format_index_summary,   # noqa: F401
    _MAX_SCAN_FILES,
    _MAX_FILE_LINES,
    _EXTENSIONS,            # noqa: F401
)

# ── Tree-sitter query patterns for each language ─────────────────────

_TS_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @name) @function
        (class_definition name: (identifier) @name) @class
    """,
    "rust": """
        (function_item name: (identifier) @name) @function
        (struct_item name: (type_identifier) @name) @struct
        (enum_item name: (type_identifier) @name) @enum
        (trait_item name: (type_identifier) @name) @trait
        (impl_item) @impl
        (type_item name: (type_identifier) @name) @type
        (const_item name: (identifier) @name) @const
    """,
    "javascript": """
        (function_declaration name: (identifier) @name) @function
        (class_declaration name: (identifier) @name) @class
        (method_definition name: (property_identifier) @name) @method
    """,
    "typescript": """
        (function_declaration name: (identifier) @name) @function
        (class_declaration name: (type_identifier) @name) @class
        (method_definition name: (property_identifier) @name) @method
        (interface_declaration name: (type_identifier) @name) @interface
        (type_alias_declaration name: (type_identifier) @name) @type
    """,
    "go": """
        (function_declaration name: (identifier) @name) @function
        (method_declaration name: (field_identifier) @name) @method
        (type_declaration (type_spec name: (type_identifier) @name)) @type
    """,
    "ruby": """
        (method name: (identifier) @name) @method
        (class name: (constant) @name) @class
        (module name: (constant) @name) @module
    """,
}

# Map file extensions to tree-sitter language names
_EXT_TO_TS_LANG = {
    ".py": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rb": "ruby",
}


def is_tree_sitter_available() -> bool:
    """Check if tree-sitter with at least one language grammar is available."""
    return _HAVE_TREE_SITTER and len(_LANGUAGE_PARSERS) > 0


def build_index(workspace: str) -> CodeIndex:
    """Build a code index using tree-sitter when available, falling back to regex.

    Same interface as code_index.build_index() — drop-in replacement.
    """
    if is_tree_sitter_available():
        return _build_index_ts(workspace)
    else:
        # Fall back to regex-based index
        from wisp.code_index import build_index as regex_build
        return regex_build(workspace)


def _build_index_ts(workspace: str) -> CodeIndex:
    """Build code index using tree-sitter parsers."""
    ws = Path(workspace).resolve()
    index = CodeIndex()
    files_scanned = 0

    # Collect source files
    source_files = []
    for ext in _EXT_TO_TS_LANG:
        source_files.extend(ws.rglob(f"*{ext}"))

    # Filter out non-project directories
    ignore_dirs = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "target", "build", "dist", ".eggs", "egg-info",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
    }
    source_files = [
        f for f in source_files
        if not any(part.startswith(".") and part != "." for part in f.relative_to(ws).parts[:1])
        and not any(ignore in f.parts for ignore in ignore_dirs)
    ]
    source_files = sorted(set(source_files))

    for file_path in source_files:
        if files_scanned >= _MAX_SCAN_FILES:
            break

        ext = file_path.suffix.lower()
        ts_lang = _EXT_TO_TS_LANG.get(ext)
        if not ts_lang:
            continue

        rel_path = str(file_path.relative_to(ws))
        index.languages.add(ts_lang.capitalize())

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        if len(lines) > _MAX_FILE_LINES:
            logger.debug("Skipping large file %s (%d lines)", rel_path, len(lines))
            continue

        symbols = _extract_symbols_ts(content, ts_lang, rel_path)
        if symbols:
            index.symbols[rel_path] = symbols
            index.total_symbols += len(symbols)

        files_scanned += 1

    index.files_scanned = files_scanned
    logger.info(
        "Tree-sitter indexed %d symbols from %d files (%s)",
        index.total_symbols, index.files_scanned,
        ", ".join(sorted(index.languages)),
    )
    return index


def _extract_symbols_ts(content: str, lang: str, file_path: str) -> list[Symbol]:
    """Extract symbols using tree-sitter parser."""
    symbols: list[Symbol] = []

    parser_lang = _LANGUAGE_PARSERS.get(lang)
    if not parser_lang:
        # Fall back to regex for this language
        from wisp.code_index import _extract_symbols
        return _extract_symbols(content.splitlines(), lang.capitalize(), file_path)

    try:
        ts_parser = tree_sitter.Parser(language=parser_lang)  # type: ignore
        tree = ts_parser.parse(bytes(content, "utf-8"))
    except Exception as e:
        logger.warning("Tree-sitter parse failed for %s: %s", file_path, e)
        return []

    query_str = _TS_QUERIES.get(lang, "")
    if not query_str:
        return []

    try:
        query = tree_sitter.Query(parser_lang, query_str)  # type: ignore
        cursor = tree_sitter.QueryCursor(query)
        captures = cursor.captures(tree.root_node)
    except Exception as e:
        logger.warning("Tree-sitter query failed for %s: %s", file_path, e)
        return []

    # captures is a dict: capture_name -> list of nodes
    symbols: list[Symbol] = []
    for cap_name, nodes in captures.items():
        for node in nodes:
            if cap_name == "name":
                continue  # handled by the parent capture group
            kind = cap_name
            # Find the name child within this node
            name_node = None
            for child in node.children:
                if child.type == "identifier" or child.type == "type_identifier" or child.type == "property_identifier":
                    name_node = child
                    break
            if name_node is None:
                # Try to get name from captures dict
                continue
            name = name_node.text.decode("utf-8")
            symbols.append(Symbol(
                name=name,
                kind=kind,
                file=file_path,
                line=node.start_point[0] + 1,
            ))

    return symbols
