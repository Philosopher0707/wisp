"""
Diff computation and rendering for Wisp — inspired by Pi Coding Agent's edit-diff system.

Provides:
- Unified diff generation with line numbers and context windowing
- Unicode-aware fuzzy matching (smart quotes, dashes, special spaces)
- Multi-edit support (all matched against original content)
- Preview computation (diff without applying changes)
- Structured diff output for TUI rendering
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

# ── Unicode normalization for fuzzy matching ─────────────────────────

# Characters to normalize to ASCII equivalents
_UNICODE_REPLACEMENTS = {
    # Smart single quotes → '
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u201a": "'",  # single low-9 quotation mark
    "\u201b": "'",  # single high-reversed-9 quotation mark
    # Smart double quotes → "
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u201e": '"',  # double low-9 quotation mark
    "\u201f": '"',  # double high-reversed-9 quotation mark
    # Dashes/hyphens → -
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2212": "-",  # minus sign
    # Special spaces → regular space
    "\u00a0": " ",  # non-breaking space
    "\u2002": " ",  # en space
    "\u2003": " ",  # em space
    "\u2004": " ",  # three-per-em space
    "\u2005": " ",  # four-per-em space
    "\u2006": " ",  # six-per-em space
    "\u2007": " ",  # figure space
    "\u2008": " ",  # punctuation space
    "\u2009": " ",  # thin space
    "\u200a": " ",  # hair space
    "\u202f": " ",  # narrow no-break space
    "\u205f": " ",  # medium mathematical space
    "\u3000": " ",  # ideographic space
}

# Regex that matches any character needing replacement
_UNICODE_NORMALIZE_RE = re.compile("|".join(map(re.escape, _UNICODE_REPLACEMENTS.keys())))


def _normalize_with_mapping(text: str) -> tuple[str, list[int]]:
    """Normalize text for fuzzy matching and return (normalized_text, mapping).

    mapping[i] is the index in the original text of the character at
    normalized position i.  This lets us translate a match found in
    normalized space back to the original content so replacements are
    applied to the *original* text, preserving characters that were not
    part of the edit (smart quotes, special spaces, tabs, etc.).

    Normalization steps (same as normalize_for_fuzzy_match):
    1. NFKC normalization per character
    2. Tab → 4 spaces
    3. Unicode replacements (smart quotes, dashes, special spaces)
    4. Strip trailing whitespace per line
    """
    # Pass 1: character-level normalization
    norm_chars: list[str] = []
    mapping: list[int] = []

    for orig_idx, char in enumerate(text):
        transformed = unicodedata.normalize("NFKC", char)
        transformed = transformed.replace("\t", "    ")
        transformed = _UNICODE_NORMALIZE_RE.sub(
            lambda m: _UNICODE_REPLACEMENTS[m.group(0)], transformed
        )
        for c in transformed:
            norm_chars.append(c)
            mapping.append(orig_idx)

    # Pass 2: strip trailing whitespace per line
    remove = [False] * len(norm_chars)
    line_start = 0
    for i, c in enumerate(norm_chars):
        if c == "\n":
            j = i - 1
            while j >= line_start and norm_chars[j] == " ":
                remove[j] = True
                j -= 1
            line_start = i + 1

    # Strip trailing whitespace from last line (no trailing newline)
    j = len(norm_chars) - 1
    while j >= line_start and norm_chars[j] == " ":
        remove[j] = True
        j -= 1

    final_chars = [c for i, c in enumerate(norm_chars) if not remove[i]]
    final_mapping = [mapping[i] for i in range(len(norm_chars)) if not remove[i]]

    return "".join(final_chars), final_mapping


def normalize_for_fuzzy_match(text: str) -> str:
    """Normalize text for fuzzy matching.

    Applies progressive transformations:
    1. NFKC normalization (canonical Unicode composition)
    2. Strip trailing whitespace from each line
    3. Smart quotes → ASCII equivalents
    4. Unicode dashes/hyphens → ASCII hyphen
    5. Special Unicode spaces → regular space
    6. Tabs → spaces (4 spaces per tab)
    """
    # NFKC normalization
    text = unicodedata.normalize("NFKC", text)
    # Expand tabs to spaces before splitting lines
    text = text.replace("\t", "    ")
    # Strip trailing whitespace per line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Replace Unicode characters with ASCII equivalents
    text = _UNICODE_NORMALIZE_RE.sub(lambda m: _UNICODE_REPLACEMENTS[m.group(0)], text)
    return text


def fuzzy_find_text(content: str, old_text: str) -> FuzzyMatchResult:
    """Find old_text in content, trying exact match first, then fuzzy match.

    Returns the match index in the *original* content so callers can
    apply replacements to the original text without data loss.
    """
    # Try exact match first
    exact_index = content.find(old_text)
    if exact_index != -1:
        return FuzzyMatchResult(
            found=True,
            index=exact_index,
            match_length=len(old_text),
            used_fuzzy_match=False,
            content_for_replacement=content,
            original_index=exact_index,
            original_match_length=len(old_text),
        )

    # Try fuzzy match — build mapping so we can translate back to original
    fuzzy_content, mapping = _normalize_with_mapping(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)
    fuzzy_index = fuzzy_content.find(fuzzy_old_text)

    if fuzzy_index != -1:
        # Map the normalized match region back to original indices
        match_end = fuzzy_index + len(fuzzy_old_text) - 1
        orig_start = mapping[fuzzy_index]
        orig_end = mapping[match_end] + 1
        return FuzzyMatchResult(
            found=True,
            index=fuzzy_index,
            match_length=len(fuzzy_old_text),
            used_fuzzy_match=True,
            content_for_replacement=fuzzy_content,
            original_index=orig_start,
            original_match_length=orig_end - orig_start,
        )

    # ── Fallback 2: strip common leading whitespace ──────────────────
    # If old_text is indented differently than the file content,
    # strip the common leading whitespace from both and retry.
    def _dedent_lines(text: str) -> str:
        lines = text.split("\n")
        # Find minimum leading whitespace across non-empty lines
        min_indent = min(
            (len(line) - len(line.lstrip()) for line in lines if line.strip()),
            default=0,
        )
        if min_indent > 0:
            return "\n".join(line[min_indent:] if line.strip() else line for line in lines)
        return text

    dedented_old = _dedent_lines(fuzzy_old_text)
    dedented_content = _dedent_lines(fuzzy_content)
    if dedented_old != fuzzy_old_text:
        dedented_index = dedented_content.find(dedented_old)
        if dedented_index != -1:
            # Map back to original indices via the fuzzy content mapping
            match_end = dedented_index + len(dedented_old) - 1
            orig_start = mapping[dedented_index]
            orig_end = mapping[match_end] + 1
            return FuzzyMatchResult(
                found=True,
                index=dedented_index,
                match_length=len(dedented_old),
                used_fuzzy_match=True,
                content_for_replacement=fuzzy_content,
                original_index=orig_start,
                original_match_length=orig_end - orig_start,
            )

    # ── Fallback 3: approximate match with SequenceMatcher ────────────
    # If the text is very similar (ratio > 0.85), accept the best match.
    import difflib
    matcher = difflib.SequenceMatcher(None, fuzzy_content, fuzzy_old_text)
    match = matcher.find_longest_match(0, len(fuzzy_content), 0, len(fuzzy_old_text))
    if match.size > 0:
        ratio = match.size / len(fuzzy_old_text)
        if ratio >= 0.85:
            match_end = match.a + match.size - 1
            orig_start = mapping[match.a]
            orig_end = mapping[match_end] + 1
            return FuzzyMatchResult(
                found=True,
                index=match.a,
                match_length=match.size,
                used_fuzzy_match=True,
                content_for_replacement=fuzzy_content,
                original_index=orig_start,
                original_match_length=orig_end - orig_start,
            )

    return FuzzyMatchResult(
        found=False,
        index=-1,
        match_length=0,
        used_fuzzy_match=False,
        content_for_replacement=content,
        original_index=-1,
        original_match_length=0,
    )


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class FuzzyMatchResult:
    """Result of fuzzy_find_text."""
    found: bool
    index: int
    match_length: int
    used_fuzzy_match: bool
    content_for_replacement: str
    original_index: int = -1
    original_match_length: int = 0


@dataclass
class DiffResult:
    """Result of a diff computation."""
    diff: str
    first_changed_line: Optional[int] = None


@dataclass
class EditResult:
    """Result of applying an edit."""
    success: bool
    path: str
    diff: Optional[str] = None
    first_changed_line: Optional[int] = None
    old_length: int = 0
    new_length: int = 0
    used_fuzzy_match: bool = False
    fuzzy_similarity: Optional[float] = None
    error: Optional[str] = None
    edits_applied: int = 0


@dataclass
class EditOp:
    """A single edit operation."""
    old_text: str
    new_text: str


@dataclass
class DiffHunk:
    """A single hunk within a diff — contiguous change block with context."""
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[dict]  # each: {prefix: str, line_num: int | None, content: str}


def parse_hunks(diff_text: str) -> list[DiffHunk]:
    """Parse a diff string (as produced by generate_diff_string) into hunks.

    Each hunk is a contiguous block of added/removed lines with surrounding context.
    Returns empty list for empty diffs.
    """
    if not diff_text.strip():
        return []

    raw_lines = diff_text.split("\n")
    hunks: list[DiffHunk] = []
    current_lines: list[dict] = []
    old_start: int | None = None
    new_start: int | None = None
    old_count = 0
    new_count = 0
    in_change_block = False

    def _flush():
        nonlocal old_start, new_start, old_count, new_count, in_change_block, current_lines
        if current_lines:
            hunks.append(DiffHunk(
                header=f"@@ -{old_start or 1},{old_count} +{new_start or 1},{new_count} @@",
                old_start=old_start or 1,
                old_count=old_count,
                new_start=new_start or 1,
                new_count=new_count,
                lines=current_lines,
            ))
        current_lines = []
        old_start = None
        new_start = None
        old_count = 0
        new_count = 0
        in_change_block = False

    for raw in raw_lines:
        if not raw:
            continue

        # Skip markers like "      ..."
        stripped = raw.strip()
        if stripped == "...":
            _flush()
            continue

        if len(raw) < 2:
            continue

        prefix = raw[0]
        rest = raw[1:].lstrip(" ")  # skip leading spaces after prefix

        # Parse line number and content
        # Format: "<prefix><line_num_padded> <content>"
        try:
            space_idx = rest.index(" ")
        except ValueError:
            continue

        num_str = rest[:space_idx]
        content = rest[space_idx + 1:]

        try:
            line_num = int(num_str) if num_str else None
        except ValueError:
            line_num = None

        if prefix == " ":
            # Context line — belongs to current hunk if we're in a change block,
            # otherwise starts or continues context
            if in_change_block:
                current_lines.append({"prefix": " ", "line_num": line_num, "content": content})
                old_count += 1
                new_count += 1
            else:
                # Keep trailing context ready — only keep last N context lines
                current_lines.append({"prefix": " ", "line_num": line_num, "content": content})
                if len(current_lines) > 6:
                    current_lines.pop(0)
                if old_start is None and line_num is not None:
                    old_start = line_num
                    new_start = line_num

        elif prefix == "+":
            in_change_block = True
            if old_start is None:
                old_start = line_num
            if new_start is None:
                new_start = line_num
            current_lines.append({"prefix": "+", "line_num": line_num, "content": content})
            new_count += 1

        elif prefix == "-":
            in_change_block = True
            if old_start is None:
                old_start = line_num
            if new_start is None:
                new_start = line_num
            current_lines.append({"prefix": "-", "line_num": line_num, "content": content})
            old_count += 1

    _flush()
    return hunks


# ── Line ending handling ─────────────────────────────────────────────


def detect_line_ending(content: str) -> str:
    """Detect the line ending style of content."""
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")
    if lf_idx == -1:
        return "\n"
    if crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    """Convert all line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    """Restore original line endings."""
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(content: str) -> tuple[str, str]:
    """Strip UTF-8 BOM if present. Returns (bom, text_without_bom)."""
    if content.startswith("\ufeff"):
        return "\ufeff", content[1:]
    return "", content


