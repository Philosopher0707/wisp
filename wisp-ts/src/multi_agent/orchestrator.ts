/** SubagentOrchestrator — thin coordinator for subagent execution. */

import { WispConfig } from "../config.js";
import { SubagentContract, SubagentResult, OrchestratorEvent, EventKind } from "./task.js";
import { ROLE_CONFIGS } from "./roles.js";
import { WorktreeManager } from "./worktree.js";
import { WispAgentCore } from "../core/engine.js";
import { ProviderFactory } from "../providers/factory.js";
import { SecurityPolicy } from "../infra/security.js";
import { PermissionMode } from "../config.js";
import { ToolRegistry } from "../tools/registry.js";
import { TokenCounter } from "../infra/token_counter.js";
import { UnifiedStore } from "../infra/store.js";

// ── BudgetTracker ─────────────────────────────────────────────────

export class BudgetTracker {
  private _consumed = 0;
  private _globalBudget: number | null = null;

  setBudget(budget: number | null): void { this._globalBudget = budget; }
  getConsumed(): number { return this._consumed; }
  getRemaining(): number | null {
    if (this._globalBudget === null) return null;
    return Math.max(0, this._globalBudget - this._consumed);
  }
  getRatio(): number | null {
    if (!this._globalBudget || this._globalBudget <= 0) return null;
    return Math.max(0, this.getRemaining()! / this._globalBudget);
  }
  check(): string | null {
    const remaining = this.getRemaining();
    if (remaining !== null && remaining <= 0) return `Global token budget exhausted (${this._globalBudget} tokens)`;
    return null;
  }
  record(tokens: number): void { this._consumed += tokens; }
  removeBudget(): void { this._globalBudget = null; }
}

// ── ResultCache ───────────────────────────────────────────────────

export class ResultCache {
  private _cache = new Map<string, { result: SubagentResult; ts: number }>();
  private _hits = 0;
  private _misses = 0;

  private _key(contract: SubagentContract): string {
    const parts = [
      contract.task,
      contract.role,
      contract.tools.sort().join(","),
      contract.model ?? "",
      contract.workspace ?? "",
      contract.outputFormat,
      JSON.stringify(contract.outputSchema ?? {}),
      contract.systemPrompt ?? "",
      contract.cacheContext,
    ];
    let hash = 0;
    const str = parts.join("|");
    for (let i = 0; i < str.length; i++) {
      const c = str.charCodeAt(i);
      hash = ((hash << 5) - hash + c) | 0;
    }
    return hash.toString(16).padStart(8, "0");
  }

  get(contract: SubagentContract): SubagentResult | null {
    const key = this._key(contract);
    const entry = this._cache.get(key);
    if (!entry) { this._misses++; return null; }
    const ttl = contract.outputFormat === "json" ? 300000 : 60000;
    if (performance.now() - entry.ts > ttl) {
      this._cache.delete(key);
      this._misses++;
      return null;
    }
    this._hits++;
    return entry.result;
  }

  set(contract: SubagentContract, result: SubagentResult): void {
    this._cache.set(this._key(contract), { result, ts: performance.now() });
  }

  stats(): Record<string, number> {
    const total = this._hits + this._misses;
    return { hits: this._hits, misses: this._misses, total, hitRate: total > 0 ? this._hits / total : 0, size: this._cache.size };
  }

  clear(): void {
    this._cache.clear();
    this._hits = 0;
    this._misses = 0;
  }
}

// ── Telemetry ─────────────────────────────────────────────────────

export class Telemetry {
  private _records = new Map<string, Record<string, unknown>[]>();

  record(model: string, result: SubagentResult): void {
    const list = this._records.get(model) ?? [];
    list.push({
      task_id: result.taskId,
      success: result.success,
      elapsed_seconds: result.elapsedSeconds,
      tokens_used: result.tokensUsed,
      timestamp: Date.now() / 1000,
    });
    this._records.set(model, list);
  }

  get(): Record<string, Record<string, unknown>[]> {
    return Object.fromEntries(this._records);
  }

  summary(): Record<string, Record<string, unknown>> {
    const summary: Record<string, Record<string, unknown>> = {};
    for (const [model, records] of this._records) {
      if (!records.length) continue;
      const latencies = records.map((r) => r.elapsed_seconds as number);
      const successes = records.map((r) => r.success as boolean);
      const tokens = records.map((r) => r.tokens_used as number);
      summary[model] = {
        count: records.length,
        success_rate: successes.filter(Boolean).length / successes.length,
        avg_latency: latencies.reduce((a, b) => a + b, 0) / latencies.length,
        max_latency: Math.max(...latencies),
        total_tokens: tokens.reduce((a, b) => a + b, 0),
      };
    }
    return summary;
  }

