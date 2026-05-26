/** Production-grade WispAgentCore — stateless turn engine. */

import { AgentEvent, EventType, toolResult, error as makeError, done as makeDone } from "./events.js";
import { Provider, ProviderEvent } from "../providers/protocol.js";
import { WispConfig } from "../config.js";
import { SecurityPolicy, SecurityDecision } from "../infra/security.js";
import { ToolRegistry } from "../tools/registry.js";
import { TokenCounter } from "../infra/token_counter.js";
import { TOOL_SCHEMAS } from "../tools/schemas.js";

export interface ToolCallEvent {
  type: "tool_call";
  name: string;
  arguments: Record<string, unknown>;
  id?: string;
}

export class WispAgentCore {
  config: WispConfig;
  provider: Provider;
  security: SecurityPolicy;
  toolRegistry: ToolRegistry;
  tokenCounter: TokenCounter;

  constructor(
    config: WispConfig,
    provider: Provider,
    security: SecurityPolicy,
    toolRegistry: ToolRegistry,
    tokenCounter: TokenCounter
  ) {
    this.config = config;
    this.provider = provider;
    this.security = security;
    this.toolRegistry = toolRegistry;
    this.tokenCounter = tokenCounter;
  }

  async *turn(
    session: Record<string, unknown>,
    prompt: string,
    approvalHandler?: (toolCall: Record<string, unknown>) => Promise<boolean>
  ): AsyncGenerator<Record<string, unknown>> {
    const messages = [...((session.messages as Array<{ role: string; content: string }>) ?? [])];
    const last = messages[messages.length - 1];
    if (!last || last.role !== "user" || last.content !== prompt) {
      messages.push({ role: "user", content: prompt });
    }

    const systemPrompt = this._buildSystemPrompt(session);
    const tools = this._getToolSchemas();
    const maxIterations = this.config.max_iterations;
    const workspace = String(session.workspace ?? ".");

    for (let iteration = 0; iteration < maxIterations; iteration++) {
      const pendingToolCalls: ToolCallEvent[] = [];
      const partialContent: string[] = [];
      let hasToolCalls = false;

      try {
        for await (const event of this.provider.generateStreamEvents(systemPrompt, messages, tools)) {
          const normalized = this._normalizeProviderEvent(event);
          if (normalized.type === "content") {
            const text = String(normalized.text ?? "");
            partialContent.push(text);
            yield { type: "content", text };
          } else if (normalized.type === "tool_call") {
            hasToolCalls = true;
            const tc: ToolCallEvent = {
              type: "tool_call",
              name: String(normalized.name ?? ""),
              arguments: (normalized.arguments as Record<string, unknown>) ?? {},
              id: String(normalized.id ?? `call_${Math.random().toString(36).slice(2, 10)}`),
            };

            // Security check BEFORE yielding
            const decision = this.security.check({ name: tc.name, args: tc.arguments }, { workspace });
            if (!decision.allowed) {
              yield { type: "error", message: `Blocked: ${decision.reason}`, recoverable: true };
              continue;
            }

            // Approval handler check
            if (approvalHandler) {
              const approved = await approvalHandler({ name: tc.name, arguments: tc.arguments });
              if (!approved) {
                yield { type: "error", message: `Denied: ${tc.name}`, recoverable: true };
                continue;
              }
            }

            pendingToolCalls.push(tc);
            yield { type: "tool_call", name: tc.name, arguments: tc.arguments, id: tc.id };
          } else if (normalized.type === "error") {
            yield { type: "error", message: String(normalized.message ?? ""), recoverable: true };
            return;
          }
        }
      } catch (exc) {
        if (partialContent.length > 0) yield { type: "content", text: partialContent.join("") };
        yield { type: "error", message: String(exc), recoverable: true };
        return;
      }

      if (!hasToolCalls) {
        yield { type: "done", session_id: String(session.id ?? ""), reason: "natural" };
        return;
      }

      // Execute tools
      const toolResults: Record<string, unknown>[] = [];
      for (const tc of pendingToolCalls) {
        const start = performance.now();
        try {
          // Schema validation
          const schemaError = this._validateToolArgs(tc.name, tc.arguments);
          if (schemaError) {
            const errResult = { status: "error", data: schemaError };
            yield { type: "tool_result", name: tc.name, result: errResult, duration_ms: 0, tool_call_id: tc.id };
            toolResults.push({ ...errResult, tool_call_id: tc.id });
            continue;
          }

          const rawResult = await this.toolRegistry.execute(tc.name, tc.arguments, workspace);
          const durationMs = performance.now() - start;
          const normalized = this._normalizeToolResult(rawResult);
          yield { type: "tool_result", name: tc.name, result: normalized, duration_ms: durationMs, tool_call_id: tc.id };
          toolResults.push({ ...normalized, tool_call_id: tc.id });
        } catch (e) {
          const durationMs = performance.now() - start;
          const err = { status: "error", data: String(e) };
          yield { type: "tool_result", name: tc.name, result: err, duration_ms: durationMs, tool_call_id: tc.id };
          toolResults.push({ ...err, tool_call_id: tc.id });
        }
      }

      // Build assistant + tool messages for next iteration
      const assistantMsg: Record<string, unknown> = {
        role: "assistant",
        content: partialContent.join("") || "",
      };
      if (pendingToolCalls.length > 0) {
        assistantMsg.tool_calls = pendingToolCalls.map((tc) => ({
          id: tc.id,
          type: "function",
          function: { name: tc.name, arguments: JSON.stringify(tc.arguments) },
        }));
      }
      messages.push(assistantMsg as { role: string; content: string });

      for (const tr of toolResults) {
        const content = String(tr.data ?? "");
        const tcId = String(tr.tool_call_id ?? "");
        messages.push({ role: "tool", content, tool_call_id: tcId } as { role: string; content: string });
      }
    }

    yield { type: "error", message: "Max iterations reached", recoverable: false };
    yield { type: "done", session_id: String(session.id ?? ""), reason: "max_iterations" };
  }

