/** Production-grade AgentRuntime — session lifecycle manager. */

import { WispAgentCore } from "./engine.js";
import { Session } from "./session.js";
import { UnifiedStore } from "../infra/store.js";
import { TokenCounter } from "../infra/token_counter.js";
import { SubagentOrchestrator } from "../multi_agent/orchestrator.js";
import { DelegationAnalyzer, getDelegationAnalyzer } from "../multi_agent/delegation.js";

export class AgentRuntime {
  store: UnifiedStore;
  coreFactory: () => WispAgentCore;
  orchestrator: SubagentOrchestrator;
  tokenCounter: TokenCounter;
  private _sessionLocks = new Map<string, Promise<void>>();
  private _maxMessages: number;
  private _maxContextTokens: number;
  private _autoCompact: boolean;
  private _compactThreshold: number;
  private _compactKeepRecent: number;

  constructor(
    store: UnifiedStore,
    coreFactory: () => WispAgentCore,
    orchestrator: SubagentOrchestrator,
    tokenCounter: TokenCounter
  ) {
    this.store = store;
    this.coreFactory = coreFactory;
    this.orchestrator = orchestrator;
    this.tokenCounter = tokenCounter;
    this._maxMessages = 50;
    this._maxContextTokens = 128000;
    this._autoCompact = true;
    this._compactThreshold = 75;
    this._compactKeepRecent = 10;
  }

  async getOrCreateSession(sessionId: string, model: string, workspace: string): Promise<Session> {
    if (!sessionId || !model || !workspace) {
      throw new Error(`Invalid session_id=${sessionId} model=${model} workspace=${workspace}`);
    }
    // Try load from SQLite store
    const stored = this.store.loadSession(sessionId);
    if (stored) {
      const session = new Session(sessionId, model, workspace);
      session.messages = (stored.messages as Array<{ role: string; content: string }>) ?? [];
      session.compactionHistory = (stored.compaction_history as Array<{ before_count: number; after_count: number; summary: string; timestamp: number }>) ?? [];
      session.createdAt = new Date(stored.created_at as string).getTime() / 1000;
      session.updatedAt = new Date(stored.updated_at as string).getTime() / 1000;
      return session;
    }
    const session = new Session(sessionId, model, workspace);
    this._saveToStore(session);
    return session;
  }

  async *runTurn(
    session: Session,
    prompt: string,
    approvalHandler?: (toolCall: Record<string, unknown>) => Promise<boolean>
  ): AsyncGenerator<Record<string, unknown>> {
    if (!prompt || typeof prompt !== "string") {
      throw new Error(`Invalid prompt: ${prompt}`);
    }

    // Per-session lock serialization
    const sid = session.sessionId;
    while (this._sessionLocks.has(sid)) {
      await this._sessionLocks.get(sid);
    }
    const lockPromise = this._consumeGenerator(this._runTurnLocked(session, prompt, approvalHandler));
    this._sessionLocks.set(sid, lockPromise);
    try {
      yield* this._runTurnLocked(session, prompt, approvalHandler);
    } finally {
      this._sessionLocks.delete(sid);
    }
  }

