/** WorktreeManager — git worktree lifecycle for isolated subagents. */

import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import crypto from "node:crypto";

export class WorktreeManager {
  workspace: string;
  private _worktreesRoot: string;

  constructor(workspace: string) {
    this.workspace = workspace;
    this._worktreesRoot = path.join(workspace, ".wisp", "worktrees");
  }

  private async _exec(args: string[], cwd?: string): Promise<{ stdout: string; stderr: string; code: number }> {
    return new Promise((resolve) => {
      const child = spawn("git", args, {
        cwd: cwd ?? this.workspace,
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
      });
      let stdout = "";
      let stderr = "";
      child.stdout.on("data", (d) => (stdout += d));
      child.stderr.on("data", (d) => (stderr += d));
      child.on("close", (code) => resolve({ stdout, stderr, code: code ?? 1 }));
    });
  }

  async create(agentName: string): Promise<string> {
    const check = await this._exec(["rev-parse", "--show-toplevel"]);
    if (check.code !== 0) throw new Error(`Workspace is not a git repo: ${check.stderr}`);

    fs.mkdirSync(this._worktreesRoot, { recursive: true });
    const shortId = crypto.randomUUID().slice(0, 8);
    const safeName = agentName.replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 32).replace(/^-|-$/g, "") || "subagent";
    const dirName = `${safeName}-${shortId}`;
    const branchName = `wisp-subagent/${safeName}-${shortId}`;
    const worktreePath = path.resolve(this._worktreesRoot, dirName);

    const add = await this._exec(["worktree", "add", worktreePath, "-b", branchName]);
    if (add.code !== 0) throw new Error(`git worktree add failed: ${add.stderr}`);

    // Sync uncommitted diff
    const diff = await this._exec(["diff", "HEAD"]);
    if (diff.stdout.trim()) {
      const apply = spawn("git", ["apply"], { cwd: worktreePath, stdio: ["pipe", "pipe", "pipe"] });
      apply.stdin?.write(diff.stdout);
      apply.stdin?.end();
      await new Promise((r) => apply.on("close", r));
    }

    return worktreePath;
  }

  async detectFilesChanged(worktreePath: string): Promise<string[]> {
    const result = await this._exec(["diff", "--name-only", "HEAD"], worktreePath);
    return result.stdout.split("\n").map((l) => l.trim()).filter(Boolean);
  }

  async getPatch(worktreePath: string): Promise<string> {
    const add = await this._exec(["add", "-A"], worktreePath);
    if (add.code !== 0) return "";
    const diff = await this._exec(["diff", "HEAD"], worktreePath);
    await this._exec(["reset", "HEAD"], worktreePath);
    return diff.stdout;
  }

  async applyPatch(patch: string): Promise<boolean> {
    if (!patch.trim()) return true;
    const apply = spawn("git", ["apply", "--3way"], {
      cwd: this.workspace,
      stdio: ["pipe", "pipe", "pipe"],
    });
    apply.stdin?.write(patch);
    apply.stdin?.end();
    const code = await new Promise<number>((r) => apply.on("close", (c) => r(c ?? 1)));
    if (code === 0) return true;
    // fallback: best-effort apply
    const fallback = spawn("git", ["apply", "--reject", "--whitespace=fix"], {
      cwd: this.workspace,
      stdio: ["pipe", "pipe", "pipe"],
    });
    fallback.stdin?.write(patch);
    fallback.stdin?.end();
    const fbCode = await new Promise<number>((r) => fallback.on("close", (c) => r(c ?? 1)));
    return fbCode === 0;
  }

  async cleanup(worktreePath: string): Promise<void> {
    const remove = await this._exec(["worktree", "remove", "--force", worktreePath]);
    if (remove.code !== 0 && fs.existsSync(worktreePath)) {
      // Force delete
      fs.rmSync(worktreePath, { recursive: true, force: true });
    }
  }
}
