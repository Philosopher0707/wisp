/** Auto-delegation triggers — detect when a task should be delegated to subagents. */

import { SubagentContract } from "./task.js";

const LLM_CLASSIFY_PROMPT = (
  "Analyze this task and determine if it should be delegated to specialized "
  + "subagents. A task benefits from delegation if it is multi-faceted, requires "
  + "parallel research, or spans many files.\n\n"
  + "Task: {task}\n\n"
  + 'Respond with JSON only: {"delegate": true/false, "confidence": 0.0-1.0, '
  + '"reason": "short reason", "subagents": ["role1", "role2"]}'
);

export class DelegationSignal {
  shouldDelegate = false;
  reason = "";
  suggestedContracts: Partial<SubagentContract>[] = [];
  confidence = 0;

  constructor(init?: Partial<DelegationSignal>) {
    if (init) Object.assign(this, init);
  }
}

export class DelegationAnalyzer {
  complexityIndicators = [
    "implement", "build", "create", "design", "refactor",
    "architecture", "system", "framework", "library",
    "multi-step", "complex", "complicated", "sophisticated",
    "end-to-end", "full-stack", "integration",
  ];

  researchIndicators = [
    "research", "investigate", "analyze", "compare", "survey",
    "evaluate", "benchmark", "study", "explore",
  ];

  multiFileIndicators = [
    "across", "throughout", "all files", "multiple", "every",
    "entire codebase", "whole project", "global",
  ];

  specializedIndicators = [
    "security", "performance", "optimization", "memory",
    "concurrency", "async", "threading", "crypto",
    "authentication", "authorization", "database",
    "frontend", "backend", "api", "microservice",
  ];

  maxPromptLength: number;

  constructor(maxPromptLength = 100) {
    this.maxPromptLength = maxPromptLength;
  }

  async analyzeWithLlm(
    prompt: string,
    llmCall: (prompt: string) => Promise<string>,
    _currentIteration = 0,
    _maxIterations = 10
  ): Promise<DelegationSignal> {
    try {
      const classifyPrompt = LLM_CLASSIFY_PROMPT.replace("{task}", prompt.slice(0, 800));
      const response = await this._withTimeout(llmCall(classifyPrompt), 5000);
      return this._parseLlmResponse(prompt, response);
    } catch {
      return this.analyze(prompt);
    }
  }

  analyze(prompt: string, currentIteration = 0, maxIterations = 10): DelegationSignal {
    const promptLower = prompt.toLowerCase();
    let score = 0;
    const reasons: string[] = [];

    const complexityScore = this._scoreComplexity(promptLower);
    if (complexityScore >= 0.05) {
      score += complexityScore * 0.4;
      reasons.push(`complexity(${complexityScore.toFixed(2)})`);
    }

    const researchScore = this._scoreResearch(promptLower);
    if (researchScore >= 0.05) {
      score += researchScore * 0.35;
      reasons.push(`research(${researchScore.toFixed(2)})`);
    }

    const multiFileScore = this._scoreMultiFile(promptLower);
    if (multiFileScore >= 0.05) {
      score += multiFileScore * 0.3;
      reasons.push(`multi-file(${multiFileScore.toFixed(2)})`);
    }

    const specializedScore = this._scoreSpecialized(promptLower);
    if (specializedScore >= 0.05) {
      score += specializedScore * 0.3;
      reasons.push(`specialized(${specializedScore.toFixed(2)})`);
    }

    if (currentIteration > maxIterations * 0.6) {
      score += 0.5;
      reasons.push(`iteration_pressure(${currentIteration}/${maxIterations})`);
    }

    if (["delegate", "spawn agent", "parallel"].some((kw) => promptLower.includes(kw))) {
      score += 0.5;
      reasons.push("explicit_request");
    }

    const shouldDelegate = score >= 0.18;
    if (shouldDelegate) {
      return new DelegationSignal({
        shouldDelegate: true,
        reason: reasons.join("; "),
        suggestedContracts: this._suggestContracts(prompt, reasons),
        confidence: Math.min(score, 1.0),
      });
    }

    return new DelegationSignal({ shouldDelegate: false, confidence: score });
  }

