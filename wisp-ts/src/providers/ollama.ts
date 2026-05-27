/** Ollama-backed provider implementation with retry, circuit breaker, and resilient streaming. */

import { Provider, ProviderEvent, ModelInfo, HealthCheck } from "./protocol.js";

export interface OllamaConfig {
  ollama_url: string;
  model: string;
}

interface CircuitBreakerState {
  failures: number;
  lastFailureTime: number;
  state: "closed" | "open" | "half-open";
}

const CIRCUIT_THRESHOLD = 5;
const CIRCUIT_RESET_MS = 30000;
const MAX_RETRIES = 3;
const BASE_DELAY_MS = 1000;
const REQUEST_TIMEOUT_MS = 120000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isRetryable(status: number): boolean {
  return status >= 500 || status === 429 || status === 408;
}

export class OllamaProvider extends Provider {
  baseUrl: string;
  model: string;
  private _circuit: CircuitBreakerState;

  constructor(config?: OllamaConfig) {
    super();
    this.baseUrl = config?.ollama_url ?? "http://localhost:11434";
    this.model = config?.model ?? "kimi-k2.6:cloud";
    this._circuit = { failures: 0, lastFailureTime: 0, state: "closed" };
  }

  async *generateStreamEvents(
    systemPrompt: string,
    messages: Record<string, unknown>[],
    tools?: Record<string, unknown>[] | null,
    _checkpointEvery = 50
  ): AsyncGenerator<ProviderEvent> {
    // Circuit breaker check
    if (this._circuit.state === "open") {
      if (Date.now() - this._circuit.lastFailureTime < CIRCUIT_RESET_MS) {
        yield { type: "error", message: "Circuit breaker OPEN — too many failures, retry later", recoverable: true };
        return;
      }
      this._circuit.state = "half-open";
    }

    const payload: Record<string, unknown> = {
      model: this.model,
      messages: [{ role: "system", content: systemPrompt }, ...messages],
      stream: true,
    };
    if (tools) payload.tools = tools;

    let lastError: Error | null = null;
    let resp: Response | null = null;

    // Retry loop with exponential backoff
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
        resp = await fetch(`${this.baseUrl}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        if (!resp.ok) {
          if (isRetryable(resp.status) && attempt < MAX_RETRIES) {
            const delay = BASE_DELAY_MS * 2 ** attempt + Math.random() * 500;
            yield { type: "system", message: `Retryable error HTTP ${resp.status}, retrying in ${Math.round(delay)}ms...`, level: "warn" };
            await sleep(delay);
            continue;
          }
          yield { type: "error", message: `HTTP ${resp.status}: ${resp.statusText}`, recoverable: isRetryable(resp.status) };
          this._recordFailure();
          return;
        }

        // Success — reset circuit breaker
        this._recordSuccess();
        break;
      } catch (exc) {
        lastError = exc instanceof Error ? exc : new Error(String(exc));
        const isAbort = lastError.name === "AbortError";
        const isNetwork = !isAbort;

        if ((isNetwork || isAbort) && attempt < MAX_RETRIES) {
          const delay = BASE_DELAY_MS * 2 ** attempt + Math.random() * 500;
          yield { type: "system", message: `Network error, retrying in ${Math.round(delay)}ms...`, level: "warn" };
          await sleep(delay);
          continue;
        }

        yield { type: "error", message: `Request failed: ${lastError.message}`, recoverable: false };
        this._recordFailure();
        return;
      }
    }

    if (!resp || !resp.ok) {
      yield { type: "error", message: `Request failed after ${MAX_RETRIES} retries`, recoverable: false };
      return;
    }

    const reader = resp.body?.getReader();
    if (!reader) {
      yield { type: "error", message: "No response body" };
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let streamError: Error | null = null;

    try {
      while (true) {
        let done = false;
        let value: Uint8Array | undefined;
        try {
          const readResult = await reader.read();
          done = readResult.done;
          value = readResult.value;
        } catch (readErr) {
          streamError = readErr instanceof Error ? readErr : new Error(String(readErr));
          yield { type: "error", message: `Stream interrupted: ${streamError.message}`, recoverable: true };
          break;
        }

        if (done) break;
        if (!value) continue;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const data = JSON.parse(line) as Record<string, unknown>;
            if (data.done) {
              yield { type: "done", done_reason: (data.done_reason as string) ?? "" };
              return;
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
      try {
        reader.releaseLock();
      } catch {
        // ignore release errors
      }
    }

    // If stream ended without explicit done event, yield one
    if (!streamError) {
      yield { type: "done", done_reason: "stream_end" };
    }
  }

  private _recordSuccess(): void {
    this._circuit.failures = 0;
    this._circuit.state = "closed";
  }

  private _recordFailure(): void {
    this._circuit.failures += 1;
    this._circuit.lastFailureTime = Date.now();
    if (this._circuit.failures >= CIRCUIT_THRESHOLD) {
      this._circuit.state = "open";
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
