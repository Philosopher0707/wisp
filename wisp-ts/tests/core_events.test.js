"use strict";
/** Tests for core/events.ts */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const events_js_1 = require("../core/events.js");
(0, node_test_1.describe)("AgentEvent", () => {
    (0, node_test_1.it)("should create with defaults", () => {
        const ev = new events_js_1.AgentEvent(events_js_1.EventType.CONTENT);
        node_assert_1.default.strictEqual(ev.type, events_js_1.EventType.CONTENT);
        node_assert_1.default.deepStrictEqual(ev.data, {});
        node_assert_1.default.strictEqual(ev.schemaVersion, events_js_1.EVENT_SCHEMA_VERSION);
    });
    (0, node_test_1.it)("should round-trip via toDict/fromDict", () => {
        const original = (0, events_js_1.thinking)("hello");
        const dict = original.toDict();
        const restored = events_js_1.AgentEvent.fromDict(dict);
        node_assert_1.default.strictEqual(restored.type, original.type);
        node_assert_1.default.strictEqual(restored.data.text, "hello");
    });
    (0, node_test_1.it)("should handle flat dicts", () => {
        const ev = events_js_1.AgentEvent.fromDict({ type: "content", text: "flat", extra: 1 });
        node_assert_1.default.strictEqual(ev.type, "content");
        node_assert_1.default.strictEqual(ev.data.text, "flat");
        node_assert_1.default.strictEqual(ev.data.extra, 1);
    });
    (0, node_test_1.it)("should identify final events", () => {
        node_assert_1.default.ok((0, events_js_1.done)("s1").isFinal);
        node_assert_1.default.ok((0, events_js_1.error)("boom").isFinal);
        node_assert_1.default.ok(!(0, events_js_1.thinking)("x").isFinal);
        node_assert_1.default.ok(!(0, events_js_1.toolCall)("t", {}).isFinal);
    });
    (0, node_test_1.it)("should normalize existing AgentEvent", () => {
        const ev = (0, events_js_1.content)("hi");
        const normalized = (0, events_js_1.normalizeEvent)(ev);
        node_assert_1.default.strictEqual(normalized.type, events_js_1.EventType.CONTENT);
    });
    (0, node_test_1.it)("should normalize plain objects", () => {
        const normalized = (0, events_js_1.normalizeEvent)({ type: "error", message: "oops" });
        node_assert_1.default.strictEqual(normalized.type, "error");
        node_assert_1.default.strictEqual(normalized.data.message, "oops");
    });
});
(0, node_test_1.describe)("describeEventType", () => {
    (0, node_test_1.it)("should return descriptions", () => {
        node_assert_1.default.ok((0, events_js_1.describeEventType)(events_js_1.EventType.TOOL_CALL).includes("Tool"));
        node_assert_1.default.strictEqual((0, events_js_1.describeEventType)("unknown"), "Unknown event");
    });
});
(0, node_test_1.describe)("Event factories", () => {
    (0, node_test_1.it)("thinking", () => {
        const ev = (0, events_js_1.thinking)("reasoning...");
        node_assert_1.default.strictEqual(ev.type, events_js_1.EventType.THINKING);
        node_assert_1.default.strictEqual(ev.text, "reasoning...");
    });
    (0, node_test_1.it)("toolCall", () => {
        const ev = (0, events_js_1.toolCall)("read_file", { path: "x" });
        node_assert_1.default.strictEqual(ev.toolName, "read_file");
    });
    (0, node_test_1.it)("toolResult", () => {
        const ev = (0, events_js_1.toolResult)("read_file", "content", 12, true, "tc1");
        node_assert_1.default.strictEqual(ev.data.duration_ms, 12);
        node_assert_1.default.strictEqual(ev.data.auto_approved, true);
        node_assert_1.default.strictEqual(ev.data.tool_call_id, "tc1");
    });
    (0, node_test_1.it)("content", () => {
        const ev = (0, events_js_1.content)("hello");
        node_assert_1.default.strictEqual(ev.text, "hello");
    });
    (0, node_test_1.it)("error", () => {
        const ev = (0, events_js_1.error)("fail", false);
        node_assert_1.default.strictEqual(ev.data.recoverable, false);
    });
    (0, node_test_1.it)("done", () => {
        const ev = (0, events_js_1.done)("sid", 3, "summary", "natural");
        node_assert_1.default.strictEqual(ev.data.session_id, "sid");
        node_assert_1.default.strictEqual(ev.data.turns, 3);
    });
    (0, node_test_1.it)("system", () => {
        const ev = (0, events_js_1.system)("msg", "warning");
        node_assert_1.default.strictEqual(ev.data.level, "warning");
    });
});
//# sourceMappingURL=core_events.test.js.map