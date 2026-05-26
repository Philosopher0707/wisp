/** Ollama-backed provider implementation. */

import { Provider, ProviderEvent, ModelInfo, HealthCheck } from "./protocol.js";

export interface OllamaConfig {
  ollama_url: string;
  model: string;
}

export class OllamaProvider extends Provider {
  baseUrl: string;
  model: string;

  constructor(config?: OllamaConfig) {
    super();
    this.baseUrl = config?.ollama_url ?? "http://localhost:11434";
    this.model = config?.model ?? "kimi-k2.6:cloud";
  }

  async *generateStreamEvents(
    systemPrompt: string,
    messages: Record<string, unknown>[],
    tools?: Record<string, unknown>[] | null,
    _checkpointEvery = 50
  ): AsyncGenerator<ProviderEvent> {
    const payload: Record<string, unknown> = {
      model: this.model,
      messages: [{ role: "system", content: systemPrompt }, ...messages],
      stream: true,
    };
    if (tools) payload.tools = tools;

    const resp = await fetch(`${this.baseUrl}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      yield { type: "error", message: `HTTP ${resp.status}: ${resp.statusText}` };
      return;
    }

    const reader = resp.body?.getReader();
    if (!reader) {
      yield { type: "error", message: "No response body" };
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const data = JSON.parse(line) as Record<string, unknown>;
            if (data.done) {
              yield { type: "done", done_reason: (data.done_reason as string) ?? "" };
              break;
            }
            const msg = (data.message as Record<string, unknown>) ?? {};
            const tcs = (msg.tool_calls as Record<string, unknown>[]) ?? [];
            if (tcs.length > 0) {
              for (const tc of tcs) {
                const func = (tc.function as Record<string, unknown>) ?? {};
                yield {
                  type: "tool_call",
                  name: String(func.name ?? ""),
                  arguments: func.arguments ?? {},
                };
              }
            } else if (msg.content) {
              yield { type: "content", text: String(msg.content) };
            }
          } catch {
            // ignore malformed lines
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  async healthCheck(): Promise<HealthCheck> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/tags`, { method: "GET", signal: AbortSignal.timeout(5000) });
      return { status: resp.ok ? "healthy" : "unhealthy" };
    } catch (exc) {
      return { status: "unhealthy", error: String(exc) };
    }
  }

  async listModels(): Promise<ModelInfo[]> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/tags`, { signal: AbortSignal.timeout(5000) });
      if (!resp.ok) return [];
      const data = (await resp.json()) as { models?: { name: string; size?: number }[] };
      return (data.models ?? []).map((m) => ({ id: m.name, name: m.name, size: m.size }));
    } catch {
      return [];
    }
  }

  async getModelInfo(model: string): Promise<ModelInfo> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/show`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: model }),
        signal: AbortSignal.timeout(5000),
      });
      if (!resp.ok) return { id: model, contextLength: 128000 };
      const data = (await resp.json()) as { context_length?: number };
      return { id: model, contextLength: data.context_length ?? 128000 };
    } catch {
      return { id: model, contextLength: 128000 };
    }
  }
}
