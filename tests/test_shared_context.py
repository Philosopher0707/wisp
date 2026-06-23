"""Tests for SharedContext — inter-subagent communication."""

from __future__ import annotations

import asyncio
import pytest

from wisp.multi_agent.shared_context import (
    SharedContext,
    Finding,
    build_shared_context_tool_schema,
    build_shared_context_tool_impl,
)


@pytest.fixture
def ctx():
    return SharedContext()


class TestSharedContextFindings:
    @pytest.mark.asyncio
    async def test_post_and_get(self, ctx):
        await ctx.post("agent_a", "auth_structure", "auth.py has login and logout functions")
        finding = await ctx.get("auth_structure")
        assert finding is not None
        assert finding.agent_id == "agent_a"
        assert finding.value == "auth.py has login and logout functions"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, ctx):
        result = await ctx.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_post_overwrites(self, ctx):
        await ctx.post("agent_a", "key1", "first")
        await ctx.post("agent_b", "key1", "second")
        finding = await ctx.get("key1")
        assert finding.value == "second"
        assert finding.agent_id == "agent_b"

    @pytest.mark.asyncio
    async def test_post_with_tags(self, ctx):
        await ctx.post("agent_a", "bug_found", "memory leak in utils.py", tags=["bug", "memory"])
        findings = await ctx.query("bug")
        assert len(findings) == 1
        assert findings[0].key == "bug_found"

    @pytest.mark.asyncio
    async def test_query_no_matches(self, ctx):
        await ctx.post("agent_a", "key1", "val1", tags=["foo"])
        results = await ctx.query("bar")
        assert results == []

    @pytest.mark.asyncio
    async def test_query_multiple_matches(self, ctx):
        await ctx.post("agent_a", "key1", "val1", tags=["bug"])
        await ctx.post("agent_b", "key2", "val2", tags=["bug", "auth"])
        await ctx.post("agent_c", "key3", "val3", tags=["auth"])
        bug_findings = await ctx.query("bug")
        assert len(bug_findings) == 2

    @pytest.mark.asyncio
    async def test_all_findings(self, ctx):
        await ctx.post("agent_a", "key1", "val1")
        await ctx.post("agent_b", "key2", "val2")
        all_f = await ctx.all_findings()
        assert len(all_f) == 2

    @pytest.mark.asyncio
    async def test_all_findings_empty(self, ctx):
        assert await ctx.all_findings() == []


class TestSharedContextFileClaims:
    @pytest.mark.asyncio
    async def test_claim_file_success(self, ctx):
        claimed = await ctx.claim_file("src/auth.py", "agent_a")
        assert claimed is True

    @pytest.mark.asyncio
    async def test_claim_file_already_claimed(self, ctx):
        await ctx.claim_file("src/auth.py", "agent_a")
        claimed = await ctx.claim_file("src/auth.py", "agent_b")
        assert claimed is False

    @pytest.mark.asyncio
    async def test_is_claimed(self, ctx):
        await ctx.claim_file("src/auth.py", "agent_a")
        assert await ctx.is_claimed("src/auth.py") is True
        assert await ctx.is_claimed("src/other.py") is False

    @pytest.mark.asyncio
    async def test_file_claims(self, ctx):
        await ctx.claim_file("a.py", "agent_a")
        await ctx.claim_file("b.py", "agent_b")
        claims = await ctx.file_claims()
        assert claims == {"a.py": "agent_a", "b.py": "agent_b"}

    @pytest.mark.asyncio
    async def test_file_claims_empty(self, ctx):
        claims = await ctx.file_claims()
        assert claims == {}


class TestSharedContextPromptFormatting:
    @pytest.mark.asyncio
    async def test_format_empty_context(self, ctx):
        text = ctx.format_for_prompt("agent_a")
        assert "no findings shared yet" in text

    @pytest.mark.asyncio
    async def test_format_excludes_own_findings(self, ctx):
        await ctx.post("agent_a", "my_finding", "my value")
        await ctx.post("agent_b", "sibling_finding", "sibling value")
        text = ctx.format_for_prompt("agent_a")
        assert "sibling_finding" in text
        assert "sibling value" in text
        assert "my_finding" not in text

    @pytest.mark.asyncio
    async def test_format_includes_file_claims(self, ctx):
        await ctx.claim_file("src/auth.py", "agent_b")
        text = ctx.format_for_prompt("agent_a")
        assert "src/auth.py" in text
        assert "agent_b" in text
        assert "Avoid re-reading" in text

    @pytest.mark.asyncio
    async def test_format_excludes_own_file_claims(self, ctx):
        await ctx.claim_file("src/auth.py", "agent_a")
        text = ctx.format_for_prompt("agent_a")
        assert "Files Already Being Read" not in text

    @pytest.mark.asyncio
    async def test_format_truncates_long_values(self, ctx):
        long_value = "x" * 600
        await ctx.post("agent_b", "big_finding", long_value)
        text = ctx.format_for_prompt("agent_a")
        assert "..." in text
        assert len(text) < len(long_value) + 200


class TestSharedContextSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_receives_findings(self, ctx):
        queue = ctx.subscribe()
        await ctx.post("agent_a", "key1", "value1")
        # Give the queue a moment
        await asyncio.sleep(0.01)
        finding = queue.get_nowait()
        assert finding.key == "key1"
        ctx.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_receiving(self, ctx):
        queue = ctx.subscribe()
        ctx.unsubscribe(queue)
        await ctx.post("agent_a", "key1", "value1")
        await asyncio.sleep(0.01)
        assert queue.empty()


class TestSharedContextSerialize:
    @pytest.mark.asyncio
    async def test_to_dict(self, ctx):
        await ctx.post("agent_a", "key1", "value1", tags=["bug"])
        await ctx.claim_file("a.py", "agent_b")
        d = ctx.to_dict()
        assert "findings" in d
        assert "key1" in d["findings"]
        assert d["findings"]["key1"]["agent_id"] == "agent_a"
        assert "a.py" in d["file_claims"]


class TestSharedContextToolIntegration:
    def test_tool_schema_structure(self):
        schema = build_shared_context_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "share_finding"
        assert "key" in schema["function"]["parameters"]["properties"]
        assert "value" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_tool_impl_posts_finding(self, ctx):
        impl = build_shared_context_tool_impl("agent_a", ctx)
        result = await impl(key="discovery", value="found a bug in parser.py", tags=["bug"])
        assert result["status"] == "ok"
        finding = await ctx.get("discovery")
        assert finding is not None
        assert finding.value == "found a bug in parser.py"
        assert finding.agent_id == "agent_a"
