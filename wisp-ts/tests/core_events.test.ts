/** Tests for core/events.ts */

import { describe, it } from "node:test";
import assert from "node:assert";
import {
  AgentEvent,
  EventType,
  thinking,
  toolCall,
  toolResult,
  content,
  error,
  done,
  system,
  normalizeEvent,
  describeEventType,
  EVENT_SCHEMA_VERSION,
} from "../src/core/events.js";

describe("AgentEvent", () => {
  it("should create with defaults", () => {
    const ev = new AgentEvent(EventType.CONTENT);
    assert.strictEqual(ev.type, EventType.CONTENT);
    assert.deepStrictEqual(ev.data, {});
    assert.strictEqual(ev.schemaVersion, EVENT_SCHEMA_VERSION);
  });

  it("should round-trip via toDict/fromDict", () => {
    const original = thinking("hello");
    const dict = original.toDict();
    const restored = AgentEvent.fromDict(dict);
    assert.strictEqual(restored.type, original.type);
    assert.strictEqual(restored.data.text, "hello");
  });

  it("should handle flat dicts", () => {
    const ev = AgentEvent.fromDict({ type: "content", text: "flat", extra: 1 });
    assert.strictEqual(ev.type, "content");
    assert.strictEqual(ev.data.text, "flat");
    assert.strictEqual(ev.data.extra, 1);
  });

  it("should identify final events", () => {
    assert.ok(done("s1").isFinal);
    assert.ok(error("boom").isFinal);
    assert.ok(!thinking("x").isFinal);
    assert.ok(!toolCall("t", {}).isFinal);
  });

  it("should normalize existing AgentEvent", () => {
    const ev = content("hi");
    const normalized = normalizeEvent(ev);
    assert.strictEqual(normalized.type, EventType.CONTENT);
  });

  it("should normalize plain objects", () => {
    const normalized = normalizeEvent({ type: "error", message: "oops" });
    assert.strictEqual(normalized.type, "error");
    assert.strictEqual(normalized.data.message, "oops");
  });
});

describe("describeEventType", () => {
  it("should return descriptions", () => {
    assert.ok(describeEventType(EventType.TOOL_CALL).includes("Tool"));
    assert.strictEqual(describeEventType("unknown"), "Unknown event");
  });
});

describe("Event factories", () => {
  it("thinking", () => {
    const ev = thinking("reasoning...");
    assert.strictEqual(ev.type, EventType.THINKING);
    assert.strictEqual(ev.text, "reasoning...");
  });

  it("toolCall", () => {
    const ev = toolCall("read_file", { path: "x" });
    assert.strictEqual(ev.toolName, "read_file");
  });

  it("toolResult", () => {
    const ev = toolResult("read_file", "content", 12, true, "tc1");
    assert.strictEqual(ev.data.duration_ms, 12);
    assert.strictEqual(ev.data.auto_approved, true);
    assert.strictEqual(ev.data.tool_call_id, "tc1");
  });

  it("content", () => {
    const ev = content("hello");
    assert.strictEqual(ev.text, "hello");
  });

  it("error", () => {
    const ev = error("fail", false);
    assert.strictEqual(ev.data.recoverable, false);
  });

  it("done", () => {
    const ev = done("sid", 3, "summary", "natural");
    assert.strictEqual(ev.data.session_id, "sid");
    assert.strictEqual(ev.data.turns, 3);
  });

  it("system", () => {
    const ev = system("msg", "warning");
    assert.strictEqual(ev.data.level, "warning");
  });
});