  private _buildSystemPrompt(session: Record<string, unknown>): string {
    const ws = String(session.workspace ?? ".");
    const parts: string[] = [];

    parts.push(`You are Wisp, a helpful coding assistant. Workspace: ${ws}`);

    // Tool descriptions
    parts.push("\n## Tools available");
    const descriptions: Record<string, string> = {
      read_file: "Read file contents",
      write_file: "Create or overwrite a file",
      edit_file: "Targeted text replacement",
      run_bash: "Execute shell commands",
      list_files: "Explore directory structure",
      git_status: "Show git status",
      git_diff: "Show git diff",
      git_commit: "Stage and commit",
      git_push: "Push to remote",
    };
    for (const [name, desc] of Object.entries(descriptions)) {
      parts.push(`- ${name}: ${desc}`);
    }

    // Compaction notice
    const compactionHistory = session.compaction_history as Array<unknown> | undefined;
    if (compactionHistory && compactionHistory.length > 0) {
      parts.push(`\n[Session compacted ${compactionHistory.length} times.]`);
    }

    return parts.join("\n");
  }

  private _getToolSchemas(): Record<string, unknown>[] {
    return TOOL_SCHEMAS as unknown as Record<string, unknown>[];
  }

  private _normalizeProviderEvent(event: ProviderEvent): Record<string, unknown> {
    const ev = event as Record<string, unknown>;
    if (typeof ev.type === "string") return ev;
    return { type: "unknown", ...ev };
  }

  private _validateToolArgs(name: string, args: Record<string, unknown>): string | null {
    const schema = TOOL_SCHEMAS.find((s) => s.function.name === name);
    if (!schema) return null; // unknown tool
    const required = schema.function.parameters.required ?? [];
    for (const key of required) {
      if (!(key in args)) return `Missing required arg '${key}' for tool '${name}'`;
    }
    return null;
  }

  private _normalizeToolResult(result: unknown): Record<string, unknown> {
    if (result && typeof result === "object" && "status" in (result as Record<string, unknown>)) {
      return result as Record<string, unknown>;
    }
    return { status: "ok", data: String(result ?? "") };
  }
}
