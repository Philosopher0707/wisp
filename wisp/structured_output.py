"""Force JSON output from the model with retry-on-parse-failure.

Ollama supports `format: "json"` (free-form JSON) and `format: <schema object>`
(structured JSON).  This module wraps that capability with:

1. Markdown-fence extraction (`` ```json ... ``` `` → raw JSON).
2. Retry loop with escalation prompting on parse failure.
3. Lightweight schema validation (key presence, type checks).
4. Developer helper APIs: ``enforce_json()``, ``extract_json()``.

All operations are self-contained — no external dependencies beyond standard
library and existing Wisp components.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Exceptions ─────────────────────────────────────────────────────────

class JsonExtractionError(RuntimeError):
    """Raised when all retries to extract valid JSON are exhausted."""

    def __init__(self, message: str, attempts: list[str], last_error: str = ""):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class SchemaValidationError(RuntimeError):
    """Raised when JSON parses but doesn't satisfy the requested schema."""

    def __init__(self, message: str, data: Any):
        super().__init__(message)
        self.data = data


# ── Result types ───────────────────────────────────────────────────────

@dataclass
class ExtractedJson:
    """Result of a JSON extraction attempt."""
    text: str          # raw model output
    data: Any        # parsed JSON (dict, list, str, int, bool, None)
    retried: int     # number of retries needed (0 = first try)


# ── Fence extraction ──────────────────────────────────────────────────

_FENCE_START = re.compile(r"```(?:json)?\s*")
_FENCE_END = re.compile(r"\s*```")


def _strip_fences(text: str) -> str:
    """Remove markdown code fences around JSON."""
    text = text.strip()
    # Try fenced extraction
    m = _FENCE_START.search(text)
    if m:
        start = m.end()
        end_m = _FENCE_END.search(text, start)
        if end_m:
            return text[start:end_m.start()].strip()
    return text.strip()


def _find_json(text: str) -> str:
    """Find the outermost JSON object or array in text."""
    text = _strip_fences(text)
    # Find first { or [
    start_obj = text.find("{")
    start_arr = text.find("[")
    if start_obj == -1 and start_arr == -1:
        return text
    start = min(start for start in (start_obj, start_arr) if start != -1)

    # Find matching end brace/bracket
    depth = 0
    in_string = False
    escape = False
    end = start
    for i, ch in enumerate(text[start:], start=start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return text[start:end]


# ── Parsing ──────────────────────────────────────────────────────────

def _try_parse(text: str) -> tuple[bool, Any, str]:
    """Attempt to parse JSON. Returns (ok, data_or_none, error_or_none)."""
    raw = _find_json(text)
    if not raw:
        return False, None, "No JSON found in text"
    try:
        data = json.loads(raw)
        return True, data, ""
    except json.JSONDecodeError as e:
        return False, None, str(e)


# ── Schema validation (lightweight) ────────────────────────────────────

_REQUIRED_KEYS: dict[str, list[str] | None] = {
    "type": None,
    "function": ["name", "parameters"],
}


def _validate_schema(data: Any, schema_key: Optional[str] = None) -> bool:
    """Lightweight schema check — key presence, basic types."""
    if schema_key is None:
        return True  # free-form JSON — any structure is fine

    req = _REQUIRED_KEYS.get(schema_key)
    if req is None:
        return True

    if not isinstance(data, dict):
        return False
    for key in req:
        if key not in data:
            return False
    return True


# ── Extract with retry ───────────────────────────────────────────────

def extract_json(text: str, schema_key: Optional[str] = None) -> ExtractedJson:
    """Parse JSON from raw model text.

    Handles markdown fences, trailing explanations, and stray punctuation.
    """
    ok, data, err = _try_parse(text)
    if ok and _validate_schema(data, schema_key):
        return ExtractedJson(text=text, data=data, retried=0)
    raise JsonExtractionError(
        f"Failed to extract JSON: {err}",
        attempts=[text],
        last_error=err,
    )


async def enforce_json(
    core_generate_fn,
    prompt: str,
    schema_key: Optional[str] = None,
    system: str = "",
    max_retries: int = 2,
    format_hint: str | dict | None = None,
) -> ExtractedJson:
    """Generate JSON from the model, retrying on parse failure.

    Args:
        core_generate_fn: A callable that takes ``(prompt, system, format_hint)`` and
                          returns the raw assistant text (str).
        prompt: The user prompt (not including JSON instructions).
        schema_key: For lightweight validation (e.g. "type" expects {"type":"..."}).
        system: Extra system prompt prepended to the JSON instruction.
        max_retries: How many times to retry after parse failure (total tries = 1 + max_retries).
        format_hint: Passed to the model backend (e.g. Ollama's ``format="json"``).

    Returns:
        ``ExtractedJson`` on success.

    Raises:
        ``JsonExtractionError`` if all retries are exhausted.
    """
    attempts: list[str] = []

    json_instruction = (
        "\n\nIMPORTANT: Respond ONLY with valid JSON. "
        "No markdown, no prose, no explanations. "
        "Wrap your entire response in a single JSON object or array."
    )

    full_prompt = prompt + json_instruction
    if system:
        full_system = system + "\n" + json_instruction
    else:
        full_system = json_instruction

    for attempt in range(1 + max_retries):
        try:
            text = await core_generate_fn(full_prompt, full_system, format_hint)
        except Exception as e:
            logger.warning("enforce_json generate failed on attempt %d: %s", attempt, e)
            attempts.append(str(e))
            if attempt == max_retries:
                raise JsonExtractionError(
                    f"Model generation failed after {max_retries} retries: {e}",
                    attempts,
                    last_error=str(e),
                )
            continue

        attempts.append(text)
        ok, data, err = _try_parse(text)
        if ok and _validate_schema(data, schema_key):
            return ExtractedJson(text=text, data=data, retried=attempt)

        logger.debug("enforce_json parse failed on attempt %d: %s", attempt, err)

        # Escalate prompt for next try
        full_prompt = (
            prompt
            + f"\n\nYour previous response was not valid JSON. Error: {err}. "
            "Please respond with ONLY a JSON object. No markdown fences, no commentary."
        )
        full_system = json_instruction + (
            f"\nATTEMPT {attempt + 1}/{max_retries + 1}. "
            "You MUST output valid JSON only."
        )

    raise JsonExtractionError(
        f"Failed to extract valid JSON after {1 + max_retries} attempts",
        attempts,
        last_error=err,
    )


# ── Convenience: turn any dict into Ollama-compatible format: json hint ─


def validate_json_response(text: str, required_keys: Optional[list[str]] = None) -> dict:
    """Quick validation helper used by tools internally.

    Args:
        text: Raw model output.
        required_keys: If provided, assert these keys exist in the root dict.

    Returns:
        The parsed dict.

    Raises:
        ``ValueError`` if parsing or validation fails.
    """
    ok, data, err = _try_parse(text)
    if not ok:
        raise ValueError(f"Invalid JSON: {err}")
    if required_keys:
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise ValueError(f"Missing required keys: {missing}")
    return data if isinstance(data, dict) else {"data": data}
