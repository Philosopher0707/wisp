"""Composable subagent patterns: map-reduce, vote, chain.

These are higher-order functions that operate on a ``SubagentOrchestrator``
instance to compose multiple subagent runs into structured workflows.
"""

from __future__ import annotations

import copy
import logging
from typing import Callable

from .task import SubagentContract, SubagentResult

logger = logging.getLogger(__name__)


async def run_map_reduce(
    orchestrator,
    task: str,
    items: list[str],
    mapper: Callable[[str], SubagentContract],
    reducer: str,
    max_concurrent: int = 4,
    retry_failed: bool = True,
) -> SubagentResult:
    """Map-reduce: split work across mappers, then synthesize with a reducer."""
    if not items:
        return SubagentResult(
            task_id="map-reduce",
            success=False,
            output="[MAP-REDUCE FAILED] No items provided to process.",
            error="No items provided",
            elapsed_seconds=0.0,
        )

    # Map phase
    mapper_contracts = [mapper(item) for item in items]
    mapper_results = await orchestrator.run_parallel(mapper_contracts, max_concurrent)

    # Retry failed mappers
    if retry_failed:
        retry_contracts = []
        retry_indices = []
        for i, r in enumerate(mapper_results):
            if not r.success and not r.timed_out:
                contract = mapper_contracts[i]
                retry_contract = SubagentContract(
                    **{
                        **{k: v for k, v in contract.__dict__.items()
                           if k in SubagentContract.__dataclass_fields__},
                        "task": (
                            f"{contract.task}\n\n"
                            f"IMPORTANT: Previous attempt failed: {r.error or 'unknown'}. "
                            f"Please try again with a different approach."
                        ),
                    }
                )
                retry_contracts.append(retry_contract)
                retry_indices.append(i)

        if retry_contracts:
            logger.info("Retrying %d failed mapper(s)", len(retry_contracts))
            retry_results = await orchestrator.run_parallel(retry_contracts, max_concurrent)
            for idx, r_result in zip(retry_indices, retry_results):
                if r_result.success:
                    mapper_results[idx] = r_result

    # Build reducer input
    successful = [r for r in mapper_results if r.success]
    failed = [r for r in mapper_results if not r.success]

    parts = [f"## Overall Task\n{task}\n"]
    parts.append(f"## Mapper Results ({len(successful)}/{len(mapper_results)} succeeded)\n")

    for i, r in enumerate(successful):
        parts.append(f"### Mapper {i+1}: {r.task_id}\n")
        parts.append(r.output[:2000])
        if len(r.output) > 2000:
            parts.append("\n... [truncated]\n")
        parts.append("\n")

    if failed:
        parts.append(f"## Failed Mappers ({len(failed)})\n")
        for r in failed:
            parts.append(f"- {r.task_id}: {r.error or 'unknown error'}\n")

    reducer_input = "\n".join(parts)

    # Guard: truncate if too large
    from wisp.infra.token_counter import TokenCounter
    chars_per_token = getattr(orchestrator.config, "chars_per_token", 4)
    counter = TokenCounter(chars_per_token=chars_per_token)
    estimated_tokens = counter.estimate_chars(len(reducer_input))
    max_tokens = orchestrator.config.max_context_tokens * 0.8
    if estimated_tokens > max_tokens:
        logger.warning(
            "Reducer input %d tokens exceeds budget %d. Truncating.",
            estimated_tokens, max_tokens,
        )
        truncated_parts = parts[:2]
        budget_per_mapper = int(max_tokens * 4 // len(successful)) if successful else 1000
        for i, r in enumerate(successful):
            truncated_parts.append(f"### Mapper {i+1}: {r.task_id}\n")
            truncated_parts.append(r.output[:budget_per_mapper])
            if len(r.output) > budget_per_mapper:
                truncated_parts.append("\n... [truncated]\n")
            truncated_parts.append("\n")
        if failed:
            truncated_parts.append(f"## Failed Mappers ({len(failed)})\n")
            for r in failed:
                truncated_parts.append(f"- {r.task_id}: {r.error or 'unknown error'}\n")
        reducer_input = "\n".join(truncated_parts)

    # Reduce phase
    reducer_contract = SubagentContract(
        name="reducer",
        role="generalist",
        task=f"{reducer}\n\n{reducer_input}",
        max_iterations=15,
        timeout_seconds=120.0,
        worktree_isolated=False,
    )

    reducer_result = await orchestrator.run(reducer_contract)
    mapper_tokens = sum(r.tokens_used for r in mapper_results)
    reducer_result.tokens_used += mapper_tokens
    return reducer_result


async def run_vote(
    orchestrator,
    task: str,
    agents: list[SubagentContract],
    consensus_threshold: float = 0.6,
    max_concurrent: int = 4,
) -> SubagentResult:
    """Vote: ask multiple independent subagents the same question, take majority."""
    if not agents:
        return SubagentResult(
            task_id="vote",
            success=False,
            output="[VOTE FAILED] No agents provided for voting.",
            error="No agents provided",
            elapsed_seconds=0.0,
        )

    voting_contracts = []
    for agent in agents:
        c = SubagentContract(**{**agent.__dict__, "task": task})
        voting_contracts.append(c)

    results = await orchestrator.run_parallel(voting_contracts, max_concurrent)

    successful = [r for r in results if r.success]
    total = len(results)
    passed = len(successful)

    if total == 0:
        return SubagentResult(
            task_id="vote",
            success=False,
            output="[VOTE FAILED] No voting agents executed.",
            error="No results from voting agents",
            elapsed_seconds=0.0,
        )

    # Robust consensus: group by normalized similarity

    def _normalize(text: str) -> str:
        return " ".join(text.lower().strip().split())

    def _similar(a: str, b: str) -> bool:
        na, nb = _normalize(a), _normalize(b)
        if na == nb:
            return True
        if len(na) <= 10 and len(nb) <= 10:
            if len(na) > len(nb):
                return nb in na
            return na in nb
        return False

    groups: list[list[str]] = []
    for r in successful:
        out = r.output.strip()[:500]
        placed = False
        for g in groups:
            if _similar(out, g[0]):
                g.append(out)
                placed = True
                break
        if not placed:
            groups.append([out])

    if groups:
        winner_group = max(groups, key=len)
        winner = winner_group[0]
        count = len(winner_group)
        consensus_reached = count / total >= consensus_threshold

        # Tie-breaker
        if len(groups) >= 2:
            sorted_groups = sorted(groups, key=len, reverse=True)
            if len(sorted_groups[0]) == len(sorted_groups[1]):
                logger.info("Vote tie detected (%d-%d), running tie-breaker",
                            len(sorted_groups[0]), len(sorted_groups[1]))
                tie_contract = SubagentContract(
                    name="tie-breaker",
                    role="generalist",
                    task=(
                        f"Break this tie vote.\n\n"
                        f"Question: {task}\n\n"
                        f"Option A ({len(sorted_groups[0])} votes):\n"
                        f"{sorted_groups[0][0][:500]}\n\n"
                        f"Option B ({len(sorted_groups[1])} votes):\n"
                        f"{sorted_groups[1][0][:500]}\n\n"
                        f"Which option is better? Respond with 'A' or 'B' and a brief reason."
                    ),
                    timeout_seconds=30,
                    max_iterations=5,
                )
                tie_result = await orchestrator.run(tie_contract)
                if tie_result.success:
                    if "A" in tie_result.output.upper():
                        winner = sorted_groups[0][0]
                        count = len(sorted_groups[0]) + 1
                        total += 1
                    elif "B" in tie_result.output.upper():
                        winner = sorted_groups[1][0]
                        count = len(sorted_groups[1]) + 1
                        total += 1
                consensus_reached = count / total >= consensus_threshold
    else:
        winner = ""
        count = 0
        consensus_reached = False

    lines = [
        f"## Vote Result: {task[:100]}",
        "",
        f"**Consensus:** {'REACHED' if consensus_reached else 'NOT REACHED'}",
        f"**Agreement:** {count}/{total} ({count/total*100:.0f}%) — threshold {consensus_threshold*100:.0f}%",
        "",
        "### Individual Votes",
    ]
    for i, r in enumerate(results):
        status = "✓" if r.success else "✗"
        match = " (matches winner)" if r.success and _similar(r.output.strip()[:500], winner) else ""
        lines.append(f"{status} Agent {i+1} ({r.task_id}):{match}")
        if r.error:
            lines.append(f"   Error: {r.error}")

    lines.append("")
    lines.append("### Winning Answer")
    lines.append(winner if winner else "(no consensus)")

    return SubagentResult(
        task_id="vote",
        success=consensus_reached,
        output="\n".join(lines),
        elapsed_seconds=sum(r.elapsed_seconds for r in results),
        iterations_used=sum(r.iterations_used for r in results),
        files_changed=list(set(f for r in results for f in r.files_changed)),
        input_tokens=sum(r.input_tokens for r in results),
        output_tokens=sum(r.output_tokens for r in results),
        tokens_used=sum(r.tokens_used for r in results),
    )


async def run_chain(
    orchestrator,
    contracts: list[SubagentContract],
    pass_context: bool = True,
    max_concurrent: int = 1,
    continue_on_error: bool = False,
) -> SubagentResult:
    """Chain: run subagents sequentially, optionally passing context forward."""
    if max_concurrent != 1:
        logger.warning("run_chain with max_concurrent > 1 breaks sequential context passing")

    context_parts = []
    last_result = None
    all_files_changed = []
    total_elapsed = 0.0
    total_iterations = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    failed_steps = []

    shared_worktree_path = None
    chain_patch = None
    try:
        any_isolated = any(c.worktree_isolated for c in contracts)
        if any_isolated:
            import uuid
            short_id = str(uuid.uuid4())[:8]
            try:
                shared_worktree_path = await orchestrator._worktree_mgr.create(f"chain-{short_id}")
            except Exception as exc:
                logger.warning("Failed to create shared chain worktree, falling back to unisolated: %s", exc)

        for i, contract in enumerate(contracts):
            copied = copy.deepcopy(contract.__dict__)
            
            # Use the shared worktree for all steps in the chain
            if shared_worktree_path and copied.get("worktree_isolated", True):
                copied["worktree_isolated"] = False
                copied["workspace"] = str(shared_worktree_path)

            if pass_context and context_parts:
                context_block = "\n\n".join(context_parts[-3:])
                copied["task"] = (
                    f"{copied['task']}\n\n"
                    f"## Previous Steps Context\n"
                    f"{context_block}"
                )
                
            contract = SubagentContract(**copied)

            result = await orchestrator.run(contract)
            last_result = result
            all_files_changed.extend(result.files_changed)
            total_elapsed += result.elapsed_seconds
            total_iterations += result.iterations_used
            total_input_tokens += result.input_tokens
            total_output_tokens += result.output_tokens
            total_tokens += result.tokens_used

            if pass_context:
                context_parts.append(
                    f"### Step {i+1}: {contract.name}\n"
                    f"{result.output[:1500]}"
                )

            if not result.success:
                failed_steps.append((i + 1, contract.name, result.error))
                if not continue_on_error:
                    output = (
                        f"## Chain Failed at Step {i+1}/{len(contracts)}\n\n"
                        f"**Failed step:** {contract.name}\n"
                        f"**Error:** {result.error or 'unknown error'}\n\n"
                        f"### Completed Steps\n"
                        + "\n\n".join(context_parts[:-1] if context_parts else [])
                    )
                    last_result = SubagentResult(
                        task_id=f"chain-failed-at-{i+1}",
                        success=False,
                        output=output,
                        elapsed_seconds=total_elapsed,
                        iterations_used=total_iterations,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        tokens_used=total_tokens,
                        files_changed=list(set(all_files_changed)),
                        error=result.error,
                    )
                    break
    finally:
        if shared_worktree_path:
            import os
            try:
                chain_patch = await orchestrator._worktree_mgr.get_patch(shared_worktree_path)
                if not os.environ.get("WISP_KEEP_WORKTREES", "").lower() == "true":
                    await orchestrator._worktree_mgr.cleanup(shared_worktree_path)
            except Exception as exc:
                logger.warning("Failed to clean up shared chain worktree %s: %s", shared_worktree_path, exc)

    if last_result is None:
        return SubagentResult(
            task_id="chain-empty",
            success=True,
            output="(empty chain)",
        )

    success = len(failed_steps) == 0
    if not hasattr(last_result, "task_id") or not last_result.task_id.startswith("chain-failed"):
        output_lines = [f"## Chain Complete ({len(contracts)} steps)"]
        if failed_steps:
            output_lines.append(f"\n**Failed steps:** {len(failed_steps)}")
            for step_num, name, error in failed_steps:
                output_lines.append(f"- Step {step_num} ({name}): {error or 'unknown'}")
        output_lines.append(f"\n{last_result.output}")
        output_lines.append(
            f"\n---\n"
            f"*Chain elapsed: {total_elapsed:.1f}s, "
            f"iterations: {total_iterations}, "
            f"tokens: {total_tokens}*"
        )
        last_result.output = "\n".join(output_lines)
        last_result.elapsed_seconds = total_elapsed
        last_result.iterations_used = total_iterations
        last_result.input_tokens = total_input_tokens
        last_result.output_tokens = total_output_tokens
        last_result.tokens_used = total_tokens
        last_result.files_changed = list(set(all_files_changed))
        last_result.success = success
    
    last_result.worktree_patch = chain_patch
    return last_result
