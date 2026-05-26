/** Provider protocol — the interface all LLM providers must implement. */

export interface ProviderEvent {
  type: string;
  [key: string]: unknown;
}

export interface ModelInfo {
  id: string;
  name?: string;
  size?: number;
  contextLength?: number;
}

export interface HealthCheck {
  status: "healthy" | "unhealthy";
  error?: string;
}

export abstract class Provider {
  abstract generateStreamEvents(
    systemPrompt: string,
    messages: Record<string, unknown>[],
    tools?: Record<string, unknown>[] | null,
    checkpointEvery?: number
  ): AsyncGenerator<ProviderEvent>;

  abstract healthCheck(): Promise<HealthCheck>;

  abstract listModels(): Promise<ModelInfo[]>;

  abstract getModelInfo(model: string): Promise<ModelInfo>;
}