  private async *_runTurnLocked(
    session: Session,
    prompt: string,
    approvalHandler?: (toolCall: Record<string, unknown>) => Promise<boolean>
  ): AsyncGenerator<Record<string, unknown>> {
    const start = performance.now();

    // Auto-compact before turn
    if (this._autoCompact) {
      const shouldCompact = this._shouldCompact(session);
      if (shouldCompact) {
        this._compactSession(session, this._compactKeepRecent);
        yield { type: "system", message: "Session auto-compacted", level: "info" };
      }
    }

    // Add user message
    session.messages.push({ role: "user", content: prompt });

    // Auto-delegation check
    const analyzer = getDelegationAnalyzer();
    const delegation = analyzer.analyze(prompt, 0, this._maxMessages);
    if (delegation.shouldDelegate && delegation.suggestedContracts.length > 0) {
      yield { type: "system", message: "Auto-delegating to subagents...", level: "info" };
      const contracts = delegation.suggestedContracts.map((c) => ({
        ...c,
        workspace: session.workspace,
        model: session.model,
      }));
      const results = await this.orchestrator.runParallel(contracts as import("../multi_agent/task.js").SubagentContract[]);
      const succeeded = results.filter((r) => r.success);
      const contextParts = succeeded.map((r) => `[${r.taskId}]\n${r.output.slice(0, 2000)}`);
      if (contextParts.length > 0) {
        session.messages.push({ role: "system", content: `Subagent results:\n${contextParts.join("\n\n")}` });
      }
    }

    const core = this.coreFactory();
    const assistantContent: string[] = [];
    const toolCalls: Record<string, unknown>[] = [];
    const toolResults: Record<string, unknown>[] = [];
    let turnSucceeded = false;

    try {
      for await (const event of core.turn(session.toDict(), prompt, approvalHandler)) {
        yield event;
        const etype = event.type;
        if (etype === "content") assistantContent.push(String(event.text ?? ""));
        else if (etype === "tool_call") toolCalls.push(event);
        else if (etype === "tool_result") toolResults.push(event);
      }
      turnSucceeded = true;
    } catch (exc) {
      yield { type: "error", message: `Turn aborted: ${exc}`, recoverable: true };
    } finally {
      // Reconstruct session messages
      if (toolCalls.length > 0 || toolResults.length > 0) {
        for (const tc of toolCalls) {
          session.messages.push({
            role: "assistant",
            content: "",
            tool_calls: [{
              id: String(tc.id ?? `call_${Math.random().toString(36).slice(2, 10)}`),
              type: "function",
              function: {
                name: String(tc.name ?? ""),
                arguments: JSON.stringify(tc.arguments ?? {}),
              },
            }],
          });
        }
        for (const tr of toolResults) {
          session.messages.push({
            role: "tool",
            content: String((tr.result as Record<string, unknown>)?.data ?? tr.data ?? ""),
            tool_call_id: String(tr.tool_call_id ?? ""),
          });
        }
      }
      if (assistantContent.length > 0) {
        session.messages.push({ role: "assistant", content: assistantContent.join("") });
      }
      session.updatedAt = Date.now() / 1000;
      this._saveToStore(session);

      // Telemetry
      const latencyMs = performance.now() - start;
      const tokenCounts = this.tokenCounter.countMessages(session.messages);
      // (In production, log to telemetry service)
    }
  }

  private _shouldCompact(session: Session): boolean {
    if (session.messages.length <= this._compactKeepRecent) return false;
    const totalChars = session.messages.reduce((sum, m) => sum + (m.content?.length ?? 0), 0);
    const estimatedTokens = this.tokenCounter.estimateChars(totalChars);
    const threshold = this._maxContextTokens * (this._compactThreshold / 100);
    return estimatedTokens > threshold;
  }

  private _compactSession(session: Session, keepRecent: number): void {
    const msgs = session.messages;
    if (msgs.length <= keepRecent) return;
    const toSummarize = msgs.slice(0, -keepRecent);
    const keep = msgs.slice(-keepRecent);
    const summary = `[Previous conversation: ${toSummarize.length} messages summarized]`;
    session.messages = [{ role: "system", content: summary }, ...keep];
    session.compactionHistory.push({
      before_count: msgs.length,
      after_count: session.messages.length,
      summary,
      timestamp: Date.now() / 1000,
    });
  }

  private async _consumeGenerator(gen: AsyncGenerator<Record<string, unknown>>): Promise<void> {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    for await (const _ of gen) { /* drain */ }
  }

  private _saveToStore(session: Session): void {
    try {
      this.store.saveSession({
        id: session.sessionId,
        model: session.model,
        workspace: session.workspace,
        title: "",
        messages: session.messages,
        compaction_history: session.compactionHistory,
        created_at: new Date(session.createdAt * 1000).toISOString(),
        updated_at: new Date(session.updatedAt * 1000).toISOString(),
      });
    } catch {
      // graceful degradation if SQLite unavailable
    }
  }
}