  clear(): void { this._records.clear(); }
}

// ── SubagentOrchestrator ──────────────────────────────────────────

const MAX_SUBAGENT_DEPTH_DEFAULT = 2;
const MAX_SUBAGENT_BRANCHING_DEFAULT = 3;

export class SubagentOrchestrator {
  config: WispConfig;
  workspace: string;
  private _budget = new BudgetTracker();
  private _cache = new ResultCache();
  private _telemetry = new Telemetry();
  private _maxDepth: number;
  private _maxBranching: number;
  private _poolSize: number;
  private _active = 0;
  private _semaphore: { acquire(): Promise<() => void> };

  constructor(config: WispConfig, workspace: string) {
    this.config = config;
    this.workspace = workspace;
    this._maxDepth = (config as unknown as Record<string, unknown>).max_subagent_depth as number ?? MAX_SUBAGENT_DEPTH_DEFAULT;
    this._maxBranching = (config as unknown as Record<string, unknown>).max_subagent_branching as number ?? MAX_SUBAGENT_BRANCHING_DEFAULT;
    this._poolSize = (config as unknown as Record<string, unknown>).subagent_pool_size as number ?? 4;
    this._semaphore = this._makeSemaphore(this._poolSize);
  }

  private _makeSemaphore(n: number): { acquire(): Promise<() => void> } {
    let count = n;
    const queue: Array<{ resolve: (release: () => void) => void }> = [];
    return {
      acquire(): Promise<() => void> {
        if (count > 0) {
          count--;
          return Promise.resolve(() => { count++; const next = queue.shift(); if (next) { count--; next.resolve(() => { count++; const n2 = queue.shift(); if (n2) { count--; n2.resolve(() => { count++; }); } }); } });
        }
        return new Promise((resolve) => queue.push({ resolve }));
      },
    };
  }

  setGlobalTokenBudget(budget: number | null): void { this._budget.setBudget(budget); }
  getTokensConsumed(): number { return this._budget.getConsumed(); }
  getTokenBudgetRemaining(): number | null { return this._budget.getRemaining(); }
  getCacheStats(): Record<string, number> { return this._cache.stats(); }
  clearCache(): void { this._cache.clear(); }
  getTelemetry(): Record<string, Record<string, unknown>[]> { return this._telemetry.get(); }
  getTelemetrySummary(): Record<string, Record<string, unknown>> { return this._telemetry.summary(); }
  setPoolSize(size: number): void {
    if (size < 1) throw new Error("Pool size must be >= 1");
    this._poolSize = size;
    this._semaphore = this._makeSemaphore(size);
  }
  getPoolStatus(): Record<string, number> {
    return { pool_size: this._poolSize, active_agents: this._active, available_slots: Math.max(0, this._poolSize - this._active) };
  }

  async run(contract: SubagentContract): Promise<SubagentResult> {
    if (contract.subagentDepth >= this._maxDepth) {
      return new SubagentResult({
        taskId: contract.name,
        success: false,
        output: `[DEPTH LIMIT EXCEEDED] Max subagent depth is ${this._maxDepth}`,
        error: `Subagent depth ${contract.subagentDepth} exceeds max ${this._maxDepth}`,
      });
    }

    const roleError = this._validateRole(contract);
    if (roleError) {
      return new SubagentResult({ taskId: contract.name, success: false, output: `[ROLE VALIDATION FAILED] ${roleError}`, error: roleError });
    }

    if (contract.timeoutSeconds <= 0) {
      return new SubagentResult({ taskId: contract.name, success: false, output: "[CONTRACT INVALID] timeout_seconds must be > 0", error: "timeout_seconds must be > 0" });
    }
    if (contract.maxIterations <= 0) {
      return new SubagentResult({ taskId: contract.name, success: false, output: "[CONTRACT INVALID] max_iterations must be > 0", error: "max_iterations must be > 0" });
    }

    const cached = this._cache.get(contract);
    if (cached) return cached;

    const budgetError = this._budget.check();
    if (budgetError) {
      return new SubagentResult({ taskId: contract.name, success: false, output: `[TOKEN BUDGET EXCEEDED] ${budgetError}`, error: budgetError });
    }

    const release = await this._semaphore.acquire();
    this._active++;
    try {
      const result = await this._execute(contract);
      this._cache.set(contract, result);
      this._budget.record(result.tokensUsed);
      this._telemetry.record(contract.model ?? this.config.model ?? "unknown", result);
      return result;
    } finally {
      this._active--;
      release();
    }
  }

