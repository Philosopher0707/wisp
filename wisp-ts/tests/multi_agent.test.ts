/** Tests for multi-agent system */

import { describe, it } from "node:test";
import assert from "node:assert";
import { SubagentContract, SubagentResult, OrchestratorEvent, EventKind } from "../src/multi_agent/task.js";
import { ROLE_CONFIGS, AgentRole } from "../src/multi_agent/roles.js";
import { DelegationAnalyzer, DelegationSignal } from "../src/multi_agent/delegation.js";
import { SubagentOrchestrator, BudgetTracker, ResultCache, Telemetry } from "../src/multi_agent/orchestrator.js";
import { TaskDAG, TaskNode, DAGScheduler, DAGResult } from "../src/multi_agent/dag.js";
import { runMapReduce, runVote, runChain } from "../src/multi_agent/patterns.js";
import { WispConfig } from "../src/config.js";

describe("SubagentContract", () => {
  it("has defaults", () => {
    const c = new SubagentContract();
    assert.strictEqual(c.role, "generalist");
    assert.deepStrictEqual(c.tools, ["all"]);
  });

  it("maps prompt to task", () => {
    const c = new SubagentContract({ prompt: "do thing" });
    assert.strictEqual(c.task, "do thing");
  });
});

describe("SubagentResult", () => {
  it("normalizes duration alias", () => {
    const r = new SubagentResult({ durationSeconds: 5 });
    assert.strictEqual(r.elapsedSeconds, 5);
  });
});

describe("ROLE_CONFIGS", () => {
  it("has all roles", () => {
    assert.ok(ROLE_CONFIGS[AgentRole.CODER]);
    assert.ok(ROLE_CONFIGS[AgentRole.REVIEWER]);
    assert.ok(ROLE_CONFIGS[AgentRole.TESTER]);
    assert.ok(ROLE_CONFIGS[AgentRole.RESEARCHER]);
    assert.ok(ROLE_CONFIGS[AgentRole.PLANNER]);
    assert.ok(ROLE_CONFIGS[AgentRole.DEBUGGER]);
    assert.ok(ROLE_CONFIGS[AgentRole.GENERALIST]);
  });

  it("coder has write tools", () => {
    assert.ok(ROLE_CONFIGS.coder.allowedTools.includes("write_file"));
  });

  it("reviewer cannot write files", () => {
    assert.ok(!ROLE_CONFIGS.reviewer.allowedTools.includes("write_file"));
  });
});

describe("DelegationAnalyzer", () => {
  it("delegates complex tasks", () => {
    const a = new DelegationAnalyzer();
    const sig = a.analyze("Implement a full-stack auth system with JWT, refresh tokens, and role-based access control across the entire codebase");
    assert.ok(sig.shouldDelegate);
    assert.ok(sig.confidence > 0.3);
    assert.ok(sig.suggestedContracts.length > 0);
  });

  it("does not delegate simple tasks", () => {
    const a = new DelegationAnalyzer();
    const sig = a.analyze("What is 2+2?");
    assert.ok(!sig.shouldDelegate);
  });

  it("triggers on research keywords", () => {
    const a = new DelegationAnalyzer();
    const sig = a.analyze("Research the best way to handle concurrency in Rust");
    assert.ok(sig.shouldDelegate);
  });

  it("suggests contracts for complex tasks", () => {
    const a = new DelegationAnalyzer();
    const sig = a.analyze("Build a new API endpoint with tests and documentation");
    assert.ok(sig.suggestedContracts.some((c) => c.role === "coder"));
  });
});

describe("SubagentOrchestrator", () => {
  it("enforces depth limit", async () => {
    const orch = new SubagentOrchestrator(new WispConfig(), ".");
    const contract = new SubagentContract({ name: "deep", task: "x", subagentDepth: 10 });
    const result = await orch.run(contract);
    assert.ok(!result.success);
    assert.ok(result.output.includes("DEPTH LIMIT"));
  });

  it("enforces role validation", async () => {
    const orch = new SubagentOrchestrator(new WispConfig(), ".");
    const contract = new SubagentContract({ name: "bad", task: "x", role: "unknown_role" });
    const result = await orch.run(contract);
    assert.ok(!result.success);
    assert.ok(result.error?.includes("Unknown role"));
  });

  it("rejects invalid timeout", async () => {
    const orch = new SubagentOrchestrator(new WispConfig(), ".");
    const contract = new SubagentContract({ name: "bad", task: "x", timeoutSeconds: 0 });
    const result = await orch.run(contract);
    assert.ok(!result.success);
  });

  it("runs parallel subagents", async () => {
    const orch = new SubagentOrchestrator(new WispConfig({ provider: "mock" }), ".");
    const contracts = [
      new SubagentContract({ name: "a", task: "task a" }),
      new SubagentContract({ name: "b", task: "task b" }),
    ];
    const results = await orch.runParallel(contracts, 2);
    assert.strictEqual(results.length, 2);
    assert.ok(results.every((r) => r.success));
  });

  it("spawnWithGuards respects depth", async () => {
    const orch = new SubagentOrchestrator(new WispConfig(), ".");
    const out = await orch.spawnWithGuards("test", { depth: 10 });
    assert.ok(out.includes("depth"));
  });
});