  private _scoreComplexity(prompt: string): number {
    let score = 0;
    const words = prompt.split(" ");
    if (prompt.length > 200) score += 0.3;
    else if (prompt.length > 80) score += 0.2;
    else if (prompt.length > 40) score += 0.1;
    const matches = this.complexityIndicators.filter((kw) => prompt.includes(kw)).length;
    score += Math.min(matches * 0.2, 0.6);
    if (words.length > 20) score += 0.15;
    return Math.min(score, 1.0);
  }

  private _scoreResearch(prompt: string): number {
    const matches = this.researchIndicators.filter((kw) => prompt.includes(kw)).length;
    return Math.min(matches * 0.4, 1.0);
  }

  private _scoreMultiFile(prompt: string): number {
    const matches = this.multiFileIndicators.filter((kw) => prompt.includes(kw)).length;
    return Math.min(matches * 0.5, 1.0);
  }

  private _scoreSpecialized(prompt: string): number {
    const matches = this.specializedIndicators.filter((kw) => prompt.includes(kw)).length;
    return Math.min(matches * 0.25, 1.0);
  }

  private _suggestContracts(prompt: string, _reasons: string[]): Partial<SubagentContract>[] {
    const contracts: Partial<SubagentContract>[] = [];
    const promptLower = prompt.toLowerCase();

    if (this.researchIndicators.some((kw) => promptLower.includes(kw))) {
      contracts.push({
        name: "researcher",
        role: "researcher",
        task: `Research and analyze: ${prompt.slice(0, 200)}`,
        timeoutSeconds: 180,
        maxIterations: 15,
      });
    }

    if (this.complexityIndicators.some((kw) => promptLower.includes(kw))) {
      contracts.push({
        name: "implementer",
        role: "coder",
        task: `Implement the solution for: ${prompt.slice(0, 200)}`,
        timeoutSeconds: 300,
        maxIterations: 20,
      });
    }

    if (contracts.length >= 2) {
      contracts.push({
        name: "reviewer",
        role: "reviewer",
        task: `Review the approach for: ${prompt.slice(0, 200)}`,
        timeoutSeconds: 180,
        maxIterations: 15,
      });
    }

    return contracts;
  }

  private _parseLlmResponse(prompt: string, response: string): DelegationSignal {
    try {
      let jsonStr = response.trim();
      if (jsonStr.startsWith("```")) {
        const lines = jsonStr.split("\n");
        jsonStr = lines.slice(1, lines.length - 1).join("\n");
      }
      const data = JSON.parse(jsonStr) as Record<string, unknown>;
      const shouldDelegate = !!data.delegate;
      const confidence = Number(data.confidence ?? 0);
      const reason = String(data.reason ?? "LLM classified");
      const roles = (data.subagents as string[]) ?? [];

      const roleToConfig: Record<string, Partial<SubagentContract>> = {
        researcher: { role: "researcher", timeoutSeconds: 180, maxIterations: 15 },
        coder: { role: "coder", timeoutSeconds: 300, maxIterations: 20 },
        reviewer: { role: "reviewer", timeoutSeconds: 180, maxIterations: 15 },
        generalist: { role: "generalist", timeoutSeconds: 180, maxIterations: 15 },
      };

      const contracts: Partial<SubagentContract>[] = [];
      for (const role of roles) {
        const cfg = roleToConfig[role] ?? { role: "generalist", timeoutSeconds: 180, maxIterations: 15 };
        contracts.push({ name: `${role}-${contracts.length}`, role: cfg.role as string, task: `[${role}] ${prompt.slice(0, 200)}`, timeoutSeconds: cfg.timeoutSeconds, maxIterations: cfg.maxIterations });
      }

      return new DelegationSignal({
        shouldDelegate,
        reason,
        suggestedContracts: contracts,
        confidence,
      });
    } catch {
      return this.analyze(prompt);
    }
  }

  private async _withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timeout")), ms);
      promise.then(
        (v) => { clearTimeout(timer); resolve(v); },
        (e) => { clearTimeout(timer); reject(e); }
      );
    });
  }
}

let _defaultAnalyzer: DelegationAnalyzer | null = null;

export function getDelegationAnalyzer(): DelegationAnalyzer {
  if (!_defaultAnalyzer) _defaultAnalyzer = new DelegationAnalyzer();
  return _defaultAnalyzer;
}