  private async _execute(contract: SubagentContract): Promise<SubagentResult> {
    const start = performance.now();
    const system = contract.systemPrompt ?? this._defaultSystemPrompt(contract);
    const agentWorkspace = contract.workspace ?? this.workspace;

    let worktreePath: string | null = null;
    const worktreeMgr = new WorktreeManager(agentWorkspace);

    if (contract.worktreeIsolated) {
      try {
        worktreePath = await worktreeMgr.create(contract.name);
      } catch (exc) {
        // fallback to shared workspace
      }
    }

    const effectiveWorkspace = worktreePath ?? agentWorkspace;

    // Build child config
    const childConfig = new WispConfig({
      ...this.config,
      model: contract.model ?? this.config.model,
      workspace: effectiveWorkspace,
      auto_approve: contract.autoApprove,
      max_iterations: contract.maxIterations,
    });

    // Create core
    const factory = new ProviderFactory();
    const provider = factory.fromConfig({
      provider: childConfig.provider,
      ollama_url: childConfig.ollama_url,
      model: childConfig.model,
    });
    const security = new SecurityPolicy(
      (childConfig.permission_mode as PermissionMode) ?? PermissionMode.AUTO_EDIT
    );
    const registry = new ToolRegistry();
    const counter = new TokenCounter(childConfig.chars_per_token);
    const core = new WispAgentCore(childConfig, provider, security, registry, counter);

    // Create session
    const sessionId = `sub-${contract.name}-${Date.now().toString(36)}`;
    const session = {
      id: sessionId,
      model: childConfig.model,
      workspace: effectiveWorkspace,
      messages: [{ role: "system", content: system }, { role: "user", content: contract.task }],
    };

    const toolCallsLog: Array<{ name: string; args_preview: string }> = [];
    let outputText = "";
    let iterationsUsed = 0;
    let hasError = false;

    try {
      const deadline = start + contract.timeoutSeconds * 1000;
      for await (const event of core.turn(session, contract.task)) {
        if (performance.now() > deadline) {
          hasError = true;
          break;
        }
        const etype = String(event.type ?? "");
        if (etype === "content") {
          outputText = String(event.text ?? "");
        } else if (etype === "tool_call") {
          iterationsUsed++;
          toolCallsLog.push({
            name: String(event.name ?? ""),
            args_preview: JSON.stringify(event.arguments ?? {}).slice(0, 200),
          });
        } else if (etype === "error") {
          hasError = true;
          break;
        }
      }
    } catch (exc) {
      hasError = true;
    }

    const elapsed = (performance.now() - start) / 1000;

    // Capture worktree patch
    let patch: string | null = null;
    let patchApplied = false;
    if (worktreePath) {
      try {
        patch = await worktreeMgr.getPatch(worktreePath);
        const filesChanged = await worktreeMgr.detectFilesChanged(worktreePath);
        if (patch && !hasError) {
          patchApplied = await worktreeMgr.applyPatch(patch);
        }
        if (!process.env.WISP_KEEP_WORKTREES) {
          await worktreeMgr.cleanup(worktreePath);
        }
        return new SubagentResult({
          taskId: contract.name,
          success: !hasError,
          output: outputText,
          elapsedSeconds: elapsed,
          iterationsUsed,
          tokensUsed: Math.round(outputText.length / childConfig.chars_per_token),
          toolCalls: toolCallsLog,
          worktreePatch: patch,
          patchApplied,
          filesChanged: filesChanged ?? [],
          error: hasError ? "Subagent execution failed or timed out" : null,
        });
      } catch {
        // cleanup may fail — ignore
      }
    }

    return new SubagentResult({
      taskId: contract.name,
      success: !hasError,
      output: outputText,
      elapsedSeconds: elapsed,
      iterationsUsed,
      tokensUsed: Math.round(outputText.length / childConfig.chars_per_token),
      toolCalls: toolCallsLog,
      error: hasError ? "Subagent execution failed or timed out" : null,
    });
  }

  async runParallel(contracts: SubagentContract[], maxConcurrent = 4): Promise<SubagentResult[]> {
    const effectiveMax = Math.min(maxConcurrent, this._poolSize);
    const semaphore = this._makeSemaphore(effectiveMax);

    const tasks = contracts.map(async (c) => {
      const release = await semaphore.acquire();
      try {
        return await this.run(c);
      } finally {
        release();
      }
    });

    const results = await Promise.allSettled(tasks);
    return results.map((r, i) => {
      if (r.status === "fulfilled") return r.value;
      return new SubagentResult({
        taskId: contracts[i].name,
        success: false,
        error: String(r.reason),
      });
    });
  }

