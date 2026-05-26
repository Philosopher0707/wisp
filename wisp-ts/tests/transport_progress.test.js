"use strict";
/** Tests for transport/progress.ts */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const progress_js_1 = require("../transport/progress.js");
const events_js_1 = require("../core/events.js");
(0, node_test_1.describe)("ProgressTracker", () => {
    (0, node_test_1.it)("starts in understand phase", () => {
        const pt = new progress_js_1.ProgressTracker();
        pt.startTurn(1);
        node_assert_1.default.strictEqual(pt.progress.phase, progress_js_1.UNDERSTAND);
        node_assert_1.default.strictEqual(pt.progress.turnNumber, 1);
    });
    (0, node_test_1.it)("advances to plan on plan_task", () => {
        const pt = new progress_js_1.ProgressTracker();
        pt.startTurn(1);
        const phase = pt.onEvent((0, events_js_1.toolCall)("plan_task", { goal: "x" }));
        node_assert_1.default.strictEqual(phase, progress_js_1.PLAN);
        node_assert_1.default.strictEqual(pt.progress.phase, progress_js_1.PLAN);
    });
    (0, node_test_1.it)("advances to execute on write_file", () => {
        const pt = new progress_js_1.ProgressTracker();
        pt.startTurn(1);
        pt.onEvent((0, events_js_1.toolCall)("write_file", { path: "a.ts", content: "x" }));
        node_assert_1.default.strictEqual(pt.progress.phase, progress_js_1.EXECUTE);
    });
    (0, node_test_1.it)("tracks tool counts", () => {
        const pt = new progress_js_1.ProgressTracker();
        pt.startTurn(1);
        pt.onEvent((0, events_js_1.toolCall)("read_file", { path: "x" }));
        pt.onToolResult("read_file", "ok");
        const stats = pt.onDone();
        node_assert_1.default.strictEqual(stats.toolsRun, 1);
        node_assert_1.default.strictEqual(stats.toolsSucceeded, 1);
        node_assert_1.default.strictEqual(stats.toolsFailed, 0);
    });
    (0, node_test_1.it)("tracks file changes", () => {
        const pt = new progress_js_1.ProgressTracker();
        pt.startTurn(1);
        pt.onEvent((0, events_js_1.toolCall)("write_file", { path: "foo.ts", content: "" }));
        pt.onToolResult("write_file", "ok");
        const stats = pt.onDone();
        node_assert_1.default.deepStrictEqual(stats.filesChanged, ["foo.ts"]);
    });
    (0, node_test_1.it)("computes elapsed", async () => {
        const pt = new progress_js_1.ProgressTracker();
        pt.startTurn(1);
        await new Promise((r) => setTimeout(r, 50));
        const elapsed = pt.elapsed;
        node_assert_1.default.ok(elapsed >= 0.04, `expected elapsed >= 0.04, got ${elapsed}`);
    });
});
(0, node_test_1.describe)("TurnProgress", () => {
    (0, node_test_1.it)("has defaults", () => {
        const tp = new progress_js_1.TurnProgress();
        node_assert_1.default.strictEqual(tp.turnNumber, 0);
        node_assert_1.default.strictEqual(tp.phase, progress_js_1.UNDERSTAND);
        node_assert_1.default.deepStrictEqual(tp.filesChanged, []);
    });
});
//# sourceMappingURL=transport_progress.test.js.map