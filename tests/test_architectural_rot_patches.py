"""Tests for the architectural rot security and performance patches.

Covers:
  - WorkspaceTrustManager (trusted/untrusted path loading)
  - Hooks Auditing log files (creation, parameter scrubbing)
  - Thread-safe AgentMetrics (RLock race guard)
  - SemanticIndex in-memory vector cache invalidation on SQLite DB writes
"""

import os
import json
import tempfile
import threading
from pathlib import Path
import pytest
import numpy as np

from wisp.trust import WorkspaceTrustManager
from wisp.hooks import HookManager, HookEvent, build_hook_context
from wisp.metrics import AgentMetrics
from wisp.semantic_index import SemanticIndex, SearchResult
from wisp.async_utils import sync_gen_iter, _GEN_EXECUTOR


def test_workspace_trust_manager():
    """Verify WorkspaceTrustManager correctly tracks trusted workspaces and honors WISP_TRUST_ALL_WORKSPACES."""
    # Save environment variable state to prevent CI pollution
    env_backup = os.environ.get("WISP_TRUST_ALL_WORKSPACES")
    if env_backup is not None:
        del os.environ["WISP_TRUST_ALL_WORKSPACES"]
        
    try:
        with tempfile.TemporaryDirectory() as td:
            trust_file = Path(td) / "trusted.json"
            
            # Test default/untrusted state
            old_trust_file = WorkspaceTrustManager.TRUST_FILE
            WorkspaceTrustManager.TRUST_FILE = trust_file
            try:
                workspace_path = Path(td) / "project-a"
                workspace_path.mkdir()
                
                assert WorkspaceTrustManager.is_workspace_trusted(str(workspace_path)) is False
                
                # Add to trusted list
                WorkspaceTrustManager.trust_workspace(str(workspace_path))
                assert WorkspaceTrustManager.is_workspace_trusted(str(workspace_path)) is True
                
                # Test override environment variable
                another_path = Path(td) / "project-b"
                another_path.mkdir()
                assert WorkspaceTrustManager.is_workspace_trusted(str(another_path)) is False
                
                os.environ["WISP_TRUST_ALL_WORKSPACES"] = "true"
                assert WorkspaceTrustManager.is_workspace_trusted(str(another_path)) is True
            finally:
                WorkspaceTrustManager.TRUST_FILE = old_trust_file
    finally:
        if env_backup is not None:
            os.environ["WISP_TRUST_ALL_WORKSPACES"] = env_backup


@pytest.mark.asyncio
async def test_hooks_audit_logging_and_scrubbing():
    """Verify that custom hook execution timing is logged and sensitive parameter values are scrubbed."""
    env_backup = os.environ.get("WISP_TRUST_ALL_WORKSPACES")
    if env_backup is not None:
        del os.environ["WISP_TRUST_ALL_WORKSPACES"]
        
    try:
        with tempfile.TemporaryDirectory() as td:
            # Trust workspace so hooks are loaded
            old_trust_file = WorkspaceTrustManager.TRUST_FILE
            WorkspaceTrustManager.TRUST_FILE = Path(td) / "trusted.json"
            try:
                WorkspaceTrustManager.trust_workspace(td)
                
                # Create HookManager using this trusted workspace
                mgr = HookManager(workspace=Path(td))
                
                # Write a dummy script hook
                hooks_dir = Path(td) / ".wisp" / "hooks"
                hooks_dir.mkdir(parents=True)
                hook_script = hooks_dir / "PRE_BASH_test.sh"
                hook_script.write_text("#!/bin/bash\necho '{\"action\":\"allow\"}'")
                hook_script.chmod(0o755)
                
                mgr.load_project_hooks()
                assert mgr.hook_count >= 1
                
                # Execute hook and pass sensitive key contents
                sensitive_args = {
                    "command": "rm -rf sensitive_dir",
                    "content": "extremely secret API keys inside this block",
                    "safe_key": "safe_value"
                }
                
                # Run the PRE_BASH hook via run_hooks
                context = build_hook_context(
                    event=HookEvent.PRE_BASH,
                    tool_name="run_bash",
                    tool_args=sensitive_args,
                    workspace=td,
                )
                
                # We need to trust the workspace during run
                WorkspaceTrustManager.trust_workspace(td)
                await mgr.run_hooks(HookEvent.PRE_BASH, context)
                
                # Verify hook audit log exists and matches formatting
                audit_log_file = Path(td) / ".wisp" / "hooks_audit.jsonl"
                assert audit_log_file.exists()
                
                lines = audit_log_file.read_text().splitlines()
                assert len(lines) >= 1
                
                audit_entry = json.loads(lines[0])
                assert "event" in audit_entry
                assert "duration_seconds" in audit_entry
                assert "timestamp" in audit_entry
                
                # Check parameter scrubbing
                params = audit_entry.get("tool_args", {})
                assert "safe_key" in params
                assert params["safe_key"] == "safe_value"
                assert "command" in params
                assert "[scrubbed" in params["command"]
                assert "content" in params
                assert "[scrubbed" in params["content"]
            finally:
                WorkspaceTrustManager.TRUST_FILE = old_trust_file
    finally:
        if env_backup is not None:
            os.environ["WISP_TRUST_ALL_WORKSPACES"] = env_backup


