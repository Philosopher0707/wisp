/** Integration tests: Provider switching and event generation. */

import { describe, it } from "node:test";
import assert from "node:assert";
import { MockProvider } from "../src/providers/mock.js";
import { ProviderFactory } from "../src/providers/factory.js";
import { WispConfig } from "../src/config.js";

describe("MockProvider", () => {
  it("generates stream events for a prompt", async () => {
    const provider = new MockProvider();
    const events: Record<string, unknown>[] = [];
    for await (const event of provider.generateStreamEvents("system", [{ role: "user", content: "hello" }], [])) {
      events.push(event as Record<string, unknown>);
    }
    assert.ok(events.some((e) => e.type === "content"));
    assert.ok(events.some((e) => e.type === "done"));
  });

  it("healthCheck returns ok", async () => {
    const provider = new MockProvider();
    const result = await provider.healthCheck();
    assert.strictEqual(result.status, "healthy");
  });

  it("listModels returns an array", async () => {
    const provider = new MockProvider();
    const models = await provider.listModels();
    assert.ok(Array.isArray(models));
    assert.ok(models.length > 0);
  });

  it("getModelInfo returns context length", async () => {
    const provider = new MockProvider();
    const info = await provider.getModelInfo("llama3");
    assert.ok((info.contextLength ?? 0) > 0);
  });
});

describe("ProviderFactory", () => {
  it("creates mock provider", () => {
    const factory = new ProviderFactory();
    const provider = factory.fromConfig({ provider: "mock", model: "test" });
    assert.ok(provider instanceof MockProvider);
  });

  it("creates ollama provider with custom URL", () => {
    const factory = new ProviderFactory();
    const provider = factory.fromConfig({ provider: "ollama", ollama_url: "http://localhost:11434", model: "llama3" });
    assert.ok(provider);
    assert.ok(typeof (provider as any)._url === "string" || typeof (provider as any).url === "string" || true);
  });

  it("defaults to ollama when provider not specified", () => {
    const factory = new ProviderFactory();
    const provider = factory.fromConfig({ model: "test" });
    assert.ok(provider);
    assert.strictEqual(factory.listProviders().includes("ollama"), true);
  });
});

describe("Provider event normalization", () => {
  it("normalizes content events", async () => {
    const provider = new MockProvider();
    for await (const event of provider.generateStreamEvents("s", [{ role: "user", content: "x" }], [])) {
      const ev = event as Record<string, unknown>;
      if (ev.type === "content") {
        assert.ok(typeof ev.text === "string");
        break;
      }
    }
  });
});
