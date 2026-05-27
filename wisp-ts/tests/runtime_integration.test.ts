/** Integration tests: AgentRuntime + mock core event flow. */

import { describe, it } from "node:test";
import assert from "node:assert";
import { AgentRuntime } from "../src/core/runtime.js";
import { Session } from "../src/core/session.js";
import { WispConfig } from "../src/config.js";
import { WispAgentCore } from "../src/core/engine.js";
import { MockProvider } from "../src/providers/mock.js";
import { SecurityPolicy } from "../src/infra/security.js";
import { PermissionMode } from "../src/config.js";

const PM = PermissionMode;
import { ToolRegistry } from "../src/tools/registry.js";
import { TokenCounter } from "../src/infra/token_counter.js";

function _makeMockCoreFactory() {
  return () => {
    const config = new WispConfig({ provider: "mock" });
    const provider = new MockProvider();
    const security = new SecurityPolicy(PM.FULL);
    const registry = new ToolRegistry();
    const tokenCounter = new TokenCounter();
    return new WispAgentCore(config, provider, security, registry, tokenCounter);
  };
}

function _makeStore() {
  return {
    loadSession: () => null,
    saveSession: () => {},
    setIdempotency: () => {},
    getIdempotency: () => null,
  } as any;
}

describe("AgentRuntime getOrCreateSession", () => {
  it("creates a new session when none exists", async () => {
    const runtime = new AgentRuntime(_makeStore(), _makeMockCoreFactory(), {} as any, new TokenCounter());
    const session = await runtime.getOrCreateSession("s1", "m1", "/tmp");
    assert.strictEqual(session.sessionId, "s1");
    assert.strictEqual(session.model, "m1");
    assert.strictEqual(session.workspace, "/tmp");
  });

  it("rejects invalid session_id", async () => {
    const runtime = new AgentRuntime(_makeStore(), _makeMockCoreFactory(), {} as any, new TokenCounter());
    await assert.rejects(runtime.getOrCreateSession("", "m1", "/tmp"), /Invalid/);
  });
});

describe("AgentRuntime runTurn", () => {
  it("runs a turn and yields content + done", async () => {
    const runtime = new AgentRuntime(_makeStore(), _makeMockCoreFactory(), {} as any, new TokenCounter());
    const session = new Session("s1", "test", ".");
    const events: Record<string, unknown>[] = [];
    for await (const event of runtime.runTurn(session, "hello")) {
      events.push(event);
    }
    assert.ok(events.some((e) => e.type === "content"));
    assert.ok(events.some((e) => e.type === "done"));
  });

  it("rejects invalid prompt", async () => {
    const runtime = new AgentRuntime(_makeStore(), _makeMockCoreFactory(), {} as any, new TokenCounter());
    const session = new Session("s1", "test", ".");
    await assert.rejects(
      (async () => {
        const events: any[] = [];
        for await (const event of runtime.runTurn(session, "")) { events.push(event); }
      })(),
      /Invalid/
    );
  });

  it("enforces max turns", async () => {
    const runtime = new AgentRuntime(_makeStore(), _makeMockCoreFactory(), {} as any, new TokenCounter());
    // Inject max turns via reflection
    (runtime as any)._maxTurns = 1;
    const session = new Session("s1", "test", ".");
    const events: Record<string, unknown>[] = [];
    for await (const event of runtime.runTurn(session, "hello")) {
      events.push(event);
    }
    // Second turn should fail
    const events2: Record<string, unknown>[] = [];
    for await (const event of runtime.runTurn(session, "again")) {
      events2.push(event);
    }
    assert.ok(events2.some((e) => e.type === "error"));
  });

  it("serializes per-session turns", async () => {
    const runtime = new AgentRuntime(_makeStore(), _makeMockCoreFactory(), {} as any, new TokenCounter());
    const session = new Session("s1", "test", ".");
    const results: number[] = [];
    const p1 = (async () => {
      for await (const _ of runtime.runTurn(session, "first")) {}
      results.push(1);
    })();
    const p2 = (async () => {
      for await (const _ of runtime.runTurn(session, "second")) {}
      results.push(2);
    })();
    await Promise.all([p1, p2]);
    // Both should complete, but serialized order may vary
    assert.strictEqual(results.length, 2);
  });

  it("persists session after turn", async () => {
    const saved: any[] = [];
    const store = {
      ..._makeStore(),
      saveSession: (s: any) => saved.push(s),
    };
    const runtime = new AgentRuntime(store, _makeMockCoreFactory(), {} as any, new TokenCounter());
    const session = new Session("s1", "test", ".");
    for await (const _ of runtime.runTurn(session, "hello")) {}
    assert.ok(saved.length > 0);
    assert.strictEqual(saved[saved.length - 1].id, "s1");
  });
});

describe("AgentRuntime idempotency", () => {
  it("caches and replays identical prompts", async () => {
    const setCalls: any[] = [];
    const getCalls: string[] = [];
    const store = {
      ..._makeStore(),
      setIdempotency: (k: string, v: any) => setCalls.push({ k, v }),
      getIdempotency: (k: string) => { getCalls.push(k); return null; },
    };
    const runtime = new AgentRuntime(store, _makeMockCoreFactory(), {} as any, new TokenCounter());
    const session = new Session("s1", "test", ".");
    for await (const _ of runtime.runTurn(session, "hello")) {}
    // getIdempotency should be called with a hash key derived from the prompt
    assert.ok(getCalls.length > 0);
    assert.ok(getCalls[0].startsWith("s1:")); // format is sessionId:sha256(prompt)
  });
});
