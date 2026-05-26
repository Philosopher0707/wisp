"""Tests for schema validation."""
from wisp.multi_agent.schema_validator import (
    validate_json_schema,
    validate_subagent_output,
    extract_json_from_markdown,
    build_retry_prompt,
)


class TestValidateJsonSchema:
    def test_valid_object(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        data = {"name": "Alice", "age": 30}
        is_valid, errors = validate_json_schema(data, schema)
        assert is_valid
        assert errors == []

    def test_missing_required_field(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        }
        data = {}
        is_valid, errors = validate_json_schema(data, schema)
        assert not is_valid
        assert any("Missing required field: name" in e for e in errors)

    def test_wrong_type(self):
        schema = {"type": "string"}
        data = 123
        is_valid, errors = validate_json_schema(data, schema)
        assert not is_valid
        assert any("Expected type string" in e for e in errors)

    def test_valid_array(self):
        schema = {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        }
        data = ["a", "b"]
        is_valid, errors = validate_json_schema(data, schema)
        assert is_valid

    def test_array_too_short(self):
        schema = {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
        }
        data = ["a"]
        is_valid, errors = validate_json_schema(data, schema)
        assert not is_valid
        assert any("Array too short" in e for e in errors)

    def test_enum_validation(self):
        schema = {"type": "string", "enum": ["red", "green", "blue"]}
        data = "yellow"
        is_valid, errors = validate_json_schema(data, schema)
        assert not is_valid
        assert any("not in enum" in e for e in errors)

    def test_number_range(self):
        schema = {"type": "number", "minimum": 0, "maximum": 100}
        data = 150
        is_valid, errors = validate_json_schema(data, schema)
        assert not is_valid
        assert any("maximum" in e for e in errors)

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        }
        data = {"user": {"name": "Alice"}}
        is_valid, errors = validate_json_schema(data, schema)
        assert is_valid

    def test_additional_properties_false(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        }
        data = {"name": "Alice", "extra": "field"}
        is_valid, errors = validate_json_schema(data, schema)
        assert not is_valid
        assert any("Extra properties not allowed" in e for e in errors)


class TestExtractJsonFromMarkdown:
    def test_json_code_block(self):
        text = '```json\n{"name": "Alice"}\n```'
        result = extract_json_from_markdown(text)
        assert result == {"name": "Alice"}

    def test_plain_json(self):
        text = '{"name": "Alice"}'
        result = extract_json_from_markdown(text)
        assert result == {"name": "Alice"}

    def test_no_json(self):
        text = "This is just plain text"
        result = extract_json_from_markdown(text)
        assert result is None

    def test_inline_json(self):
        text = 'Some text {"key": "value"} more text'
        result = extract_json_from_markdown(text)
        assert result == {"key": "value"}


class TestValidateSubagentOutput:
    def test_valid_output(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        output = '{"name": "Alice"}'
        is_valid, data, errors = validate_subagent_output(output, schema)
        assert is_valid
        assert data == {"name": "Alice"}
        assert errors == []

    def test_invalid_output(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        output = '{"age": 30}'
        is_valid, data, errors = validate_subagent_output(output, schema)
        assert not is_valid
        assert data is not None
        assert len(errors) > 0

    def test_markdown_wrapped_output(self):
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        output = '```json\n{"result": "success"}\n```'
        is_valid, data, errors = validate_subagent_output(output, schema)
        assert is_valid
        assert data == {"result": "success"}

    def test_no_json_found(self):
        schema = {"type": "object"}
        output = "This is just text with no JSON"
        is_valid, data, errors = validate_subagent_output(output, schema)
        assert not is_valid
        assert data is None
        assert any("No valid JSON" in e for e in errors)


class TestBuildRetryPrompt:
    def test_retry_prompt_structure(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        errors = ["Missing required field: name"]
        prompt = build_retry_prompt("Original task", schema, '{"age": 30}', errors)
        assert "Original task" in prompt
        assert "Missing required field: name" in prompt
        assert "```json" in prompt
        assert "Your previous response was" in prompt