# ── Diff generation ──────────────────────────────────────────────────


def generate_diff_string(
    old_content: str,
    new_content: str,
    context_lines: int = 4,
) -> DiffResult:
    """Generate a unified diff string with line numbers and context.

    Args:
        old_content: Original file content.
        new_content: New file content after edits.
        context_lines: Number of context lines to show around changes.

    Returns:
        DiffResult with the formatted diff string and first changed line number.
    """
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")

    # Compute line-level diff using Myers algorithm (simplified LCS-based)
    diff_chunks = _compute_line_diff(old_lines, new_lines)

    max_line_num = max(len(old_lines), len(new_lines))
    line_num_width = max(len(str(max_line_num)), 1)

    output: list[str] = []
    old_line_num = 1
    new_line_num = 1
    first_changed_line: Optional[int] = None
    last_was_change = False

    for i, chunk in enumerate(diff_chunks):
        raw = chunk["lines"]
        is_change = chunk["type"] in ("added", "removed")

        if is_change:
            if first_changed_line is None:
                first_changed_line = new_line_num

            for line in raw:
                if chunk["type"] == "added":
                    line_num = str(new_line_num).rjust(line_num_width)
                    output.append(f"+{line_num} {line}")
                    new_line_num += 1
                else:  # removed
                    line_num = str(old_line_num).rjust(line_num_width)
                    output.append(f"-{line_num} {line}")
                    old_line_num += 1
            last_was_change = True

        else:
            # Context lines — only show near changes
            next_is_change = (i < len(diff_chunks) - 1 and
                              diff_chunks[i + 1]["type"] in ("added", "removed"))
            has_leading = last_was_change
            has_trailing = next_is_change

            if has_leading and has_trailing:
                if len(raw) <= context_lines * 2:
                    for line in raw:
                        line_num = str(old_line_num).rjust(line_num_width)
                        output.append(f" {line_num} {line}")
                        old_line_num += 1
                        new_line_num += 1
                else:
                    leading = raw[:context_lines]
                    trailing = raw[-context_lines:]
                    skipped = len(raw) - len(leading) - len(trailing)
                    for line in leading:
                        line_num = str(old_line_num).rjust(line_num_width)
                        output.append(f" {line_num} {line}")
                        old_line_num += 1
                        new_line_num += 1
                    output.append(f" {'':>{line_num_width}} ...")
                    old_line_num += skipped
                    new_line_num += skipped
                    for line in trailing:
                        line_num = str(old_line_num).rjust(line_num_width)
                        output.append(f" {line_num} {line}")
                        old_line_num += 1
                        new_line_num += 1

            elif has_leading:
                shown = raw[:context_lines]
                skipped = len(raw) - len(shown)
                for line in shown:
                    line_num = str(old_line_num).rjust(line_num_width)
                    output.append(f" {line_num} {line}")
                    old_line_num += 1
                    new_line_num += 1
                if skipped > 0:
                    output.append(f" {'':>{line_num_width}} ...")
                    old_line_num += skipped
                    new_line_num += skipped

            elif has_trailing:
                skipped = max(0, len(raw) - context_lines)
                if skipped > 0:
                    output.append(f" {'':>{line_num_width}} ...")
                    old_line_num += skipped
                    new_line_num += skipped
                for line in raw[skipped:]:
                    line_num = str(old_line_num).rjust(line_num_width)
                    output.append(f" {line_num} {line}")
                    old_line_num += 1
                    new_line_num += 1

            else:
                # Skip these context lines entirely
                old_line_num += len(raw)
                new_line_num += len(raw)

            last_was_change = False

    return DiffResult(
        diff="\n".join(output),
        first_changed_line=first_changed_line,
    )


