/** Composable subagent patterns: map-reduce, vote, chain. */

import { SubagentContract, SubagentResult, OrchestratorEvent, EventKind } from "./task.js";
import { SubagentOrchestrator } from "./orchestrator.js";

export async function runMapReduce(
  orchestrator: SubagentOrchestrator,
  task: string,
  items: string[],
  mapper: (item: string) => SubagentContract,
  reducer: string,
  maxConcurrent = 4,
  retryFailed = true
): Promise<SubagentResult> {
  if (!items.length) {
    return new SubagentResult({ taskId: "map-reduce", success: false, output: "[MAP-REDUCE FAILED] No items provided.", error: "No items provided" });
  }

  const mapperContracts = items.map(mapper);
  const mapperResults = await orchestrator.runParallel(mapperContracts, maxConcurrent);

  if (retryFailed) {
    const retryContracts: SubagentContract[] = [];
    const retryIndices: number[] = [];
    for (let i = 0; i < mapperResults.length; i++) {
      const r = mapperResults[i];
      if (!r.success && !r.timedOut) {
        const c = mapperContracts[i];
        retryContracts.push(new SubagentContract({
          ...c,
          task: `${c.task}\n\nIMPORTANT: Previous attempt failed: ${r.error ?? "unknown"}. Please try again with a different approach.`,
        }));
        retryIndices.push(i);
      }
    }
    if (retryContracts.length > 0) {
      const retryResults = await orchestrator.runParallel(retryContracts, maxConcurrent);
      for (let j = 0; j < retryIndices.length; j++) {
        if (retryResults[j].success) mapperResults[retryIndices[j]] = retryResults[j];
      }
    }
  }

  const successful = mapperResults.filter((r) => r.success);
  const failed = mapperResults.filter((r) => !r.success);

  const parts = [`## Overall Task\n${task}\n`, `## Mapper Results (${successful.length}/${mapperResults.length} succeeded)\n`];
  for (let i = 0; i < successful.length; i++) {
    const r = successful[i];
    parts.push(`### Mapper ${i + 1}: ${r.taskId}\n`);
    parts.push(r.output.slice(0, 2000));
    if (r.output.length > 2000) parts.push("\n... [truncated]\n");
    parts.push("\n");
  }
  if (failed.length > 0) {
    parts.push(`## Failed Mappers (${failed.length})\n`);
    for (const r of failed) {
      parts.push(`- ${r.taskId}: ${r.error ?? "unknown error"}\n`);
    }
  }

  const reducerInput = parts.join("");
  const reducerContract = new SubagentContract({
    name: "reducer",
    role: "generalist",
    task: `${reducer}\n\n${reducerInput}`,
    maxIterations: 15,
    timeoutSeconds: 120,
    worktreeIsolated: false,
  });

  const reducerResult = await orchestrator.run(reducerContract);
  const mapperTokens = mapperResults.reduce((sum, r) => sum + r.tokensUsed, 0);
  reducerResult.tokensUsed += mapperTokens;
  return reducerResult;
}

export async function runVote(
  orchestrator: SubagentOrchestrator,
  task: string,
  agents: SubagentContract[],
  consensusThreshold = 0.6,
  maxConcurrent = 4
): Promise<SubagentResult> {
  if (!agents.length) {
    return new SubagentResult({ taskId: "vote", success: false, output: "[VOTE FAILED] No agents provided.", error: "No agents provided" });
  }

  const votingContracts = agents.map((a) => new SubagentContract({ ...a, task }));
  const results = await orchestrator.runParallel(votingContracts, maxConcurrent);

  const successful = results.filter((r) => r.success);
  let total = results.length;
  const passed = successful.length;

  if (total === 0) {
    return new SubagentResult({ taskId: "vote", success: false, output: "[VOTE FAILED] No voting agents executed.", error: "No results" });
  }

  function _normalize(text: string): string {
    return text.toLowerCase().trim().split(/\s+/).join(" ");
  }

  function _similar(a: string, b: string): boolean {
    const na = _normalize(a), nb = _normalize(b);
    if (na === nb) return true;
    if (na.length <= 10 && nb.length <= 10) return na.includes(nb) || nb.includes(na);
    return false;
  }

  const groups: string[][] = [];
  for (const r of successful) {
    const out = r.output.trim().slice(0, 500);
    let placed = false;
    for (const g of groups) {
      if (_similar(out, g[0])) { g.push(out); placed = true; break; }
    }
    if (!placed) groups.push([out]);
  }

  let winner = "";
  let count = 0;
  let consensusReached = false;

  if (groups.length > 0) {
    const winnerGroup = groups.reduce((a, b) => (a.length >= b.length ? a : b));
    winner = winnerGroup[0];
    count = winnerGroup.length;
    consensusReached = count / total >= consensusThreshold;

    if (groups.length >= 2) {
      const sortedGroups = [...groups].sort((a, b) => b.length - a.length);
      if (sortedGroups[0].length === sortedGroups[1].length) {
        const tieContract = new SubagentContract({
          name: "tie-breaker",
          role: "generalist",
          task: `Break this tie vote.\n\nQuestion: ${task}\n\nOption A (${sortedGroups[0].length} votes):\n${sortedGroups[0][0].slice(0, 500)}\n\nOption B (${sortedGroups[1].length} votes):\n${sortedGroups[1][0].slice(0, 500)}\n\nWhich option is better? Respond with 'A' or 'B' and a brief reason.`,
          timeoutSeconds: 30,
          maxIterations: 5,
        });
        const tieResult = await orchestrator.run(tieContract);
        if (tieResult.success) {
          if (tieResult.output.toUpperCase().includes("A")) {
            winner = sortedGroups[0][0]; count = sortedGroups[0].length + 1; total += 1;
          } else if (tieResult.output.toUpperCase().includes("B")) {
            winner = sortedGroups[1][0]; count = sortedGroups[1].length + 1; total += 1;
          }
        }
        consensusReached = count / total >= consensusThreshold;
      }
    }
  }

  const lines = [
    `## Vote Result: ${task.slice(0, 100)}`,
    "",
    `**Consensus:** ${consensusReached ? "REACHED" : "NOT REACHED"}`,
    `**Agreement:** ${count}/${total} (${Math.round((count / total) * 100)}%) — threshold ${Math.round(consensusThreshold * 100)}%`,
    "",
    "### Individual Votes",
  ];
  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    const status = r.success ? "✓" : "✗";
    const match = r.success && _similar(r.output.trim().slice(0, 500), winner) ? " (matches winner)" : "";
    lines.push(`${status} Agent ${i + 1} (${r.taskId}):${match}`);
    if (r.error) lines.push(`   Error: ${r.error}`);
  }
  lines.push("", "### Winning Answer", winner || "(no consensus)");

  return new SubagentResult({
    taskId: "vote",
    success: consensusReached,
    output: lines.join("\n"),
    elapsedSeconds: results.reduce((sum, r) => sum + r.elapsedSeconds, 0),
    iterationsUsed: results.reduce((sum, r) => sum + r.iterationsUsed, 0),
    filesChanged: Array.from(new Set(results.flatMap((r) => r.filesChanged))),
    inputTokens: results.reduce((sum, r) => sum + r.inputTokens, 0),
    outputTokens: results.reduce((sum, r) => sum + r.outputTokens, 0),
    tokensUsed: results.reduce((sum, r) => sum + r.tokensUsed, 0),
  });
}

