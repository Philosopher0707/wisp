/** MockProvider — deterministic model provider for unit testing. */

import { Provider, ProviderEvent, ModelInfo, HealthCheck } from "./protocol.js";

export class MockProvider extends Provider {
  responses: string[];
  toolCalls: Record<string, unknown>[][];
  thinking: string[];
  model: string;
  contextLength: number;
  private _index = 0;

  constructor(options?: {
    responses?: string[];
    toolCalls?: Record<string, unknown>[][];
    thinking?: string[];
    model?: string;
    contextLength?: number;
  }) {
    super();
    this.responses = options?.responses ?? [];
    this.toolCalls = options?.toolCalls ?? [];
    this.thinking = options?.thinking ?? [];
    this.model = options?.model ?? "mock-model";
    this.contextLength = options?.contextLength ?? 128000;
  }

  async *generateStreamEvents(
    _systemPrompt: string,
    _messages: Record<string, unknown>[],
    _tools?: Record<string, unknown>[] | null
  ): AsyncGenerator<ProviderEvent> {
    const content = this._nextResponse();
    const tcs = this._nextToolCalls();
    const thinkingText = this._nextThinking();

    if (thinkingText) {
      for (const chunk of this._chunkText(thinkingText, 10)) {
        yield { type: "content", text: chunk };
      }
    }

    for (const chunk of this._chunkText(content, 10)) {
      yield { type: "content", text: chunk };
    }

    if (tcs.length > 0) {
      for (const tc of tcs) {
        const func = (tc.function as Record<string, unknown>) ?? {};
        yield {
          type: "tool_call",
          name: String(func.name ?? ""),
          arguments: func.arguments ?? {},
        };
      }
    }

    yield { type: "done" };
  }

  async healthCheck(): Promise<HealthCheck> {
    return { status: "healthy" };
  }

  async listModels(): Promise<ModelInfo[]> {
    return [{ id: this.model, name: this.model, size: 0 }];
  }

  async getModelInfo(model: string): Promise<ModelInfo> {
    return { id: model, contextLength: this.contextLength };
  }

  private _nextResponse(): string {
    const idx = this._index;
    this._index += 1;
    if (idx < this.responses.length) return this.responses[idx];
    return "[mock: no more responses]";
  }

  private _nextToolCalls(): Record<string, unknown>[] {
    const idx = this._index - 1;
    if (idx >= 0 && idx < this.toolCalls.length) return this.toolCalls[idx];
    return [];
  }

  private _nextThinking(): string {
    const idx = this._index - 1;
    if (idx >= 0 && idx < this.thinking.length) return this.thinking[idx];
    return "";
  }

  private _chunkText(text: string, size: number): string[] {
    if (!text) return [];
    const chunks: string[] = [];
    for (let i = 0; i < text.length; i += size) {
      chunks.push(text.slice(i, i + size));
    }
    return chunks;
  }
}
