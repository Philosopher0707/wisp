/** RepoMap — dependency-aware codebase index with importance scoring. */

import fs from "node:fs";
import path from "node:path";

export interface RepoEntry {
  path: string;
  name: string;
  kind: string;
  dependencies: string[];
  dependents: string[];
  importance: number;
}

export class RepoMap {
  private _entries: Map<string, RepoEntry> = new Map();
  private _deps: Map<string, Set<string>> = new Map();
  private _revDeps: Map<string, Set<string>> = new Map();

  build(workspace: string): void {
    const ws = path.resolve(workspace);
    this._scanDir(ws);
    this._computePageRank();
  }

  private _scanDir(dir: string): void {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "node_modules" || entry.name === ".git" || entry.name.startsWith(".")) continue;
        this._scanDir(full);
      } else if (entry.isFile() && /\.(ts|js|tsx|jsx|py|rs|go)$/.test(entry.name)) {
        this._parseFile(full, dir);
      }
    }
  }

  private _parseFile(filePath: string, workspace: string): void {
    try {
      const content = fs.readFileSync(filePath, "utf-8");
      const rel = path.relative(workspace, filePath);
      // Extract imports/exports as simple dependencies
      const importPattern = /from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
      const deps: string[] = [];
      let m: RegExpExecArray | null;
      while ((m = importPattern.exec(content)) !== null) {
        const dep = m[1] || m[2];
        if (dep && !dep.startsWith(".")) continue; // skip external deps
        if (dep) deps.push(dep);
      }

      const entry: RepoEntry = {
        path: rel,
        name: path.basename(filePath),
        kind: "file",
        dependencies: deps,
        dependents: [],
        importance: 0,
      };
      this._entries.set(rel, entry);
      this._deps.set(rel, new Set(deps));

      for (const dep of deps) {
        const resolved = path.resolve(path.dirname(filePath), dep);
        const candidates = [
          resolved,
          resolved + ".ts",
          resolved + ".js",
          resolved + "/index.ts",
          resolved + "/index.js",
        ];
        for (const c of candidates) {
          if (fs.existsSync(c)) {
            const depRel = path.relative(workspace, c);
            if (!this._revDeps.has(depRel)) this._revDeps.set(depRel, new Set());
            this._revDeps.get(depRel)!.add(rel);
            break;
          }
        }
      }
    } catch { /* ignore */ }
  }

  private _computePageRank(iterations = 10, damping = 0.85): void {
    const n = this._entries.size;
    if (n === 0) return;
    const base = (1 - damping) / n;
    const scores = new Map<string, number>();
    for (const key of this._entries.keys()) scores.set(key, 1 / n);

    for (let i = 0; i < iterations; i++) {
      const newScores = new Map<string, number>();
      for (const [key, entry] of this._entries) {
        let score = base;
        const dependents = this._revDeps.get(key) ?? new Set();
        for (const dep of dependents) {
          const depEntry = this._entries.get(dep);
          if (!depEntry) continue;
          const depLinks = depEntry.dependencies.length || 1;
          const depScore = scores.get(dep) ?? 0;
          score += (damping * depScore) / depLinks;
        }
        newScores.set(key, score);
        entry.importance = score;
      }
      for (const [k, v] of newScores) scores.set(k, v);
    }
  }

  topFiles(k = 20): RepoEntry[] {
    return [...this._entries.values()]
      .sort((a, b) => b.importance - a.importance)
      .slice(0, k);
  }

  format(): string {
    const files = this.topFiles(30);
    if (!files.length) return "## Repo Map\n(no source files found)";
    const maxWidth = Math.max(...files.map((f) => f.path.length));
    const lines = files.map((f) => {
      const stars = "⭐".repeat(Math.min(5, Math.ceil(f.importance * 5)));
      return `${f.path.padEnd(maxWidth)} ${stars}`;
    });
    return "## Repo Map\n" + lines.join("\n");
  }
}
