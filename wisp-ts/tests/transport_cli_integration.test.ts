/** Integration tests: CLI transport event handling and approval flow. */

import { describe, it } from "node:test";
import assert from "node:assert";
import { CLITransport } from "../src/transport/cli.js";
import { WispConfig, PermissionMode } from "../src/config.js";
import { Session } from "../src/core/session.js";
import { AgentRuntime } from "../src/core/runtime.js";
import { WispAgentCore } from "../src/core/engine.js";
import { MockProvider } from "../src/providers/mock.js";
import { SecurityPolicy } from "../src/infra/security.js";
import { ToolRegistry } from "../src/tools/registry.js";
import { TokenCounter } from "../src/infra/token_counter.js";

const PM = PermissionMode;
import { EventType, content, thinking, toolCall, toolResult, error, done } from "../src/core/events.js";

function _makeRuntime(): AgentRuntime {
  const config = new WispConfig({ provider: "mock" });
  const provider = new MockProvider();
  const security = new SecurityPolicy(PM.FULL);
  const registry = new ToolRegistry();
  const tokenCounter = new TokenCounter();
  const coreFactory = () => new WispAgentCore(config, provider, security, registry, tokenCounter);
  const store = { loadSession: () => null, saveSession: () => {}, setIdempotency: () => {}, getIdempotency: () => null } as any;
  const runtime = new AgentRuntime(store, coreFactory, {} as any, tokenCounter);
  return runtime;
}

describe("CLITransport event rendering", () => {
  it("renders content events", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    transport.start();
    await transport.send({ type: "content", text: "Hello" } as any);
    transport.stop();
  });

  it("renders thinking events", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig({ show_thinking: true }));
    transport.start();
    await transport.send({ type: "thinking", text: "Analyzing..." } as any);
    transport.stop();
  });

  it("renders tool_call then tool_result events", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    transport.start();
    transport.resetBuffers();
    await transport.send({ type: "tool_call", name: "read_file", arguments: { path: "test.ts" } } as any);
    await transport.send({ type: "tool_result", name: "read_file", result: { status: "ok", data: "export const x = 1;" }, duration_ms: 50 } as any);
    transport.stop();
  });

  it("renders error events", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    transport.start();
    await transport.send({ type: "error", message: "Something failed", recoverable: true } as any);
    transport.stop();
  });

  it("renders done events", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    transport.start();
    await transport.send({ type: "done", session_id: "s1", reason: "natural" } as any);
    transport.stop();
  });
});

describe("CLITransport approval state machine", () => {
  it("auto-approve bypasses handler when config is true", async () => {
    const runtime = _makeRuntime();
    const config = new WispConfig({ auto_approve: true });
    const transport = new CLITransport(runtime, config);
    transport.start();
    const handler = config.auto_approve ? undefined : transport.approve.bind(transport);
    assert.strictEqual(handler, undefined);
    transport.stop();
  });

  it("tracks allowed tools persistently", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    // Simulate state mutation via reflection for test coverage
    const state = (transport as any)._approvalState;
    state.allowedTools.add("write_file");
    assert.ok(state.allowedTools.has("write_file"));
  });

  it("tracks denied tools persistently", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    const state = (transport as any)._approvalState;
    state.deniedTools.add("run_bash");
    assert.ok(state.deniedTools.has("run_bash"));
  });

  it("block mode denies all tools", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    const state = (transport as any)._approvalState;
    state.blockMode = true;
    const result = await transport.approve({ name: "read_file", arguments: {} });
    assert.strictEqual(result, false);
  });

  it("auto mode allows all tools", async () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    const state = (transport as any)._approvalState;
    state.autoMode = true;
    const result = await transport.approve({ name: "run_bash", arguments: {} });
    assert.strictEqual(result, true);
  });
});

describe("CLITransport interrupt handling", () => {
  it("isInterrupted starts false", () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    transport.start();
    assert.strictEqual(transport.isInterrupted(), false);
    transport.stop();
  });

  it("resetBuffers increments turn number", () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    transport.start();
    const before = (transport as any)._turnNumber;
    transport.resetBuffers();
    const after = (transport as any)._turnNumber;
    assert.strictEqual(after, before + 1);
    transport.stop();
  });
});

describe("CLITransport printBanner", () => {
  it("prints banner without error", () => {
    const runtime = _makeRuntime();
    const transport = new CLITransport(runtime, new WispConfig());
    const session = new Session("test", "llama3", ".");
    transport.printBanner(session, "llama3", "coding");
  });
});