  async *runParallelStreaming(contracts: SubagentContract[], maxConcurrent = 4): AsyncGenerator<SubagentResult> {
    const semaphore = this._makeSemaphore(maxConcurrent);
    const tasks = contracts.map(async (c) => {
      const release = await semaphore.acquire();
      try {
        return await this.run(c);
      } finally {
        release();
      }
    });

    for (const promise of tasks) {
      try {
        yield await promise;
      } catch (exc) {
        yield new SubagentResult({ taskId: "unknown", success: false, error: String(exc) });
      }
    }
  }

  private _validateRole(contract: SubagentContract): string | null {
    if (!contract.role) return "Role is required";
    if (!ROLE_CONFIGS[contract.role]) {
      return `Unknown role '${contract.role}'. Valid roles: ${Object.keys(ROLE_CONFIGS).sort().join(", ")}`;
    }
    if (!ROLE_CONFIGS[contract.role].systemPrompt) return `Role '${contract.role}' has no system prompt configured`;
    return null;
  }

  private _defaultSystemPrompt(contract: SubagentContract): string {
    const roleCfg = ROLE_CONFIGS[contract.role];
    const base = roleCfg?.systemPrompt ?? `You are a specialist subagent: **${contract.name}**. Focus ONLY on your assigned task. Work efficiently. When done, provide a clear summary of what you did.`;
    const parts = [base];
    if (contract.tools[0] !== "all") {
      parts.push("", "## Allowed Tools", contract.tools.join(", "));
    }
    if (contract.contextFiles.length > 0) {
      parts.push("", "## Context Files");
      for (const f of contract.contextFiles) parts.push(`- ${f}`);
    }
    if (contract.systemPromptExtra) {
      parts.push("", "## Additional Instructions", contract.systemPromptExtra);
    }
    return parts.join("\n");
  }

  async spawnWithGuards(
    task: string,
    options?: {
      tools?: string[];
      maxIterations?: number;
      timeoutSeconds?: number;
      outputFormat?: string;
      worktreeIsolated?: boolean;
      maxTokens?: number;
      outputSchema?: Record<string, unknown>;
      autoRetry?: boolean;
      workspace?: string;
      autoApprove?: boolean;
      depth?: number;
      branchCount?: number;
    }
  ): Promise<string> {
    const depth = options?.depth ?? 0;
    const branchCount = options?.branchCount ?? 0;

    if (depth >= this._maxDepth) return `[Error: subagent depth ${depth} exceeds max ${this._maxDepth}]`;
    if (branchCount >= this._maxBranching) return `[Error: subagent branching ${branchCount} exceeds max ${this._maxBranching}]`;

    const contract = new SubagentContract({
      task,
      tools: options?.tools ?? ["all"],
      maxIterations: options?.maxIterations ?? 30,
      timeoutSeconds: options?.timeoutSeconds ?? 300,
      outputFormat: options?.outputFormat ?? "text",
      workspace: options?.workspace ?? this.workspace,
      autoApprove: options?.autoApprove ?? false,
      worktreeIsolated: options?.worktreeIsolated ?? false,
      maxTokens: options?.maxTokens ?? null,
      outputSchema: options?.outputSchema ?? null,
      subagentDepth: depth + 1,
      subagentBranchCount: branchCount + 1,
    });

    const result = await this.run(contract);
    let output = result.output;
    if (output.length > 12000) {
      output = output.slice(0, 12000) + `\n... [truncated: ${result.output.length} total chars]`;
    }
    return output;
  }

  async spawnParallelWithGuards(specs: (SubagentContract | Record<string, unknown>)[], depth = 0, branchCount = 0): Promise<SubagentResult[]> {
    if (depth >= this._maxDepth) {
      return specs.map((s) => new SubagentResult({
        taskId: (s as { name?: string }).name ?? "unknown",
        success: false,
        output: `[Error: subagent depth ${depth} exceeds max ${this._maxDepth}]`,
      }));
    }
    if (branchCount >= this._maxBranching) {
      return specs.map((s) => new SubagentResult({
        taskId: (s as { name?: string }).name ?? "unknown",
        success: false,
        output: `[Error: subagent branching ${branchCount} exceeds max ${this._maxBranching}]`,
      }));
    }

    const contracts = specs.map((spec) => {
      const contract = spec instanceof SubagentContract ? spec : new SubagentContract(spec as Partial<SubagentContract>);
      contract.subagentDepth = depth + 1;
      contract.subagentBranchCount = branchCount + 1;
      return contract;
    });

    return this.runParallel(contracts);
  }
}
