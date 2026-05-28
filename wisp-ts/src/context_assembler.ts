/** ContextAssembler — builds system prompts from modular context sections. */

import fs from "node:fs";
import path from "node:path";
import { TokenCounter } from "./infra/token_counter.js";
import { discoverSkills, matchSkills } from "./skills.js";
import { buildCodeIndex, searchSymbols, formatCodeIndex } from "./code_index.js";
import { RepoMap } from "./repo_map.js";

export const DEFAULT_SYSTEM = `You are Wisp, a helpful coding agent.

You have access to tools that let you read, write, and edit files, run bash commands, and list directories.

## Guidelines
1. Think step by step, BUT if the user says "do it", "write it", "go ahead", "now", or any other direct action command, SKIP the analysis and EXECUTE immediately.
2. Prefer targeted edits (edit_file) over rewriting entire files.
3. Run tests after making changes to verify correctness.
4. For git operations, use run_bash with appropriate git commands.
5. If a command fails, diagnose the error and try a different approach.
6. Keep explanations concise but clear.
7. When done, summarize what was accomplished.
8. Before declaring a task done, run lsp_diagnostics on changed files.`;

const _MAX_CACHE_SIZE = 16;

export class ContextAssembler {
  private _cache = new Map<string, string>();
  defaultSystem = DEFAULT_SYSTEM;
  tokenCounter: TokenCounter;

  constructor(tokenCounter: TokenCounter) {
    this.tokenCounter = tokenCounter;
  }

  build(params: {
    workspace: string;
    defaultSystem?: string;
    roleExtra?: string;
    skillsBlock?: string;
    memoryBlock?: string;
    projectContext?: string;
    gitContext?: string;
    repoMap?: string;
    query?: string;
    compactionHistory?: Array<unknown>;
    maxTokens?: number;
  }): string {
    const key = JSON.stringify(params);
    if (this._cache.has(key)) return this._cache.get(key)!;

    const {
      workspace,
      defaultSystem: sys,
      roleExtra,
      skillsBlock,
      memoryBlock,
      projectContext,
      gitContext,
      repoMap,
      query,
      compactionHistory,
      maxTokens = 6000,
    } = params;

    const sections: Array<{ label: string; priority: number; content: string }> = [];

    sections.push({ label: "default_system", priority: 0, content: sys ?? this.defaultSystem });
    sections.push({ label: "workspace", priority: 0, content: `## Workspace\nYou are working in: ${path.resolve(workspace)}` });

    if (roleExtra) sections.push({ label: "role_extra", priority: 1, content: roleExtra });
    if (skillsBlock) sections.push({ label: "skills", priority: 1, content: skillsBlock });
    if (memoryBlock) sections.push({ label: "memory", priority: 1, content: memoryBlock });
    if (projectContext) sections.push({ label: "project", priority: 2, content: projectContext });
    if (gitContext) sections.push({ label: "git", priority: 2, content: gitContext });
    if (repoMap) sections.push({ label: "repo_map", priority: 2, content: repoMap });

    // Query-relevant files
    if (query) {
      const relevant = this._findRelevantFiles(workspace, query);
      if (relevant.length > 0) {
        sections.push({ label: "relevant", priority: 2, content: `## Relevant Files\n${relevant.map((f) => `- ${f}`).join("\n")}` });
      }
    }

    if (compactionHistory && compactionHistory.length > 0) {
      sections.push({ label: "compaction", priority: 3, content: `[Session compacted ${compactionHistory.length} times.]` });
    }

    // Tools block
    const toolsBlock = this._buildToolsBlock();
    sections.push({ label: "tools", priority: 0, content: toolsBlock });

    // Token-aware assembly
    const system = this._fitSections(sections, maxTokens);

    // Cache
    this._cache.set(key, system);
    if (this._cache.size > _MAX_CACHE_SIZE) {
      const first = this._cache.keys().next().value;
      if (first) this._cache.delete(first);
    }
    return system;
  }