describe("BudgetTracker", () => {
  it("tracks consumption", () => {
    const bt = new BudgetTracker();
    bt.setBudget(1000);
    bt.record(200);
    assert.strictEqual(bt.getConsumed(), 200);
    assert.strictEqual(bt.getRemaining(), 800);
    assert.strictEqual(bt.getRatio(), 0.8);
    assert.strictEqual(bt.check(), null);
  });

  it("flags exhaustion", () => {
    const bt = new BudgetTracker();
    bt.setBudget(100);
    bt.record(150);
    assert.ok(bt.check()?.includes("exhausted"));
  });
});

describe("ResultCache", () => {
  it("caches and retrieves", () => {
    const cache = new ResultCache();
    const contract = new SubagentContract({ name: "x", task: "t" });
    const result = new SubagentResult({ taskId: "x", success: true });
    cache.set(contract, result);
    const got = cache.get(contract);
    assert.ok(got);
    assert.strictEqual(got!.success, true);
  });

  it("reports stats", () => {
    const cache = new ResultCache();
    const contract = new SubagentContract({ name: "x", task: "t" });
    cache.set(contract, new SubagentResult({ taskId: "x" }));
    cache.get(contract);
    const stats = cache.stats();
    assert.strictEqual(stats.hits, 1);
  });
});

describe("Telemetry", () => {
  it("records and summarizes", () => {
    const t = new Telemetry();
    t.record("m1", new SubagentResult({ taskId: "x", success: true, elapsedSeconds: 2, tokensUsed: 100 }));
    t.record("m1", new SubagentResult({ taskId: "y", success: false, elapsedSeconds: 1, tokensUsed: 50 }));
    const summary = t.summary();
    assert.strictEqual(summary.m1.count, 2);
    assert.strictEqual(summary.m1.success_rate, 0.5);
  });
});

describe("TaskDAG", () => {
  it("detects cycles", () => {
    const dag = new TaskDAG();
    dag.addNode(new TaskNode("a", "task a", ["b"]));
    dag.addNode(new TaskNode("b", "task b", ["a"]));
    const errors = dag.validate();
    assert.ok(errors.some((e) => e.includes("Cycle")));
  });

  it("computes topological levels", () => {
    const dag = new TaskDAG();
    dag.addNode(new TaskNode("a", "1"));
    dag.addNode(new TaskNode("b", "2", ["a"]));
    dag.addNode(new TaskNode("c", "3", ["a"]));
    dag.addNode(new TaskNode("d", "4", ["b", "c"]));
    const levels = dag.topologicalLevels();
    assert.deepStrictEqual(levels[0], ["a"]);
    assert.deepStrictEqual(levels[levels.length - 1], ["d"]);
  });

  it("validates acyclic graph", () => {
    const dag = new TaskDAG();
    dag.addNode(new TaskNode("root", "r"));
    dag.addNode(new TaskNode("child", "c", ["root"]));
    assert.deepStrictEqual(dag.validate(), []);
  });
});

describe("DAGScheduler", () => {
  it("executes levels in order", async () => {
    const dag = new TaskDAG();
    dag.addNode(new TaskNode("a", async () => "done-a"));
    dag.addNode(new TaskNode("b", async () => "done-b", ["a"]));
    const scheduler = new DAGScheduler(4, 5);
    const result = await scheduler.execute(dag, async (node) => node.task);
    assert.strictEqual(result.success, true);
    assert.ok(result.nodeResults.has("a"));
    assert.ok(result.nodeResults.has("b"));
  });

  it("fails on invalid DAG", async () => {
    const dag = new TaskDAG();
    dag.addNode(new TaskNode("a", "1", ["b"]));
    dag.addNode(new TaskNode("b", "2", ["a"]));
    const scheduler = new DAGScheduler();
    const result = await scheduler.execute(dag, async (node) => node.task);
    assert.strictEqual(result.success, false);
  });
});

describe("Patterns", () => {
  it("runChain passes context", async () => {
    const orch = new SubagentOrchestrator(new WispConfig({ provider: "mock" }), ".");
    const contracts = [
      new SubagentContract({ name: "step1", task: "first" }),
      new SubagentContract({ name: "step2", task: "second" }),
    ];
    const result = await runChain(orch, contracts, true);
    assert.ok(result.success);
    assert.ok(result.output.includes("Chain Complete"));
  });

  it("runVote with no agents fails", async () => {
    const orch = new SubagentOrchestrator(new WispConfig(), ".");
    const result = await runVote(orch, "question", []);
    assert.ok(!result.success);
  });

  it("runMapReduce with no items fails", async () => {
    const orch = new SubagentOrchestrator(new WispConfig(), ".");
    const result = await runMapReduce(orch, "task", [], (item) => new SubagentContract({ name: item, task: item }), "reduce");
    assert.ok(!result.success);
  });
});