def test_thread_safe_agent_metrics():
    """Verify that multiple concurrent threads can mutate AgentMetrics concurrently without race corruptions."""
    metrics = AgentMetrics()
    
    def worker():
        for _ in range(100):
            metrics.record_turn(latency_s=0.01, prompt_chars=40, completion_chars=80)
            metrics.record_tool("run_bash", duration_ms=25.0, success=True)
            metrics.record_tool_block()
            metrics.record_tool_approval(approved=True)
            metrics.record_compaction()
            metrics.record_interruption()
            
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    snap = metrics.snapshot()
    assert snap["turns"] == 1000
    assert snap["tool_calls"] == 1000
    assert snap["tool_blocks"] == 1000
    assert snap["tool_approvals"] == 1000
    assert snap["compactions"] == 1000
    assert snap["interruptions"] == 1000
    
    metrics.reset()
    assert metrics.turns == 0


def test_semantic_index_in_memory_cache():
    """Verify that the SemanticIndex vector cache invalidates correctly on index changes."""
    import sqlite3
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "semantic_index.db"
        
        # Create a mock database structure matching exact expected column names
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE files (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                file_hash TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                content TEXT NOT NULL,
                symbol_name TEXT DEFAULT '',
                content_hash TEXT NOT NULL,
                UNIQUE(file_path, start_line, end_line)
            )
        """)
        conn.execute("""
            CREATE TABLE embeddings (
                chunk_id INTEGER PRIMARY KEY,
                embedding BLOB NOT NULL,
                FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
            )
        """)
        
        # Insert a chunk
        conn.execute("INSERT INTO files VALUES ('foo.py', 123.45, 'hash1', 1)")
        conn.execute("INSERT INTO chunks VALUES (1, 'foo.py', 1, 10, 'def foo(): pass', 'foo', 'chash')")
        
        # Insert dummy embedding (dimension 4, float64 = 32 bytes)
        emb_arr = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float64)
        conn.execute("INSERT INTO embeddings VALUES (1, ?)", (emb_arr.tobytes(),))
        conn.commit()
        conn.close()
        
        # Instantiate SemanticIndex and override its mock _embed
        idx = SemanticIndex(workspace=td, db_path=str(db_path))
        idx._embed = lambda queries: [[0.1, 0.2, 0.3, 0.4]]
        
        # First search should query and populate cache
        res = idx.search("hello", top_k=1)
        assert len(res) == 1
        assert res[0].file_path == "foo.py"
        assert idx._cache_key is not None
        assert idx._cache_M is not None
        
        # Save cached state
        cached_M_ref = idx._cache_M
        
        # Subsequent search should hit the cache (same matrix reference)
        res2 = idx.search("hello", top_k=1)
        assert idx._cache_M is cached_M_ref
        
        # Now modify files table (simulate file update or reindex)
        idx.conn.execute("UPDATE files SET mtime = 200.0 WHERE path = 'foo.py'")
        idx.conn.commit()
        
        # Next search should detect changed key, invalidate cache, and reload
        res3 = idx.search("hello", top_k=1)
        assert idx._cache_M is not cached_M_ref  # cache was re-loaded!
        assert idx._cache_key[1] == 200.0


def test_context_assembler_accurate_token_math_and_markdown_safety():
    """Verify that ContextAssembler uses tiktoken for accurate estimation, slices correctly, and fixes unclosed markdown code blocks."""
    from wisp.context_assembler import ContextAssembler
    assembler = ContextAssembler()
    
    # 1. Verify accurate token estimation on complex code vs simple text
    code_text = "def fetch_data(url: str):\n    # TODO: implement this block\n    pass"
    est = assembler._estimate_tokens(code_text)
    
    # Since tiktoken is installed in this test environment, it should use the tiktoken encoder
    import tiktoken
    encoder = tiktoken.get_encoding("cl100k_base")
    expected_tokens = len(encoder.encode(code_text))
    assert est == expected_tokens
    
    # 2. Verify markdown structure preservation on truncation
    sections = [
        ("default_system", 0, "BASE SYSTEM INSTRUCTION WITH OPEN CODE BLOCK:\n```python\ndef run_code():\n    return 42"),
    ]
    system, usage = assembler._fit_sections(sections, max_tokens=15)
    
    # Verify the code block was closed automatically with [Code block truncated]
    assert "[Code block truncated]" in system
    assert system.count("```") % 2 == 0
