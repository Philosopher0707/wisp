"""Tests for markdown parser — code blocks, thinking sections, front matter."""

from wisp.markdown_parser import (
    parse_markdown,
    extract_code_blocks,
    extract_thinking,
    extract_front_matter,
    strip_markdown,
    format_code_block,
    CodeBlock,
)


class TestExtractCodeBlocks:
    def test_single_code_block(self):
        text = "Here's some code:\n```python\nprint('hello')\n```\nDone."
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].language == "python"
        assert blocks[0].code == "print('hello')"

    def test_multiple_code_blocks(self):
        text = (
            "First:\n```py\na = 1\n```\n"
            "Second:\n```js\nlet x = 2\n```\n"
        )
        blocks = extract_code_blocks(text)
        assert len(blocks) == 2
        assert blocks[0].language == "py"
        assert blocks[1].language == "js"

    def test_code_block_no_language(self):
        text = "```\nplain code\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].language is None

    def test_no_code_blocks(self):
        text = "Just plain text.\nNo fences here."
        assert extract_code_blocks(text) == []

    def test_unclosed_fence(self):
        text = "```python\nopen fence"
        assert extract_code_blocks(text) == []


class TestExtractThinking:
    def test_single_thinking_section(self):
        text = "Hello\nthinking\nLet me think...\nMore thinking\n/thinking\nWorld"
        result = extract_thinking(text)
        assert result is not None
        assert "Let me think..." in result
        assert "More thinking" in result

    def test_no_thinking_section(self):
        text = "Just a normal response."
        assert extract_thinking(text) is None

    def test_multiple_thinking_sections(self):
        text = (
            "A\nthinking\nFirst\n/thinking\n"
            "B\nthinking\nSecond\n/thinking\nC"
        )
        doc = parse_markdown(text)
        assert len(doc.thinking_sections) == 2
        assert "First" in doc.thinking_sections[0].content
        assert "Second" in doc.thinking_sections[1].content

    def test_content_without_thinking(self):
        text = "Hello\nthinking\nhidden\n/thinking\nWorld"
        doc = parse_markdown(text)
        assert "hidden" not in doc.content_without_thinking
        assert "Hello" in doc.content_without_thinking
        assert "World" in doc.content_without_thinking


class TestExtractFrontMatter:
    def test_front_matter(self):
        text = (
            "---\n"
            "name: test-skill\n"
            "description: A test\n"
            "---\n"
            "# Content here"
        )
        fm = extract_front_matter(text)
        assert fm["name"] == "test-skill"
        assert fm["description"] == "A test"

    def test_no_front_matter(self):
        text = "# Just content\nNo front matter."
        assert extract_front_matter(text) == {}

    def test_empty_front_matter(self):
        text = "---\n---\nContent"
        assert extract_front_matter(text) == {}


class TestStripMarkdown:
    def test_bold(self):
        assert strip_markdown("**bold** text") == "bold text"

    def test_italic(self):
        assert strip_markdown("*italic* text") == "italic text"

    def test_inline_code(self):
        assert strip_markdown("Use `code` here") == "Use code here"

    def test_links(self):
        assert strip_markdown("[click](http://example.com)") == "click"

    def test_images(self):
        assert strip_markdown("![alt](img.png)") == "alt"

    def test_headings(self):
        assert strip_markdown("# Title\n## Subtitle") == "Title\nSubtitle"

    def test_blockquotes(self):
        assert strip_markdown("> quote") == "quote"

    def test_mixed(self):
        text = "# Title\n**bold** and *italic* with `code`"
        result = strip_markdown(text)
        assert "Title" in result
        assert "bold" in result
        assert "italic" in result
        assert "code" in result


class TestParseMarkdown:
    def test_full_parse(self):
        text = (
            "---\n"
            "name: test\n"
            "---\n"
            "# Hello\n"
            "thinking\n"
            "Let me think\n"
            "/thinking\n"
            "Some text\n"
            "```python\n"
            "print('hi')\n"
            "```\n"
            "Done."
        )
        doc = parse_markdown(text)
        assert doc.front_matter["name"] == "test"
        assert len(doc.thinking_sections) == 1
        assert len(doc.code_blocks) == 1
        assert doc.code_blocks[0].language == "python"
        assert "Let me think" not in doc.content_without_thinking
        assert "print('hi')" not in doc.content_without_code

    def test_empty_text(self):
        doc = parse_markdown("")
        assert doc.thinking_sections == []
        assert doc.code_blocks == []
        assert doc.front_matter == {}

    def test_clean_content(self):
        text = "Hello\n\n\n\nWorld\n---\nDone"
        doc = parse_markdown(text)
        assert "\n\n\n" not in doc.content_clean


class TestFormatCodeBlock:
    def test_with_language(self):
        block = CodeBlock(language="python", code="print('hi')", start_line=1, end_line=3)
        result = format_code_block(block)
        assert "```python" in result
        assert "print('hi')" in result

    def test_without_language(self):
        block = CodeBlock(language=None, code="plain", start_line=1, end_line=3)
        result = format_code_block(block)
        assert result.startswith("```\n")

    def test_with_line_numbers(self):
        block = CodeBlock(language="py", code="a\nb\nc", start_line=1, end_line=3)
        result = format_code_block(block, show_line_numbers=True)
        assert "1  a" in result
        assert "2  b" in result
        assert "3  c" in result
