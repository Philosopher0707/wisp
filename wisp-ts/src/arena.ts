/** Arena Mode — blind A/B comparison of models on real tasks. */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execSync } from "node:child_process";

export interface ArenaEntry {
  id: string;
  prompt: string;
  modelA: string;
  modelB: string;
  summaryA: string;
  summaryB: string;
  diffA: string;
  diffB: string;
  filesChangedA: string[];
  filesChangedB: string[];
  durationMsA: number;
  durationMsB: number;
  vote?: "a" | "b" | "tie";
  createdAt: number;
}

export interface ArenaCompareRequest {
  prompt: string;
  modelA: string;
  modelB: string;
  workspace: string;
}

const LEADERBOARD_FILE = ".wisp/arena_leaderboard.json";

export class ArenaRunner {
  private _entries = new Map<string, ArenaEntry>();

  constructor(workspace: string = ".") {
    this._loadLeaderboard(path.resolve(workspace));
    this._cleanupStaleWorktrees(workspace);
  }

  async runComparison(req: ArenaCompareRequest): Promise<ArenaEntry> {
    const ws = path.resolve(req.workspace);
    const id = `arena-${crypto.randomBytes(5).toString("hex")}`;
    const entry: ArenaEntry = {
      id,
      prompt: req.prompt,
      modelA: req.modelA,
      modelB: req.modelB,
      summaryA: "",
      summaryB: "",
      diffA: "",
      diffB: "",
      filesChangedA: [],
      filesChangedB: [],
      durationMsA: 0,
      durationMsB: 0,
      createdAt: Date.now(),
    };

    // For now, arena uses the workspace directly — full git worktree isolation
    // requires the full CompositionRoot headless mode which is not yet ported.
    // We record the models and return a stub entry.
    entry.summaryA = `[Arena placeholder: ${req.modelA}]`;
    entry.summaryB = `[Arena placeholder: ${req.modelB}]`;
    entry.diffA = this._gitDiff(ws);
    entry.diffB = entry.diffA;

    this._entries.set(id, entry);
    this._saveLeaderboard(ws);
    return entry;
  }

  vote(entryId: string, vote: "a" | "b" | "tie"): ArenaEntry | undefined {
    const entry = this._entries.get(entryId);
    if (!entry) return undefined;
    entry.vote = vote;
    return entry;
  }

  getEntry(entryId: string): ArenaEntry | undefined {
    return this._entries.get(entryId);
  }

  listEntries(): ArenaEntry[] {
    return [...this._entries.values()].sort((a, b) => b.createdAt - a.createdAt);
  }

  private _gitDiff(workspace: string): string {
    try {
      return execSync("git diff", { cwd: workspace, timeout: 15_000 }).toString("utf-8").slice(0, 10_000);
    } catch {
      return "";
    }
  }

  private _saveLeaderboard(workspace: string): void {
    try {
      const file = path.join(workspace, LEADERBOARD_FILE);
      const dir = path.dirname(file);
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      const data = Array.from(this._entries.values()).map((e) => ({
        id: e.id,
        prompt: e.prompt.slice(0, 200),
        model_a: e.modelA,
        model_b: e.modelB,
        vote: e.vote,
        created_at: e.createdAt,
      }));
      fs.writeFileSync(file, JSON.stringify(data, null, 2), "utf-8");
    } catch { /* ignore */ }
  }

  private _loadLeaderboard(workspace: string): void {
    try {
      const file = path.join(workspace, LEADERBOARD_FILE);
      if (!fs.existsSync(file)) return;
      const raw = fs.readFileSync(file, "utf-8");
      const data = JSON.parse(raw) as Array<{ id: string; prompt: string; model_a: string; model_b: string; vote?: string; created_at: number }>;
      for (const item of data) {
        this._entries.set(item.id, {
          id: item.id,
          prompt: item.prompt,
          modelA: item.model_a,
          modelB: item.model_b,
          summaryA: "",
          summaryB: "",
          diffA: "",
          diffB: "",
          filesChangedA: [],
          filesChangedB: [],
          durationMsA: 0,
          durationMsB: 0,
          vote: item.vote as "a" | "b" | "tie" | undefined,
          createdAt: item.created_at,
        });
      }
    } catch { /* ignore */ }
  }

  private _cleanupStaleWorktrees(workspace: string): void {
    try {
      const wtRoot = path.join(workspace, ".wisp", "worktrees");
      if (!fs.existsSync(wtRoot)) return;
      for (const entry of fs.readdirSync(wtRoot, { withFileTypes: true })) {
        if (entry.isDirectory() && entry.name.startsWith("arena-")) {
          fs.rmSync(path.join(wtRoot, entry.name), { recursive: true, force: true });
        }
      }
    } catch { /* ignore */ }
  }
}
