/** Integration tests: tool registry execution across domains. */

import { describe, it } from "node:test";
import assert from "node:assert";
import { ToolRegistry, executeTool } from "../src/tools/registry.js";
import { toolRemember, toolRecall } from "../src/tools/memory.js";
import { toolPlanTask, toolMarkStepDone } from "../src/tools/plan.js";

describe("ToolRegistry execute", () => {
  it("executes read_file with missing file", async () => {
    const registry = new ToolRegistry();
    try {
      await registry.execute("read_file", { path: "nonexistent_file_xyz.txt" }, ".");
      assert.fail("Should have thrown");
    } catch (e: any) {
      assert.ok(e.message.includes("not found") || e.message.includes("ENOENT") || e.message.includes("No such") || e.message.includes("escapes"));
    }
  });

  it("executes list_files on current directory", async () => {
    const registry = new ToolRegistry();
    const result = await registry.execute("list_files", { path: "." }, ".");
    assert.ok(typeof result === "string");
    assert.ok((result as string).length > 0);
  });

  it("executes git_status in non-git directory", async () => {
    const registry = new ToolRegistry();
    const result = await registry.execute("git_status", {}, "/tmp");
    assert.ok(typeof result === "string");
  });

  it("executes remember then recall", async () => {
    const result1 = toolRemember("test-fact-123", ".");
    assert.ok(result1.includes("Remembered"));
    const result2 = toolRecall("test-fact", ".", 10);
    assert.ok(result2.includes("test-fact-123"));
  });

  it("executes plan_task then mark_step_done", async () => {
    const planResult = toolPlanTask("Build feature", "1. [low] Write code\n2. [medium] Test code", ".");
    assert.ok(planResult.includes("Created plan"));
    const doneResult = toolMarkStepDone("1", "Done", ".");
    assert.ok(doneResult.includes("Marked task"));
  });

  it("executes diagnose on TypeScript error", async () => {
    const registry = new ToolRegistry();
    const result = await registry.execute("diagnose", { error_output: "src/app.ts(42,5): error TS2345: Argument of type 'string'..." }, ".");
    assert.ok(typeof result === "string");
    assert.ok((result as string).includes("TypeScript"));
  });

  it("executes search_symbols in current project", async () => {
    const registry = new ToolRegistry();
    const result = await registry.execute("search_symbols", { query: "AgentRuntime" }, ".");
    assert.ok(typeof result === "string");
  });

  it("executes web_search with fallback", async () => {
    const registry = new ToolRegistry();
    const result = await registry.execute("web_search", { query: "typescript", num_results: 3 }, ".");
    assert.ok(typeof result === "string");
    const parsed = JSON.parse(result as string);
    assert.ok(parsed.status === "ok" || parsed.status === "error");
  });
});

describe("executeTool top-level", () => {
  it("rejects unknown tool", async () => {
    await assert.rejects(executeTool("nonexistent", {}, "."), /Unknown tool/);
  });

  it("executes lsp_diagnostics stub", async () => {
    const result = await executeTool("lsp_diagnostics", { path: "test.ts" }, ".");
    assert.ok(typeof result === "string");
    assert.ok((result as string).includes("not yet integrated"));
  });
});

describe("ToolRegistry schema access", () => {
  it("returns all schemas", () => {
    const registry = new ToolRegistry();
    const schemas = registry.schemas();
    assert.ok(schemas.length >= 20, `Expected >= 20 schemas, got ${schemas.length}`);
    const names = schemas.map((s) => s.function.name);
    assert.ok(names.includes("read_file"));
    assert.ok(names.includes("run_bash"));
    assert.ok(names.includes("web_fetch"));
    assert.ok(names.includes("remember"));
    assert.ok(names.includes("plan_task"));
    assert.ok(names.includes("lsp_diagnostics"));
  });

  it("has() checks tool existence", () => {
    const registry = new ToolRegistry();
    assert.ok(registry.has("read_file"));
    assert.ok(registry.has("web_search"));
    assert.ok(!registry.has("nonexistent"));
  });
});
