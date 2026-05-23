"""Message format utilities for multimodal (text + image) content.

Internal format: content arrays (OpenAI/Anthropic style).
Backward compatible: plain strings auto-wrapped to content arrays.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Union

logger = logging.getLogger(__name__)

MAX_IMAGE_MB = 5
MAX_IMAGES_PER_MESSAGE = 20
SUPPORTED_MIME_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})

ContentArray = list[dict]
Content = Union[str, ContentArray]


def to_content_array(content: Content) -> ContentArray:
    """Normalize content to content array format.

    Plain strings become [{type: "text", text: <string>}].
    Content arrays pass through unchanged.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    raise TypeError(f"Content must be str or list, got {type(content)}")


def extract_text(content: Content) -> str:
    """Extract plain text from content, joining multiple text parts."""
    parts = to_content_array(content)
    return "".join(
        p["text"] for p in parts if p.get("type") == "text" and "text" in p
    )


def extract_images(content: Content) -> list[str]:
    """Extract base64 image data URLs from content array.

    Returns base64 strings WITHOUT the 'data:image/...;base64,' prefix
    (Ollama expects raw base64).
    """
    parts = to_content_array(content)
    images: list[str] = []
    for p in parts:
        if p.get("type") == "image_url" and "image_url" in p:
            url = p["image_url"].get("url", "")
            if url.startswith("data:"):
                # Strip the data URL prefix: "data:image/png;base64,<data>"
                b64 = url.split(",", 1)[1] if "," in url else url
                images.append(b64)
            else:
                images.append(url)
    return images


def extract_data_urls(content: Content) -> list[str]:
    """Extract full data URLs (with prefix) from content array."""
    parts = to_content_array(content)
    urls: list[str] = []
    for p in parts:
        if p.get("type") == "image_url" and "image_url" in p:
            url = p["image_url"].get("url", "")
            urls.append(url)
    return urls


def build_image_part(data_url: str) -> dict:
    """Build an image_url content part from a data URL."""
    return {"type": "image_url", "image_url": {"url": data_url}}


def build_text_part(text: str) -> dict:
    """Build a text content part."""
    return {"type": "text", "text": text}


def validate_data_url(data_url: str) -> tuple[bool, str]:
    """Validate a data URL for image content.

    Returns (valid, reason).
    """
    if not data_url.startswith("data:image/"):
        return False, f"Not an image data URL: {data_url[:50]}..."

    mime_end = data_url.find(";")
    if mime_end == -1:
        return False, "Malformed data URL: no semicolon after mime type"

    mime_type = data_url[len("data:"):mime_end]
    if mime_type not in SUPPORTED_MIME_TYPES:
        return False, f"Unsupported image type: {mime_type}"

    if ";base64," not in data_url[:mime_end + 10]:
        return False, "Data URL must be base64 encoded"

    b64_part = data_url.split(",", 1)[1] if "," in data_url else ""
    if not b64_part:
        return False, "Empty base64 data"

    try:
        decoded_len = len(base64.b64decode(b64_part))
    except Exception:
        return False, "Invalid base64 encoding"

    size_mb = decoded_len / (1024 * 1024)
    if size_mb > MAX_IMAGE_MB:
        return False, f"Image too large: {size_mb:.1f}MB (max {MAX_IMAGE_MB}MB)"

    return True, "ok"


def validate_images(data_urls: list[str]) -> tuple[list[str], list[str]]:
    """Validate a list of data URLs. Returns (valid_urls, errors)."""
    if len(data_urls) > MAX_IMAGES_PER_MESSAGE:
        return (
            data_urls[:MAX_IMAGES_PER_MESSAGE],
            [f"Too many images: {len(data_urls)} (max {MAX_IMAGES_PER_MESSAGE})"],
        )

    valid: list[str] = []
    errors: list[str] = []
    for i, url in enumerate(data_urls):
        ok, reason = validate_data_url(url)
        if ok:
            valid.append(url)
        else:
            errors.append(f"Image {i + 1}: {reason}")
    return valid, errors


def to_ollama_messages(messages: list[dict]) -> list[dict]:
    """Convert internal-format messages to Ollama API format.

    Internal format may have content arrays with image_url parts.
    Ollama expects: {role, content: str, images: [base64_str, ...]}.
    """
    # Build tool_call_id → function name lookup for tool result messages.
    # Gemini models require the "name" field on function_response.
    tc_id_to_name: dict[str, str] = {}
    for msg in messages:
        for tc in msg.get("tool_calls", []):
            tc_id = tc.get("id", "")
            name = tc.get("function", {}).get("name", "")
            if tc_id and name:
                tc_id_to_name[tc_id] = name

    converted: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        # Extract text from content arrays (multimodal format → plain string)
        if isinstance(content, list):
            text = extract_text(content)
            images = extract_images(content)
            new_msg: dict = {"role": role, "content": text}
            if role == "user" and images:
                new_msg["images"] = images
            if msg.get("thinking"):
                new_msg["thinking"] = msg["thinking"]
            if msg.get("tool_call_id"):
                new_msg["tool_call_id"] = msg["tool_call_id"]
                tc_name = tc_id_to_name.get(msg["tool_call_id"], "")
                if tc_name:
                    new_msg["name"] = tc_name
        else:
            new_msg = dict(msg)
            # Gemini models require "name" on tool result messages
            if role == "tool" and msg.get("tool_call_id") and "name" not in new_msg:
                tc_name = tc_id_to_name.get(msg["tool_call_id"], "")
                if tc_name:
                    new_msg["name"] = tc_name

        # Convert tool_calls arguments from JSON string → dict (Ollama native format)
        if tool_calls:
            ollama_tcs = []
            for tc in tool_calls:
                func = dict(tc.get("function", {}))
                args = func.get("arguments", {})
                has_type = "type" in tc
                if isinstance(args, dict) and not has_type:
                    # Already in Ollama format — no conversion needed.
                    # Preserve id (Gemini models embed a signature in it).
                    tc_clean = dict(tc)
                    tc_clean.pop("type", None)
                    ollama_tcs.append(tc_clean)
                else:
                    tc_copy = dict(tc)
                    if isinstance(args, str):
                        try:
                            func["arguments"] = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    tc_copy["function"] = func
                    tc_copy.pop("type", None)
                    ollama_tcs.append(tc_copy)
            new_msg["tool_calls"] = ollama_tcs

        # Omit empty content on assistant messages with tool_calls
        if role == "assistant" and tool_calls and not content:
            new_msg.pop("content", None)

        converted.append(new_msg)

    return converted


def merge_content(text: str, images: list[str]) -> ContentArray:
    """Build a content array from text string and image data URL list."""
    parts: list[dict] = []
    if text:
        parts.append(build_text_part(text))
    for img in images:
        ok, reason = validate_data_url(img)
        if ok:
            parts.append(build_image_part(img))
        else:
            logger.warning("Skipping invalid image: %s", reason)
    return parts
