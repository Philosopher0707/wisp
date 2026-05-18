"""Contract schema validation — structured output validation for subagent results.

Validates subagent output against JSON schemas and provides retry logic
for schema validation failures.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SchemaValidationError(Exception):
    """Raised when subagent output fails schema validation."""
    def __init__(self, message: str, errors: list[str] = None):
        super().__init__(message)
        self.errors = errors or []


def validate_json_schema(data: Any, schema: dict) -> tuple[bool, list[str]]:
    """Validate data against a JSON schema.

    Args:
        data: The data to validate (should be a dict/list from json.loads)
        schema: JSON schema dict

    Returns:
        (is_valid, list_of_error_messages)
    """
    errors = []

    if not isinstance(schema, dict):
        return False, ["Schema must be a dict"]

    schema_type = schema.get("type")

    # Type validation
    if schema_type:
        type_valid = _check_type(data, schema_type)
        if not type_valid:
            errors.append(f"Expected type {schema_type}, got {type(data).__name__}")
            return False, errors

    # Object validation
    if schema_type == "object" and isinstance(data, dict):
        # Required fields
        required = schema.get("required", [])
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        # Properties
        properties = schema.get("properties", {})
        for key, prop_schema in properties.items():
            if key in data:
                valid, prop_errors = validate_json_schema(data[key], prop_schema)
                if not valid:
                    errors.extend([f"{key}: {e}" for e in prop_errors])

        # Additional properties
        additional = schema.get("additionalProperties", True)
        if additional is False:
            allowed = set(properties.keys())
            extra = set(data.keys()) - allowed
            if extra:
                errors.append(f"Extra properties not allowed: {extra}")

    # Array validation
    elif schema_type == "array" and isinstance(data, list):
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(data):
                valid, item_errors = validate_json_schema(item, items_schema)
                if not valid:
                    errors.extend([f"[{i}]: {e}" for e in item_errors])

        min_items = schema.get("minItems")
        if min_items is not None and len(data) < min_items:
            errors.append(f"Array too short: {len(data)} < {min_items}")

        max_items = schema.get("maxItems")
        if max_items is not None and len(data) > max_items:
            errors.append(f"Array too long: {len(data)} > {max_items}")

    # String validation
    elif schema_type == "string" and isinstance(data, str):
        min_len = schema.get("minLength")
        if min_len is not None and len(data) < min_len:
            errors.append(f"String too short: {len(data)} < {min_len}")

        max_len = schema.get("maxLength")
        if max_len is not None and len(data) > max_len:
            errors.append(f"String too long: {len(data)} > {max_len}")

        pattern = schema.get("pattern")
        if pattern:
            import re
            if not re.match(pattern, data):
                errors.append(f"String does not match pattern: {pattern}")

    # Number validation
    elif schema_type in ("number", "integer") and isinstance(data, (int, float)):
        minimum = schema.get("minimum")
        if minimum is not None and data < minimum:
            errors.append(f"Value {data} < minimum {minimum}")

        maximum = schema.get("maximum")
        if maximum is not None and data > maximum:
            errors.append(f"Value {data} > maximum {maximum}")

    # Enum validation
    enum = schema.get("enum")
    if enum is not None and data not in enum:
        errors.append(f"Value {data!r} not in enum {enum}")

    return len(errors) == 0, errors


def _check_type(data: Any, expected_type: str | list[str]) -> bool:
    """Check if data matches the expected JSON schema type."""
    if isinstance(expected_type, list):
        return any(_check_single_type(data, t) for t in expected_type)
    return _check_single_type(data, expected_type)


def _check_single_type(data: Any, expected_type: str) -> bool:
    """Check if data matches a single JSON schema type."""
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    expected = type_map.get(expected_type)
    if expected is None:
        return True  # Unknown type, allow it
    return isinstance(data, expected)


def extract_json_from_markdown(text: str) -> Optional[dict]:
    """Extract JSON from markdown code blocks or raw text.

    Args:
        text: Text that may contain JSON in markdown code blocks

    Returns:
        Parsed JSON dict, or None if no valid JSON found
    """
    # Try the whole text first (most common case: pure JSON output)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find JSON in code blocks
    import re

    # Look for ```json blocks
    json_blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    for block in json_blocks:
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue

    # Fall back to balanced-brace/bracket scanning for nested JSON
    # This correctly handles arbitrarily deep nesting that flat regex can't.
    for result in _scan_balanced_json(text):
        return result

    return None


def _scan_balanced_json(text: str):
    """Yield parsed JSON values by scanning for balanced { } and [ ] spans.

    Tries every position where a JSON container opens, prefers the largest
    (outermost) match so nested structures are returned whole.
    """
    candidates: list[tuple[int, int]] = []  # (start, end) pairs

    for opener, closer in (('{', '}'), ('[', ']')):
        i = 0
        while i < len(text):
            start = text.find(opener, i)
            if start == -1:
                break
            depth = 0
            in_str = False
            escape = False
            j = start
            while j < len(text):
                ch = text[j]
                if escape:
                    escape = False
                elif in_str:
                    if ch == '\\':
                        escape = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == opener:
                        depth += 1
                    elif ch == closer:
                        depth -= 1
                        if depth == 0:
                            candidates.append((start, j + 1))
                            break
                j += 1
            i = start + 1

    # Sort longest-first so we return the outermost/richest match first
    candidates.sort(key=lambda x: x[1] - x[0], reverse=True)
    seen: set[tuple[int, int]] = set()
    for start, end in candidates:
        if (start, end) in seen:
            continue
        seen.add((start, end))
        try:
            yield json.loads(text[start:end])
        except json.JSONDecodeError:
            continue


def validate_subagent_output(output: str, schema: dict,
                             auto_retry: bool = True) -> tuple[bool, Any, list[str]]:
    """Validate subagent output against a schema.

    Args:
        output: Raw subagent output text
        schema: JSON schema dict
        auto_retry: Whether to attempt extraction from markdown

    Returns:
        (is_valid, parsed_data, error_messages)
    """
    # Try to extract JSON
    data = extract_json_from_markdown(output)

    if data is None:
        if auto_retry:
            # Try more aggressive extraction
            # Look for anything that looks like JSON
            import re
            # Try to find arrays too
            array_match = re.search(r'\[.*?\]', output, re.DOTALL)
            if array_match:
                try:
                    data = json.loads(array_match.group())
                except json.JSONDecodeError:
                    pass

        if data is None:
            return False, None, ["No valid JSON found in output"]

    # Validate against schema
    is_valid, errors = validate_json_schema(data, schema)

    return is_valid, data, errors


def build_retry_prompt(original_task: str, schema: dict,
                       previous_output: str, errors: list[str]) -> str:
    """Build a prompt for retrying a subagent with schema validation feedback.

    Args:
        original_task: The original task description
        schema: The JSON schema that failed validation
        previous_output: The previous invalid output
        errors: List of validation errors

    Returns:
        Retry prompt string
    """
    schema_str = json.dumps(schema, indent=2)
    errors_str = "\n".join(f"- {e}" for e in errors)

    return f"""{original_task}

IMPORTANT: Your previous response failed validation.

Validation errors:
{errors_str}

Please provide a response that matches this JSON schema:
```json
{schema_str}
```

Your previous response was:
```
{previous_output[:500]}
```

Please fix the issues and return valid JSON matching the schema.
"""