  private _buildToolsBlock(): string {
    const descriptions: Record<string, string> = {
      read_file: "Read file contents",
      write_file: "Create or overwrite a file",
      edit_file: "Targeted text replacement",
      edit_file_multi: "Multiple precise edits in one file",
      run_bash: "Execute shell commands",
      list_files: "Explore directory structure",
      web_fetch: "Fetch content from URLs",
      web_search: "Search the web",
      search_symbols: "Search code symbols",
      search_codebase: "Semantic search",
      remember: "Store a fact in memory",
      recall: "Search memory",
      git_status: "Show git status",
      git_diff: "Show git diff",
      git_branch: "Manage branches",
      git_commit: "Stage and commit",
      git_push: "Push to remote",
      lsp_diagnostics: "Run diagnostics",
      diagnose: "Diagnose errors",
      run_tests: "Run test suite",
      plan_task: "Create structured plan",
      mark_step_done: "Mark plan step done",
      update_plan: "Update plan status",
    };
    const lines = ["## Tools available"];
    for (const [name, desc] of Object.entries(descriptions)) {
      lines.push(`- ${name}: ${desc}`);
    }
    return lines.join("\n");
  }

  private _findRelevantFiles(workspace: string, query: string): string[] {
    const files: string[] = [];
    const queryLower = query.toLowerCase();

    // Try code index search first
    try {
      const index = buildCodeIndex(workspace, 200);
      const symbols = searchSymbols(index, query, 10);
      for (const sym of symbols) {
        if (!files.includes(sym.file)) files.push(sym.file);
        if (files.length >= 5) break;
      }
    } catch { /* ignore */ }

    if (files.length >= 5) return files;

    // Fall back to repo map importance
    try {
      const repoMap = new RepoMap();
      repoMap.build(workspace);
      const top = repoMap.topFiles(5);
      for (const entry of top) {
        if (!files.includes(entry.path)) files.push(entry.path);
        if (files.length >= 5) break;
      }
    } catch { /* ignore */ }

    if (files.length >= 5) return files;

    // Ultimate fallback: naive name matching
    try {
      const entries = fs.readdirSync(workspace, { recursive: true, withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isFile()) continue;
        const name = entry.name.toLowerCase();
        if (queryLower.includes(name.replace(/\.(ts|js|py|rs|go)$/, ""))) {
          const rel = path.relative(workspace, path.join(entry.parentPath ?? workspace, entry.name));
          if (!files.includes(rel)) files.push(rel);
          if (files.length >= 5) break;
        }
      }
    } catch { /* ignore */ }

    return files;
  }

  private _fitSections(sections: Array<{ label: string; priority: number; content: string }>, maxTokens: number): string {
    const sorted = [...sections].sort((a, b) => a.priority - b.priority);
    const included: string[] = [];
    let currentTokens = 0;

    for (const { label, priority, content } of sorted) {
      const size = this.tokenCounter.estimate(content);
      if (currentTokens + size <= maxTokens) {
        included.push(content);
        currentTokens += size;
      } else if (priority <= 0) {
        // Critical sections: must include, truncate if needed
        const remaining = maxTokens - currentTokens;
        const maxChars = remaining * this.tokenCounter.charsPerToken;
        const truncated = content.slice(0, Math.max(0, maxChars));
        if (truncated) {
          included.push(`[${label} truncated]\n${truncated}`);
          currentTokens += this.tokenCounter.estimate(truncated);
        }
      }
      // Drop lower-priority sections silently
    }

    return included.join("\n\n");
  }

  invalidateCache(): void {
    this._cache.clear();
  }
}

/** Build full system prompt with skill discovery */
export function buildSystemPrompt(params: {
  workspace: string;
  query?: string;
  roleExtra?: string;
  memoryBlock?: string;
  gitContext?: string;
  repoMap?: string;
  compactionHistory?: Array<unknown>;
  tokenCounter: TokenCounter;
}): string {
  const { workspace, query, roleExtra, memoryBlock, gitContext, repoMap, compactionHistory, tokenCounter } = params;
  const assembler = new ContextAssembler(tokenCounter);

  // Discover skills
  let skillsBlock = "";
  try {
    const skills = discoverSkills(workspace);
    if (skills.length > 0) {
      const lines = ["## Skills"];
      for (const s of skills) {
        lines.push(`- ${s.name}: ${s.description}`);
      }
      skillsBlock = lines.join("\n");
    }
  } catch {
    // ignore
  }

  // Auto-detect skill from query
  if (query) {
    try {
      const matched = matchSkills(query, workspace);
      if (matched.length > 0 && matched[0][1] >= 2) {
        const skill = matched[0][0];
        skillsBlock += `\n\n## Suggested Skill: ${skill.name}\n${skill.description}\n${skill.instructions.slice(0, 500)}`;
      }
    } catch {
      // ignore
    }
  }

  return assembler.build({
    workspace,
    roleExtra,
    skillsBlock,
    memoryBlock,
    gitContext,
    repoMap,
    query,
    compactionHistory,
  });
}
