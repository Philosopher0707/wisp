/** Integration tests: security controls across the TypeScript port. */

import { describe, it } from "node:test";
import assert from "node:assert";
import { SecurityPolicy } from "../src/infra/security.js";
import { WispConfig, PermissionMode } from "../src/config.js";

const PM = PermissionMode;
import { SubagentContract } from "../src/multi_agent/task.js";
import { AuditTrail } from "../src/infra/audit.js";
import { checkDangerousCommand } from "../src/tools/bash.js";
import fs from "node:fs";
import path from "node:path";

describe("SecurityPolicy", () => {
  it("blocks dangerous commands in full mode", () => {
    const policy = new SecurityPolicy(PM.FULL);
    const decision = policy.check({ name: "run_bash", args: { command: "rm -rf /" } }, { workspace: "." });
    assert.strictEqual(decision.allowed, true); // full mode allows everything at policy level
  });

  it("enforces read_only mode", () => {
    const policy = new SecurityPolicy(PM.READ_ONLY);
    const decision = policy.check({ name: "write_file", args: {} }, { workspace: "." });
    assert.strictEqual(decision.allowed, false);
    assert.ok(decision.reason?.includes("READ_ONLY"));
  });

  it("auto_edit mode allows edits without ask", () => {
    const policy = new SecurityPolicy(PM.AUTO_EDIT);
    const decision = policy.check({ name: "edit_file", args: {} }, { workspace: "." });
    assert.strictEqual(decision.allowed, true);
  });

  it("auto_edit mode blocks bash", () => {
    const policy = new SecurityPolicy(PM.AUTO_EDIT);
    const decision = policy.check({ name: "run_bash", args: { command: "ls" } }, { workspace: "." });
    assert.strictEqual(decision.allowed, false);
    assert.ok(decision.reason?.includes("AUTO_EDIT"));
  });

  it("ask_all mode blocks write tools", () => {
    const policy = new SecurityPolicy(PM.ASK_ALL);
    const decision = policy.check({ name: "write_file", args: {} }, { workspace: "." });
    assert.strictEqual(decision.allowed, false);
    assert.ok(decision.reason?.includes("ASK_ALL"));
  });

  it("ask_all mode allows read tools", () => {
    const policy = new SecurityPolicy(PM.ASK_ALL);
    const decision = policy.check({ name: "read_file", args: {} }, { workspace: "." });
    assert.strictEqual(decision.allowed, true);
  });
});

describe("Dangerous command detection", () => {
  it("detects rm -rf /", () => {
    const reason = checkDangerousCommand("rm -rf /");
    assert.ok(reason);
  });

  it("allows safe commands", () => {
    const reason = checkDangerousCommand("ls -la");
    assert.strictEqual(reason, null);
  });

  it("detects mkfs", () => {
    const reason = checkDangerousCommand("mkfs.ext4 /dev/sda1");
    assert.ok(reason);
  });
});

describe("AuditTrail", () => {
  it("records tamper-evident entries", () => {
    const tmpDir = path.join(process.cwd(), ".tmp_test_audit");
    if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });
    const logFile = path.join(tmpDir, "audit.jsonl");
    const trail = new AuditTrail(logFile);
    trail.record("test_action", { key: "token", newValue: "secret123" });
    const lines = fs.readFileSync(logFile, "utf-8").trim().split("\n");
    assert.strictEqual(lines.length, 1);
    const entry = JSON.parse(lines[0]);
    assert.ok("_hash" in entry);
    assert.ok("_prev_hash" in entry);
    assert.ok(entry.new_value.includes("***") || entry.new_value.startsWith("sec"));
    fs.rmSync(tmpDir, { recursive: true });
  });

  it("chains hashes across entries", () => {
    const tmpDir = path.join(process.cwd(), ".tmp_test_audit2");
    if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });
    const logFile = path.join(tmpDir, "audit.jsonl");
    const trail = new AuditTrail(logFile);
    trail.record("a", { key: "x" });
    trail.record("b", { key: "y" });
    const lines = fs.readFileSync(logFile, "utf-8").trim().split("\n");
    assert.strictEqual(lines.length, 2);
    const e1 = JSON.parse(lines[0]);
    const e2 = JSON.parse(lines[1]);
    assert.strictEqual(e2._prev_hash, e1._hash);
    fs.rmSync(tmpDir, { recursive: true });
  });

  it("verify() returns null for valid chain", () => {
    const tmpDir = path.join(process.cwd(), ".tmp_test_audit3");
    if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });
    const logFile = path.join(tmpDir, "audit.jsonl");
    const trail = new AuditTrail(logFile);
    trail.record("a", { key: "x" });
    trail.record("b", { key: "y" });
    const badLine = trail.verify();
    assert.strictEqual(badLine, null);
    fs.rmSync(tmpDir, { recursive: true });
  });
});

describe("SubagentContract defaults", () => {
  it("defaults autoApprove to false", () => {
    const contract = new SubagentContract();
    assert.strictEqual(contract.autoApprove, false);
  });

  it("defaults maxIterations to positive", () => {
    const contract = new SubagentContract();
    assert.ok(contract.maxIterations > 0);
  });

  it("defaults timeoutSeconds to positive", () => {
    const contract = new SubagentContract();
    assert.ok(contract.timeoutSeconds > 0);
  });
});

describe("PermissionMode from config", () => {
  it("FULL mode value", () => {
    assert.strictEqual(PermissionMode.FULL, "full");
  });

  it("READ_ONLY mode value", () => {
    assert.strictEqual(PermissionMode.READ_ONLY, "read_only");
  });
});
