"""Code index — lightweight file outline scanner for function/class/struct definitions.

Scans source files using regex to build a searchable index of symbols
(function definitions, class declarations, structs, traits, etc.).
The index is exposed to the LLM via:
1. A brief summary injected into the system prompt
2. A `search_symbols` tool for detailed queries
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum files to scan to avoid performance issues
_MAX_SCAN_FILES = 200
# Maximum lines per file to read (skip huge files)
_MAX_FILE_LINES = 5000
# File extensions to scan by language
_EXTENSIONS = {
    ".py": "Python",
    ".rs": "Rust",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rb": "Ruby",
}


@dataclass
class Symbol:
    """A single symbol (function, class, struct, etc.) found in the codebase."""

    name: str
    kind: str  # "function", "class", "method", "struct", "trait", "enum", "interface", "const"
    file: str  # relative path from workspace root
    line: int
    parent: Optional[str] = None  # parent class/struct for methods


@dataclass
class CodeIndex:
    """In-memory index of all symbols found in the workspace."""

    symbols: dict[str, list[Symbol]] = field(default_factory=dict)  # file -> symbols
    files_scanned: int = 0
    total_symbols: int = 0
    languages: set[str] = field(default_factory=set)


def build_index(workspace: str) -> CodeIndex:
    """Scan workspace for source files and build a symbol index.

    Walks the directory tree up to _MAX_SCAN_FILES files, reads each
    source file, and extracts symbol definitions using language-specific
    regex patterns.
    """
    ws = Path(workspace).resolve()
    index = CodeIndex()
    files_scanned = 0

    # Collect source files first (avoid scanning .git, node_modules, etc.)
    source_files = []
    for ext in _EXTENSIONS:
        source_files.extend(ws.rglob(f"*{ext}"))

    # Filter out common non-project directories
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

    # Sort for deterministic order
    source_files = sorted(set(source_files))

    for file_path in source_files:
        if files_scanned >= _MAX_SCAN_FILES:
            break

        ext = file_path.suffix.lower()
        lang = _EXTENSIONS.get(ext)
        if not lang:
            continue

        rel_path = str(file_path.relative_to(ws))
        index.languages.add(lang)

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        if len(lines) > _MAX_FILE_LINES:
            logger.debug("Skipping large file %s (%d lines)", rel_path, len(lines))
            continue

        symbols = _extract_symbols(lines, lang, rel_path)
        if symbols:
            index.symbols[rel_path] = symbols
            index.total_symbols += len(symbols)

        files_scanned += 1

    index.files_scanned = files_scanned
    logger.info(
        "Indexed %d symbols from %d files (%s)",
        index.total_symbols, index.files_scanned,
        ", ".join(sorted(index.languages)),
    )
    return index


def search_symbols(index: CodeIndex, query: str, max_results: int = 20) -> list[Symbol]:
    """Search for symbols matching a query (case-insensitive substring match).

    Args:
        index: The code index to search.
        query: Search term (matched against symbol name, kind, and file path).
        max_results: Maximum number of results to return.

    Returns:
        List of matching Symbol objects, sorted by relevance (exact match first,
        then prefix match, then substring).
    """
    query_lower = query.lower()
    results: list[Symbol] = []

    for file_path, symbols in index.symbols.items():
        for sym in symbols:
            name_lower = sym.name.lower()
            file_lower = file_path.lower()
            kind_lower = sym.kind.lower()

            # Match against name, kind, or file path
            if (query_lower in name_lower
                    or query_lower in file_lower
                    or query_lower in kind_lower):
                results.append(sym)

    # Sort: exact name match > prefix match > substring match
    def sort_key(sym: Symbol) -> tuple:
        name_lower = sym.name.lower()
        if name_lower == query_lower:
            return (0, sym.name, sym.file, sym.line)
        elif name_lower.startswith(query_lower):
            return (1, sym.name, sym.file, sym.line)
        else:
            return (2, sym.name, sym.file, sym.line)

    results.sort(key=sort_key)
    return results[:max_results]


def format_index_summary(index: CodeIndex) -> str:
    """Format a brief summary of the code index for the system prompt.

    Returns an empty string if no symbols were found.
    """
    if index.total_symbols == 0:
        return ""

    # Count by kind
    kind_counts: dict[str, int] = {}
    for symbols in index.symbols.values():
        for sym in symbols:
            kind_counts[sym.kind] = kind_counts.get(sym.kind, 0) + 1

    # Build a human-readable summary
    parts = [f"{index.total_symbols} symbols in {index.files_scanned} files"]
    if index.languages:
        parts.append(f"({', '.join(sorted(index.languages))})")

    kind_summary = ", ".join(
        f"{count} {kind}s" for kind, count in sorted(kind_counts.items())
    )

    lines = [
        "## Code Index",
        f"- {', '.join(parts)}",
        f"- {kind_summary}",
        "- Use search_symbols() to find specific functions, classes, or types",
    ]
    return "\n".join(lines)


# ── Language-specific extractors ─────────────────────────────────────


def _extract_symbols(lines: list[str], lang: str, file_path: str) -> list[Symbol]:
    """Extract symbols from source lines using language-specific patterns."""
    if lang == "Python":
        return _extract_python(lines, file_path)
    elif lang == "Rust":
        return _extract_rust(lines, file_path)
    elif lang in ("JavaScript", "TypeScript"):
        return _extract_javascript(lines, file_path, lang)
    elif lang == "Go":
        return _extract_go(lines, file_path)
    elif lang == "Ruby":
        return _extract_ruby(lines, file_path)
    return []


def _extract_python(lines: list[str], file_path: str) -> list[Symbol]:
    """Extract Python symbols: class, function, async function."""
    symbols: list[Symbol] = []
    current_class: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Class definition
        m = re.match(r'^class\s+(\w+)', stripped)
        if m:
            current_class = m.group(1)
            symbols.append(Symbol(
                name=current_class,
                kind="class",
                file=file_path,
                line=i + 1,
            ))
            continue

        # Function/method definition
        m = re.match(r'^(?:async\s+)?def\s+(\w+)\s*\(', stripped)
        if m:
            name = m.group(1)
            # Skip private methods if they're not dunder methods
            if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
                if current_class:
                    # Still include private methods inside classes
                    pass
                else:
                    continue
            kind = "method" if current_class else "function"
            symbols.append(Symbol(
                name=name,
                kind=kind,
                file=file_path,
                line=i + 1,
                parent=current_class,
            ))
            continue

        # Reset class context on non-indented lines (end of class)
        # Check the ORIGINAL line (before strip) to detect indentation.
        # Also reset on blank lines when inside a class (they separate methods from top-level code).
        if current_class:
            if not line or not line[0] in (" ", "\t"):
                # Blank line or non-indented line
                if not line.startswith(("def ", "class ", "async ", "@", "#", ")")):
                    current_class = None
                elif line.startswith(("def ", "async ")):
                    # This is a top-level function, not a method — class ended
                    current_class = None

    return symbols


def _extract_rust(lines: list[str], file_path: str) -> list[Symbol]:
    """Extract Rust symbols: fn, struct, enum, trait, type, const, macro."""
    symbols: list[Symbol] = []
    current_impl: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip comments and strings
        if stripped.startswith("//") or stripped.startswith("#!"):
            continue

        # Function
        m = re.match(r'^(?:pub\s+)?(?:unsafe\s+)?fn\s+(\w+)', stripped)
        if m:
            name = m.group(1)
            kind = "method" if current_impl else "function"
            symbols.append(Symbol(
                name=name,
                kind=kind,
                file=file_path,
                line=i + 1,
                parent=current_impl,
            ))
            continue

        # Struct
        m = re.match(r'^(?:pub\s+)?struct\s+(\w+)', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="struct", file=file_path, line=i + 1))
            continue

        # Enum
        m = re.match(r'^(?:pub\s+)?enum\s+(\w+)', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="enum", file=file_path, line=i + 1))
            continue

        # Trait
        m = re.match(r'^(?:pub\s+)?(?:unsafe\s+)?trait\s+(\w+)', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="trait", file=file_path, line=i + 1))
            continue

        # impl block
        m = re.match(r'^(?:pub\s+)?(?:unsafe\s+)?impl\s+(.+?)(?:\s+for\s+(.+?))?\s*\{?$', stripped)
        if m:
            impl_for = m.group(1).strip()
            impl_target = m.group(2).strip() if m.group(2) else None
            current_impl = impl_target or impl_for
            symbols.append(Symbol(
                name=f"impl {impl_for}" + (f" for {impl_target}" if impl_target else ""),
                kind="impl",
                file=file_path,
                line=i + 1,
            ))
            continue

        # Type alias
        m = re.match(r'^(?:pub\s+)?type\s+(\w+)', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="type", file=file_path, line=i + 1))
            continue

        # Const
        m = re.match(r'^(?:pub\s+)?const\s+(\w+)', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="const", file=file_path, line=i + 1))
            continue

        # Reset impl context on closing brace or non-indented line
        if current_impl and (stripped == "}" or (stripped and not stripped.startswith((" ", "\t", "#", "//")))):
            current_impl = None

    return symbols


def _extract_javascript(lines: list[str], file_path: str, lang: str) -> list[Symbol]:
    """Extract JavaScript/TypeScript symbols: function, class, method, interface, type."""
    symbols: list[Symbol] = []
    current_class: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip comments
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        # Class
        m = re.match(r'^(?:export\s+)?(?:default\s+)?class\s+(\w+)', stripped)
        if m:
            current_class = m.group(1)
            symbols.append(Symbol(name=current_class, kind="class", file=file_path, line=i + 1))
            continue

        # Function declaration
        m = re.match(r'^(?:export\s+)?(?:async\s+)?function\s+(?:\*\s+)?(\w+)', stripped)
        if m:
            name = m.group(1)
            kind = "method" if current_class else "function"
            symbols.append(Symbol(
                name=name, kind=kind, file=file_path, line=i + 1, parent=current_class,
            ))
            continue

        # Arrow function assigned to const/let/var
        m = re.match(r'^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\(|\w+\s*=>)', stripped)
        if m:
            symbols.append(Symbol(
                name=m.group(1), kind="function", file=file_path, line=i + 1,
            ))
            continue

        # Method in class (shorthand)
        if current_class:
            m = re.match(r'^(?:async\s+)?(\w+)\s*\(', stripped)
            if m and not stripped.startswith(("if", "for", "while", "switch", "catch", "return")):
                symbols.append(Symbol(
                    name=m.group(1), kind="method", file=file_path, line=i + 1, parent=current_class,
                ))
                continue

        # TypeScript: interface
        if lang == "TypeScript":
            m = re.match(r'^(?:export\s+)?interface\s+(\w+)', stripped)
            if m:
                symbols.append(Symbol(name=m.group(1), kind="interface", file=file_path, line=i + 1))
                continue

            # TypeScript: type alias
            m = re.match(r'^(?:export\s+)?type\s+(\w+)\s*=', stripped)
            if m:
                symbols.append(Symbol(name=m.group(1), kind="type", file=file_path, line=i + 1))
                continue

            # TypeScript: enum
            m = re.match(r'^(?:export\s+)?enum\s+(\w+)', stripped)
            if m:
                symbols.append(Symbol(name=m.group(1), kind="enum", file=file_path, line=i + 1))
                continue

        # Reset class context on non-indented closing brace
        if current_class and line.strip() == "}" and not line[0] in (" ", "\t"):
            current_class = None

    return symbols


def _extract_go(lines: list[str], file_path: str) -> list[Symbol]:
    """Extract Go symbols: func, struct, interface, const, var, type."""
    symbols: list[Symbol] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Function
        m = re.match(r'^func\s+(?:\([^)]*\)\s+)?(\w+)', stripped)
        if m:
            name = m.group(1)
            # Skip if it's a method (has receiver)
            has_receiver = '(' in line and line.index('(') < line.index('func') + 5
            kind = "method" if has_receiver and '(' in stripped and ')' in stripped[:stripped.index('(') + 20] else "function"
            symbols.append(Symbol(name=name, kind=kind, file=file_path, line=i + 1))
            continue

        # Struct
        m = re.match(r'^type\s+(\w+)\s+struct', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="struct", file=file_path, line=i + 1))
            continue

        # Interface
        m = re.match(r'^type\s+(\w+)\s+interface', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="interface", file=file_path, line=i + 1))
            continue

        # Type alias
        m = re.match(r'^type\s+(\w+)\s+', stripped)
        if m and 'struct' not in stripped and 'interface' not in stripped:
            symbols.append(Symbol(name=m.group(1), kind="type", file=file_path, line=i + 1))
            continue

        # Const
        m = re.match(r'^const\s+(\w+)', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="const", file=file_path, line=i + 1))
            continue

        # Var
        m = re.match(r'^var\s+(\w+)', stripped)
        if m:
            symbols.append(Symbol(name=m.group(1), kind="var", file=file_path, line=i + 1))
            continue

    return symbols


def _extract_ruby(lines: list[str], file_path: str) -> list[Symbol]:
    """Extract Ruby symbols: def, class, module."""
    symbols: list[Symbol] = []
    current_class: Optional[str] = None
    current_module: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip comments
        if stripped.startswith("#"):
            continue

        # Module
        m = re.match(r'^(?:class\s+<<\s+)?module\s+(\w+)', stripped)
        if m:
            current_module = m.group(1)
            symbols.append(Symbol(name=current_module, kind="module", file=file_path, line=i + 1))
            continue

        # Class
        m = re.match(r'^class\s+(\w+)', stripped)
        if m:
            current_class = m.group(1)
            symbols.append(Symbol(name=current_class, kind="class", file=file_path, line=i + 1))
            continue

        # Method
        m = re.match(r'^def\s+(?:self\.)?(\w+(?:[?!]))?', stripped)
        if m:
            name = m.group(1)
            if name:
                kind = "method"
                parent = current_class or current_module
                symbols.append(Symbol(
                    name=name, kind=kind, file=file_path, line=i + 1, parent=parent,
                ))
            continue

        # attr_reader/writer/accessor
        m = re.match(r'^attr_(?:reader|writer|accessor)\s+(?::)?(\w+)', stripped)
        if m:
            symbols.append(Symbol(
                name=m.group(1), kind="attribute", file=file_path, line=i + 1,
                parent=current_class or current_module,
            ))
            continue

        # Reset class/module context on `end`
        if stripped == "end":
            current_class = None
            current_module = None

    return symbols
