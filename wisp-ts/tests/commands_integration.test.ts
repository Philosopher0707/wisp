/** Integration tests: slash commands through the REPL path. */

import { describe, it } from "node:test";
import assert from "node:assert";
import { dispatch, AgentAdapter, allCommands, lookup } from "../src/commands.js";
import { WispConfig } from "../src/config.js";
import { Session } from "../src/core/session.js";
import { AgentRuntime } from "../src/core/runtime.js";
import { TokenCounter } from "../src/infra/token_counter.js";

function _makeAdapter(): AgentAdapter {
  const config = new WispConfig();
  const store = { loadSession: () => null, saveSession: () => {}, setIdempotency: () => {}, getIdempotency: () => null } as any;
  const runtime = new AgentRuntime(store, () => ({} as any), {} as any, new TokenCounter());
  const session = new Session("test", "m1", ".");
  return new AgentAdapter(config, runtime, session);
}

describe("Slash command dispatch", () => {
  it("returns false for non-slash input", () => {
    const adapter = _makeAdapter();
    const result = dispatch("hello world", adapter);
    assert.strictEqual(result, false);
  });

  it("handles /help", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/help", adapter);
    assert.strictEqual(result, true);
  });

  it("handles bare / as help", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/", adapter);
    assert.strictEqual(result, true);
  });

  it("handles /clear", () => {
    const adapter = _makeAdapter();
    adapter.messages.push({ role: "user", content: "hi" });
    const result = dispatch("/clear", adapter);
    assert.strictEqual(result, true);
    assert.strictEqual(adapter.messages.length, 0);
  });

  it("handles /session", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/session", adapter);
    assert.strictEqual(result, true);
  });

  it("handles /tokens", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/tokens", adapter);
    assert.strictEqual(result, true);
  });

  it("handles /compact", () => {
    const adapter = _makeAdapter();
    adapter.messages.push({ role: "user", content: "a" });
    // Compact needs > 10 messages; this should still not crash
    for (let i = 0; i < 12; i++) adapter.messages.push({ role: "user", content: String(i) });
    const result = dispatch("/compact", adapter);
    assert.strictEqual(result, true);
  });

  it("handles /approve toggle", () => {
    const adapter = _makeAdapter();
    const before = adapter.config.auto_approve;
    const result = dispatch("/approve", adapter);
    assert.strictEqual(result, true);
    assert.strictEqual(adapter.config.auto_approve, !before);
  });

  it("handles /thinking toggle", () => {
    const adapter = _makeAdapter();
    const before = adapter.config.show_thinking;
    const result = dispatch("/thinking", adapter);
    assert.strictEqual(result, true);
    assert.strictEqual(adapter.config.show_thinking, !before);
  });

  it("handles /workspace with arg", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/workspace /tmp", adapter);
    assert.strictEqual(result, true);
    assert.strictEqual(adapter.config.workspace, "/tmp");
  });

  it("handles /drop", () => {
    const adapter = _makeAdapter();
    adapter.messages.push({ role: "user", content: "msg" });
    const result = dispatch("/drop", adapter);
    assert.strictEqual(result, true);
    assert.strictEqual(adapter.messages.length, 0);
  });

  it("handles /skill with no args", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/skill", adapter);
    assert.strictEqual(result, true);
  });

  it("handles /new", () => {
    const adapter = _makeAdapter();
    const oldId = adapter.session.sessionId;
    const result = dispatch("/new", adapter);
    assert.strictEqual(result, true);
    assert.notStrictEqual(adapter.session.sessionId, oldId);
  });

  it("handles /exit", () => {
    const adapter = _makeAdapter();
    assert.throws(() => dispatch("/exit", adapter), /exit|EXIT/);
  });

  it("handles unknown command", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/unknown_cmd_xyz", adapter);
    assert.strictEqual(result, true);
  });

  it("handles alias /h", () => {
    const adapter = _makeAdapter();
    const result = dispatch("/h", adapter);
    assert.strictEqual(result, true);
  });

  it("handles alias /cls", () => {
    const adapter = _makeAdapter();
    adapter.messages.push({ role: "user", content: "x" });
    const result = dispatch("/cls", adapter);
    assert.strictEqual(result, true);
    assert.strictEqual(adapter.messages.length, 0);
  });
});

describe("Command registry", () => {
  it("has 17+ commands", () => {
    const cmds = allCommands();
    assert.ok(cmds.length >= 17, `Expected >= 17 commands, got ${cmds.length}`);
  });

  it("lookup finds registered commands", () => {
    assert.ok(lookup("help"));
    assert.ok(lookup("clear"));
    assert.ok(lookup("exit"));
    assert.ok(!lookup("nonexistent"));
  });

  it("aliases resolve to same command", () => {
    const h = lookup("h");
    const help = lookup("help");
    assert.ok(h);
    assert.ok(help);
    assert.strictEqual(h?.name, "help");
  });
});
