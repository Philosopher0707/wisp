/** Provider factory — manages provider registration, discovery, and instantiation. */

import { Provider } from "./protocol.js";
import { OllamaProvider, OllamaConfig } from "./ollama.js";
import { MockProvider } from "./mock.js";

export class ProviderFactory {
  private _providers = new Map<
    string,
    new (config?: unknown) => Provider
  >();
  private _default: string | null = null;

  constructor() {
    this._registerBuiltins();
  }

  private _registerBuiltins(): void {
    this.register("ollama", OllamaProvider as unknown as new (config?: unknown) => Provider);
    this.register("mock", MockProvider as unknown as new (config?: unknown) => Provider);
  }

  register(name: string, cls: new (config?: unknown) => Provider): void {
    this._providers.set(name, cls);
  }

  create(name: string, config?: unknown): Provider {
    const cls = this._providers.get(name);
    if (!cls) {
      throw new Error(
        `Unknown provider: ${name}. Available: ${Array.from(this._providers.keys()).join(", ")}`
      );
    }
    return new cls(config);
  }

  listProviders(): string[] {
    return Array.from(this._providers.keys());
  }

  setDefault(name: string): void {
    if (!this._providers.has(name)) {
      throw new Error(`Unknown provider: ${name}`);
    }
    this._default = name;
  }

  getDefault(): string | null {
    return this._default;
  }

  createDefault(config?: unknown): Provider {
    if (!this._default) throw new Error("No default provider set");
    return this.create(this._default, config);
  }

  fromConfig(config: { provider?: string; ollama_url?: string; model?: string }): Provider {
    const name = config.provider ?? "ollama";
    if (name === "ollama") {
      const ollamaCfg: OllamaConfig = {
        ollama_url: config.ollama_url ?? "http://localhost:11434",
        model: config.model ?? "kimi-k2.6:cloud",
      };
      return this.create("ollama", ollamaCfg);
    }
    return this.create(name, config);
  }
}