def _compute_line_diff(
    old_lines: list[str],
    new_lines: list[str],
) -> list[dict]:
    """Compute a line-level diff using LCS (Longest Common Subsequence).

    Returns a list of chunks, each with 'type' ('equal', 'added', 'removed')
    and 'lines' (list of strings).
    """
    # Build LCS table
    m, n = len(old_lines), len(new_lines)
    # Use 1D DP for memory efficiency
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if old_lines[i - 1] == new_lines[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # Backtrack to produce diff chunks
    chunks: list[dict] = []
    i, j = m, n

    # We'll build in reverse, then flip
    reverse_chunks: list[dict] = []

    while i > 0 or j > 0:
        if i > 0 and j > 0 and old_lines[i - 1] == new_lines[j - 1]:
            reverse_chunks.append({"type": "equal", "lines": [old_lines[i - 1]]})
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            reverse_chunks.append({"type": "added", "lines": [new_lines[j - 1]]})
            j -= 1
        else:
            reverse_chunks.append({"type": "removed", "lines": [old_lines[i - 1]]})
            i -= 1

    # Merge consecutive chunks of the same type
    for chunk in reversed(reverse_chunks):
        if chunks and chunks[-1]["type"] == chunk["type"]:
            chunks[-1]["lines"].extend(chunk["lines"])
        else:
            chunks.append({"type": chunk["type"], "lines": list(chunk["lines"])})

    return chunks


# ── Edit application ─────────────────────────────────────────────────


def apply_edits_to_content(
    content: str,
    edits: list[EditOp],
    file_path: str = "<unknown>",
) -> tuple[str, str, bool]:
    """Apply one or more exact-text replacements to content.

    All edits are matched against the SAME original content. Replacements
    are applied in reverse order so offsets remain stable.

    Args:
        content: The original file content (LF-normalized).
        edits: List of edit operations.
        file_path: File path for error messages.

    Returns:
        Tuple of (base_content, new_content, used_fuzzy_match).

    Raises:
        ValueError: If any old_text is not found, not unique, or edits overlap.
    """
    # Validate no empty old_text
    for i, edit in enumerate(edits):
        if not edit.old_text:
            suffix = f" in {file_path}" if len(edits) == 1 else f" edits[{i}] in {file_path}"
            raise ValueError(f"old_text must not be empty{suffix}")

    # Match all edits against original content
    matches = [fuzzy_find_text(content, edit.old_text) for edit in edits]

    # Find all matches
    matched_edits: list[dict] = []
    any_fuzzy = any(m.used_fuzzy_match for m in matches)
    for i, edit in enumerate(edits):
        match = matches[i]
        if not match.found:
            suffix = f" in {file_path}" if len(edits) == 1 else f" edits[{i}] in {file_path}"
            raise ValueError(
                f"Could not find the exact text{suffix}. "
                "The old_text must match exactly including all whitespace and newlines."
            )

        # Check uniqueness in normalized space
        fuzzy_content = normalize_for_fuzzy_match(content)
        count = fuzzy_content.count(
            normalize_for_fuzzy_match(edit.old_text) if match.used_fuzzy_match else edit.old_text
        )
        if count > 1:
            suffix = f" in {file_path}" if len(edits) == 1 else f" edits[{i}] in {file_path}"
            raise ValueError(
                f"Found {count} occurrences of the text{suffix}. "
                "The text must be unique. Please provide more context to make it unique."
            )

        # ── Collision safety check ───────────────────────────────────
        # When fuzzy matching, verify the matched original text actually
        # normalizes to what we asked for. NFKC can cause false matches
        # (e.g., fullwidth "ａ" collides with regular "a" in normalized
        # space), mapping back to the wrong region of original content.
        if match.used_fuzzy_match:
            actual_original = content[match.original_index : match.original_index + match.original_match_length]
            if normalize_for_fuzzy_match(actual_original) != normalize_for_fuzzy_match(edit.old_text):
                raise ValueError(
                    f"Fuzzy match resolved to unexpected text{suffix}. "
                    f"The matched region does not match what was requested after normalization. "
                    f"Provide more context to make the target unique."
                )

        # Use original indices so replacements preserve untouched content
        matched_edits.append({
            "edit_index": i,
            "match_index": match.original_index,
            "match_length": match.original_match_length,
            "new_text": edit.new_text,
        })

    # Sort by position and check for overlaps
    matched_edits.sort(key=lambda e: e["match_index"])
    for i in range(1, len(matched_edits)):
        prev = matched_edits[i - 1]
        curr = matched_edits[i]
        if prev["match_index"] + prev["match_length"] > curr["match_index"]:
            raise ValueError(
                f"edits[{prev['edit_index']}] and edits[{curr['edit_index']}] "
                f"overlap in {file_path}. Merge them into one edit or target disjoint regions."
            )

    # Apply replacements in reverse order (right-to-left) on ORIGINAL content
    new_content = content
    for edit in reversed(matched_edits):
        new_content = (
            new_content[:edit["match_index"]]
            + edit["new_text"]
            + new_content[edit["match_index"] + edit["match_length"]:]
        )

    if content == new_content:
        suffix = f" to {file_path}" if len(edits) == 1 else f" to {file_path}"
        raise ValueError(
            f"No changes made{suffix}. "
            "The replacement produced identical content."
        )

    return content, new_content, any_fuzzy


# ── High-level API ───────────────────────────────────────────────────


def compute_edit_diff(
    path: str,
    edits: list[EditOp],
    workspace: str,
) -> EditResult:
    """Compute the diff for edit operations without applying them.

    Used for preview rendering before the tool executes.

    Args:
        path: File path (relative or absolute).
        edits: List of edit operations to preview.
        workspace: Workspace root directory.

    Returns:
        EditResult with diff preview or error.
    """
    from pathlib import Path as PathLib

    ws = PathLib(workspace).resolve()
    file_path = (ws / path).resolve() if not PathLib(path).is_absolute() else PathLib(path).resolve()

    try:
        if not file_path.exists():
            return EditResult(
                success=False,
                path=path,
                error=f"File not found: {path}",
            )

        raw_content = file_path.read_text(encoding="utf-8", errors="replace")
        _, content = strip_bom(raw_content)
        normalized = normalize_to_lf(content)

        base_content, new_content, used_fuzzy = apply_edits_to_content(normalized, edits, path)
        diff_result = generate_diff_string(base_content, new_content)

        return EditResult(
            success=True,
            path=path,
            diff=diff_result.diff,
            first_changed_line=diff_result.first_changed_line,
            edits_applied=len(edits),
            used_fuzzy_match=used_fuzzy,
        )

    except ValueError as e:
        return EditResult(
            success=False,
            path=path,
            error=str(e),
        )
    except Exception as e:
        return EditResult(
            success=False,
            path=path,
            error=f"Could not edit file: {path}. {e}",
        )


def apply_edit_with_diff(
    path: str,
    edits: list[EditOp],
    workspace: str,
) -> EditResult:
    """Apply edits to a file and return the diff.

    Args:
        path: File path (relative or absolute).
        edits: List of edit operations.
        workspace: Workspace root directory.

    Returns:
        EditResult with diff and metadata.
    """
    from pathlib import Path as PathLib

    ws = PathLib(workspace).resolve()
    file_path = (ws / path).resolve() if not PathLib(path).is_absolute() else PathLib(path).resolve()

    try:
        if not file_path.exists():
            return EditResult(
                success=False,
                path=path,
                error=f"File not found: {path}",
            )

        with open(file_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
            raw_content = f.read()
        bom, content = strip_bom(raw_content)
        original_ending = detect_line_ending(content)
        normalized = normalize_to_lf(content)

        base_content, new_content, used_fuzzy = apply_edits_to_content(normalized, edits, path)

        # Restore BOM and line endings before writing
        final_content = bom + restore_line_endings(new_content, original_ending)
        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            f.write(final_content)

        diff_result = generate_diff_string(base_content, new_content)

        # Calculate total old/new lengths
        total_old = sum(len(e.old_text) for e in edits)
        total_new = sum(len(e.new_text) for e in edits)

        return EditResult(
            success=True,
            path=path,
            diff=diff_result.diff,
            first_changed_line=diff_result.first_changed_line,
            old_length=total_old,
            new_length=total_new,
            edits_applied=len(edits),
            used_fuzzy_match=used_fuzzy,
        )

    except ValueError as e:
        return EditResult(
            success=False,
            path=path,
            error=str(e),
        )
    except Exception as e:
        return EditResult(
            success=False,
            path=path,
            error=f"Could not edit file: {path}. {e}",
        )
