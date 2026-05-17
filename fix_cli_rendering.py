import re

with open('./wisp/transport/cli.py', 'r') as f:
    content = f.read()

old_func = '''def _render_tool_result(name: str, result, duration_ms, 
                        show_tool_output: bool, box_mode: bool, width: int) -> str:
    """Render a tool result. Extracts and renders diffs for write/edit tools."""
    duration_str = _format_duration(duration_ms)

    # Parse JSON result if it's a string (execute_tool returns JSON string)
    meta = None
    result_text: str
    if isinstance(result, str) and result.startswith("{"):
        try:
            parsed = json.loads(result)
            meta = parsed.get("metadata", {})
            result_text = _coerce_tool_data(parsed.get("data", result))
        except (json.JSONDecodeError, KeyError):
            result_text = str(result)
    elif isinstance(result, dict):
        meta = result.get("metadata", {})
        result_text = result.get("data", str(result))
    else:
        result_text = str(result)

    diff_text = (meta or {}).get("diff", "")
    is_edit_tool = name in ("write_file", "edit_file", "edit_file_multi")

    # Full-output tools (non-edit): preserve multi-line formatting
    if name in _FULL_OUTPUT_TOOLS and not is_edit_tool:
        output_str = result_text
        if not show_tool_output:
            line_count = output_str.count("\\n") + 1
            return dim(f"  ✓ {name} ({duration_str}) — {line_count} lines of output · · · · · · · · · · · · · · · · · · ·")

        if box_mode:
            header = dim(f"  ✓ {name} ({duration_str}) " + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2))
            body = _box(output_str, width=width)
            return f"{header}\\n{body}"

        header = dim(f"  ✓ {name} ({duration_str})")
        body = dim(f"     → {name} result:\\n{output_str}")
        return f"{header}\\n{body}"

    # Edit tools: show summary + diff if available
    if is_edit_tool and diff_text:
        header = dim(f"  ✓ {name} ({duration_str}) " + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2))
        summary = dim(f"     → {result_text[:200].replace(chr(10), ' ')}")
        try:
            from wisp.diff_renderer import render_diff_box
            lang = _detect_language(meta.get('path', ''))
            diff_box = render_diff_box(diff_text, title=f"Diff — {meta.get('path', '')}"[:60],
                                       width=width, box_mode=box_mode, language=lang)
            return f"{header}\\n{summary}\\n{diff_box}"
        except ImportError:
            pass

    # Regular / compact tool results
    if not show_tool_output:
        return dim(f"  ✓ {name} ({duration_str}) · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · ·")

    status_icon = "✓" if not result_text.startswith("Error") else "✗"
    if result_text.startswith("Error"):
        preview = result_text[:200].replace("\\n", " ")
        return dim(f"  ✗ {name} ({duration_str})") + "\\n" + dim(f"     → {preview}")

    preview = result_text[:200].replace("\\n", " ")
    if len(result_text) > 200:
        preview += "..."
    header = dim(f"  {status_icon} {name} ({duration_str}) " + "·" * max(0, width - len(f"  {status_icon} {name} ({duration_str}) ") - 2))
    return f"{header}\\n" + dim(f"     → {preview}")'''

new_func = '''def _render_tool_result(name: str, result, duration_ms, 
                        show_tool_output: bool, box_mode: bool, width: int) -> str:
    """Render a tool result. Extracts and renders diffs for write/edit tools."""
    duration_str = _format_duration(duration_ms)

    # ── Parse JSON result and determine true success/error status ──
    meta: dict | None = None
    parsed: dict | None = None
    result_text: str
    if isinstance(result, str) and result.startswith("{"):
        try:
            parsed = json.loads(result)
            meta = parsed.get("metadata", {})
            result_text = _coerce_tool_data(parsed.get("data", result))
        except (json.JSONDecodeError, KeyError):
            result_text = str(result)
    elif isinstance(result, dict):
        meta = result.get("metadata", {})
        result_text = result.get("data", str(result))
    else:
        result_text = str(result)

    # Determine *true* success from the JSON status field, not by grepping
    # for the string "Error" in the text which can give false negatives.
    if isinstance(parsed, dict):
        is_error = parsed.get("status") == "error"
    else:
        is_error = result_text.startswith("[") or result_text.startswith("Error")

    diff_text = (meta or {}).get("diff", "")
    is_edit_tool = name in ("write_file", "edit_file", "edit_file_multi")

    # Edit tools with a diff: show diff regardless of success/failure
    if is_edit_tool and diff_text:
        header = dim(f"  {'✓' if not is_error else '✗'} {name} ({duration_str}) " + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2))
        summary = dim(f"     → {result_text[:200].replace(chr(10), ' ')}")
        try:
            from wisp.diff_renderer import render_diff_box
            lang = _detect_language(meta.get('path', ''))
            diff_box = render_diff_box(diff_text, title=f"Diff — {meta.get('path', '')}"[:60],
                                       width=width, box_mode=box_mode, language=lang)
            return f"{header}\\n{summary}\\n{diff_box}"
        except ImportError:
            pass

    # Full-output tools (non-edit): preserve multi-line formatting
    if name in _FULL_OUTPUT_TOOLS and not is_edit_tool:
        output_str = result_text
        if not show_tool_output:
            line_count = output_str.count("\\n") + 1
            return dim(f"  {'✓' if not is_error else '✗'} {name} ({duration_str}) — {line_count} lines of output · · · · · · · · · · · · · · · · · · ·")

        if box_mode:
            header = dim(f"  {'✓' if not is_error else '✗'} {name} ({duration_str}) " + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2))
            body = _box(output_str, width=width)
            return f"{header}\\n{body}"

        header = dim(f"  {'✓' if not is_error else '✗'} {name} ({duration_str})")
        body = dim(f"     → {name} result:\\n{output_str}")
        return f"{header}\\n{body}"

    # Regular / compact tool results
    if not show_tool_output:
        return dim(f"  {'✓' if not is_error else '✗'} {name} ({duration_str}) · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · ·")

    # Detailed output
    if is_error:
        preview = result_text[:200].replace("\\n", " ")
        return dim(f"  ✗ {name} ({duration_str})") + "\\n" + dim(f"     → {preview}")

    preview = result_text[:200].replace("\\n", " ")
    if len(result_text) > 200:
        preview += "..."
    header = dim(f"  ✓ {name} ({duration_str}) " + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2))
    return f"{header}\\n" + dim(f"     → {preview}")'''

if old_func in content:
    content = content.replace(old_func, new_func)
    with open('./wisp/transport/cli.py', 'w') as f:
        f.write(content)
    print("cli.py updated successfully")
else:
    print("ERROR: Could not find exact old function")
    # Debug: show what we have around the function
    idx = content.find("def _render_tool_result")
    if idx >= 0:
        print(f"Found _render_tool_result at character {idx}")
        snippet = content[idx:idx+500]
        print("First 500 chars of function:")
        print(repr(snippet[:200]))