export async function runChain(
  orchestrator: SubagentOrchestrator,
  contracts: SubagentContract[],
  passContext = true,
  _maxConcurrent = 1,
  continueOnError = false
): Promise<SubagentResult> {
  const contextParts: string[] = [];
  let lastResult: SubagentResult | null = null;
  const allFilesChanged: string[] = [];
  let totalElapsed = 0;
  let totalIterations = 0;
  let totalTokens = 0;
  const failedSteps: Array<{ step: number; name: string; error: string | null }> = [];

  for (let i = 0; i < contracts.length; i++) {
    const contract = new SubagentContract({ ...contracts[i] });
    if (passContext && contextParts.length > 0) {
      const contextBlock = contextParts.slice(-3).join("\n\n");
      contract.task = `${contract.task}\n\n## Previous Steps Context\n${contextBlock}`;
    }

    const result = await orchestrator.run(contract);
    lastResult = result;
    allFilesChanged.push(...result.filesChanged);
    totalElapsed += result.elapsedSeconds;
    totalIterations += result.iterationsUsed;
    totalTokens += result.tokensUsed;

    if (passContext) {
      contextParts.push(`### Step ${i + 1}: ${contract.name}\n${result.output.slice(0, 1500)}`);
    }

    if (!result.success) {
      failedSteps.push({ step: i + 1, name: contract.name, error: result.error });
      if (!continueOnError) {
        const outputLines = [
          `## Chain Failed at Step ${i + 1}/${contracts.length}`,
          `**Failed step:** ${contract.name}`,
          `**Error:** ${result.error ?? "unknown error"}`,
          "",
          "### Completed Steps",
          ...contextParts.slice(0, -1),
        ];
        return new SubagentResult({
          taskId: `chain-failed-at-${i + 1}`,
          success: false,
          output: outputLines.join("\n"),
          elapsedSeconds: totalElapsed,
          iterationsUsed: totalIterations,
          tokensUsed: totalTokens,
          filesChanged: Array.from(new Set(allFilesChanged)),
          error: result.error,
        });
      }
    }
  }

  if (!lastResult) {
    return new SubagentResult({ taskId: "chain-empty", success: true, output: "(empty chain)" });
  }

  const success = failedSteps.length === 0;
  const outputLines = [`## Chain Complete (${contracts.length} steps)`];
  if (failedSteps.length > 0) {
    outputLines.push(`\n**Failed steps:** ${failedSteps.length}`);
    for (const f of failedSteps) {
      outputLines.push(`- Step ${f.step} (${f.name}): ${f.error ?? "unknown"}`);
    }
  }
  outputLines.push(`\n${lastResult.output}`);
  outputLines.push(`\n---\n*Chain elapsed: ${totalElapsed.toFixed(1)}s, iterations: ${totalIterations}, tokens: ${totalTokens}*`);

  return new SubagentResult({
    taskId: "chain",
    success,
    output: outputLines.join("\n"),
    elapsedSeconds: totalElapsed,
    iterationsUsed: totalIterations,
    tokensUsed: totalTokens,
    filesChanged: Array.from(new Set(allFilesChanged)),
  });
}
