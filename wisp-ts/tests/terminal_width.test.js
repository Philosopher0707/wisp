"use strict";
/** Tests for terminal_width.ts */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const terminal_width_js_1 = require("../terminal_width.js");
(0, node_test_1.describe)("displayWidth", () => {
    (0, node_test_1.it)("measures ASCII correctly", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.displayWidth)("hello"), 5);
    });
    (0, node_test_1.it)("measures CJK as 2", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.displayWidth)("你好"), 4);
    });
    (0, node_test_1.it)("ignores ANSI", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.displayWidth)("\u001b[32mok\u001b[0m"), 2);
    });
});
(0, node_test_1.describe)("stripAnsi", () => {
    (0, node_test_1.it)("removes color codes", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.stripAnsi)("\u001b[32mok\u001b[0m"), "ok");
    });
});
(0, node_test_1.describe)("wrapTextWide", () => {
    (0, node_test_1.it)("wraps text", () => {
        const lines = (0, terminal_width_js_1.wrapTextWide)("hello world foo bar", 10);
        node_assert_1.default.ok(lines.length > 1);
        node_assert_1.default.ok(lines.every((l) => (0, terminal_width_js_1.displayWidth)(l) <= 10 || l === ""));
    });
    (0, node_test_1.it)("handles empty", () => {
        node_assert_1.default.deepStrictEqual((0, terminal_width_js_1.wrapTextWide)("", 10), [""]);
    });
});
(0, node_test_1.describe)("padRight", () => {
    (0, node_test_1.it)("pads to target", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.padRight)("hi", 5), "hi   ");
    });
    (0, node_test_1.it)("no-op if already wide enough", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.padRight)("hello", 3), "hello");
    });
});
(0, node_test_1.describe)("center", () => {
    (0, node_test_1.it)("centers text", () => {
        const result = (0, terminal_width_js_1.center)("hi", 6);
        node_assert_1.default.strictEqual((0, terminal_width_js_1.displayWidth)(result), 6);
        node_assert_1.default.ok(result.includes("hi"));
    });
});
(0, node_test_1.describe)("BoxChars", () => {
    (0, node_test_1.it)("unicode mode draws corners", () => {
        const box = new terminal_width_js_1.BoxChars(terminal_width_js_1.OutputMode.UNICODE);
        node_assert_1.default.strictEqual(box.tl, "┌");
        node_assert_1.default.strictEqual(box.br, "┘");
    });
    (0, node_test_1.it)("ascii mode draws plus", () => {
        const box = new terminal_width_js_1.BoxChars(terminal_width_js_1.OutputMode.ASCII);
        node_assert_1.default.strictEqual(box.tl, "+");
        node_assert_1.default.strictEqual(box.hz, "-");
    });
    (0, node_test_1.it)("minimal mode is empty", () => {
        const box = new terminal_width_js_1.BoxChars(terminal_width_js_1.OutputMode.MINIMAL);
        node_assert_1.default.strictEqual(box.tl, "");
    });
    (0, node_test_1.it)("top with title", () => {
        const box = new terminal_width_js_1.BoxChars(terminal_width_js_1.OutputMode.UNICODE);
        const top = box.top(20, "Title");
        node_assert_1.default.ok(top.includes("Title"));
    });
    (0, node_test_1.it)("line wraps content", () => {
        const box = new terminal_width_js_1.BoxChars(terminal_width_js_1.OutputMode.UNICODE);
        const line = box.line(10, "hi");
        node_assert_1.default.ok(line.includes("hi"));
    });
});
(0, node_test_1.describe)("truncate", () => {
    (0, node_test_1.it)("does not truncate short text", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.truncate)("hi", 5), "hi");
    });
    (0, node_test_1.it)("truncates long text", () => {
        node_assert_1.default.strictEqual((0, terminal_width_js_1.truncate)("hello world", 8), "hello...");
    });
});
//# sourceMappingURL=terminal_width.test.js.map