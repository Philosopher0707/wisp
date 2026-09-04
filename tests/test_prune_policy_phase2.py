"""Phase 2.3 RED tests — PrunePolicy as the single pruning contract.

Target: prune_messages accepts PrunePolicy (contracts) as well as the
legacy PrunerConfig, with identical ceilings. Call sites reference the
shared policy instead of bare defaults.
"""

from __future__ import annotations


def _messages_with_tools(n: int = 5):
    msgs: list[dict] = [{"role": "user", "content": "hi"}]
    for i in range(n):
        msgs.append({
            "role": "tool",
            "name": "read_file",
            "content": "line\n" * 2000 + f" #{i}",
        })
    return msgs


def test_prune_messages_accepts_prune_policy():
    from wisp.core.context_pruner import prune_messages
    from wisp.core.contracts import PrunePolicy

    out = prune_messages(_messages_with_tools(), PrunePolicy(keep_last_n_full=1))
    assert isinstance(out, list)
    assert len(out) == 6


def test_prune_policy_keep_n_changes_output():
    from wisp.core.context_pruner import prune_messages
    from wisp.core.contracts import PrunePolicy

    msgs = _messages_with_tools()
    keep1 = prune_messages(msgs, PrunePolicy(keep_last_n_full=1))
    keep4 = prune_messages(msgs, PrunePolicy(keep_last_n_full=4))
    # Fewer kept-full → older results condensed → smaller or equal payload.
    size = lambda ms: sum(len(str(m.get("content", ""))) for m in ms)
    assert size(keep1) <= size(keep4)


def test_policy_to_config_conversion_matches_ceilings():
    from wisp.core.context_pruner import pruner_config_from_policy
    from wisp.core.contracts import PrunePolicy

    cfg = pruner_config_from_policy(PrunePolicy())
    assert cfg.keep_last_n_full == 3
    assert cfg.max_bytes_per_historical_result == 8192
    assert cfg.max_bytes_per_recent_result == 50000
    assert cfg.max_total_bytes == 200000
    assert cfg.read_file_historical_max_bytes == 2048


def test_callsites_reference_shared_policy():
    import pathlib

    for rel in ("wisp/core/stateless.py", "wisp/ollama_client.py", "wisp/providers/openai.py"):
        src = pathlib.Path(rel).read_text()
        assert "PrunePolicy" in src or "PRUNE_POLICY" in src, f"{rel} does not reference PrunePolicy"
