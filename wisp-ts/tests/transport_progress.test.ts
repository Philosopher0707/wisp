/** Tests for transport/progress.ts */

import { describe, it } from "node:test";
import assert from "node:assert";
import { ProgressTracker, TurnProgress, UNDERSTAND, PLAN, EXECUTE, VERIFY } from "../src/transport/progress.js";
import { AgentEvent, toolCall, toolResult } from "../src/core/events.js";

describe("ProgressTracker", () => {
  it("starts in understand phase", () => {
    const pt = new ProgressTracker();
    pt.startTurn(1);
    assert.strictEqual(pt.progress.phase, UNDERSTAND);
    assert.strictEqual(pt.progress.turnNumber, 1);
  });

  it("advances to plan on plan_task", () => {
    const pt = new ProgressTracker();
    pt.startTurn(1);
    const phase = pt.onEvent(toolCall("plan_task", { goal: "x" }));
    assert.strictEqual(phase, PLAN);
    assert.strictEqual(pt.progress.phase, PLAN);
  });

  it("advances to execute on write_file", () => {
    const pt = new ProgressTracker();
    pt.startTurn(1);
    pt.onEvent(toolCall("write_file", { path: "a.ts", content: "x" }));
    assert.strictEqual(pt.progress.phase, EXECUTE);
  });

  it("tracks tool counts", () => {
    const pt = new ProgressTracker();
    pt.startTurn(1);
    pt.onEvent(toolCall("read_file", { path: "x" }));
    pt.onToolResult("read_file", "ok");
    const stats = pt.onDone();
    assert.strictEqual(stats.toolsRun, 1);
    assert.strictEqual(stats.toolsSucceeded, 1);
    assert.strictEqual(stats.toolsFailed, 0);
  });

  it("tracks file changes", () => {
    const pt = new ProgressTracker();
    pt.startTurn(1);
    pt.onEvent(toolCall("write_file", { path: "foo.ts", content: "" }));
    pt.onToolResult("write_file", "ok");
    const stats = pt.onDone();
    assert.deepStrictEqual(stats.filesChanged, ["foo.ts"]);
  });

  it("computes elapsed", async () => {
    const pt = new ProgressTracker();
    pt.startTurn(1);
    await new Promise((r) => setTimeout(r, 50));
    const elapsed = pt.elapsed;
    assert.ok(elapsed >= 0.04, `expected elapsed >= 0.04, got ${elapsed}`);
  });
});

describe("TurnProgress", () => {
  it("has defaults", () => {
    const tp = new TurnProgress();
    assert.strictEqual(tp.turnNumber, 0);
    assert.strictEqual(tp.phase, UNDERSTAND);
    assert.deepStrictEqual(tp.filesChanged, []);
  });
});
