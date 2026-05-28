/** Lightweight code index — regex-based symbol scanner */

import fs from "node:fs";
import path from "node:path";

export interface CodeIndexSymbol {
  name: string;
  kind: string;
  file: string;
  line: number;
  parent?: string;
}

export interface CodeIndex {
  symbols: Map<string, CodeIndexSymbol[]>;
  total: number;
  languages: Set<string>;
}

const CODE_PATTERNS: Record<string, RegExp> = {
  ts: /(?:export\s+)?(?:function|class|interface|type|enum|const|let|var)\s+([A-Za-z0-9_]+)/g,
  js: /(?:export\s+)?(?:function|class|const|let|var)\s+([A-Za-z0-9_]+)/g,
  py: /(?:async\s+)?def\s+([A-Za-z0-9_]+)|class\s+([A-Za-z0-9_]+)/g,
  rs: /(?:pub\s+)?(?:fn|struct|enum|trait|impl)\s+([A-Za-z0-9_]+)/g,
  go: /(?:func|type|struct|interface)\s+(?:\([^)]+\)\s+)?([A-Za-z0-9_]+)/g,
};

export function buildCodeIndex(workspace: string, maxFiles = 500): CodeIndex {
  const index: CodeIndex = { symbols: new Map(), total: 0, languages: new Set() };

  function scan(dir: string) {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "node_modules" || entry.name === ".git" || entry.name.startsWith(".")) continue;
        scan(full);
      } else if (entry.isFile()) {
        const ext = path.extname(entry.name).slice(1);
        if (!CODE_PATTERNS[ext]) continue;
        if (index.symbols.size >= maxFiles) return;
        parseFile(full, ext, workspace, index);
      }
    }
  }

  try { scan(path.resolve(workspace)); } catch { /* ignore */ }
  return index;
}

function parseFile(filePath: string, ext: string, workspace: string, index: CodeIndex) {
  try {
    const content = fs.readFileSync(filePath, "utf-8");
    const rel = path.relative(workspace, filePath);
    const pattern = CODE_PATTERNS[ext];
    const lines = content.split("\n");
    index.languages.add(ext);

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let match: RegExpExecArray | null;
      pattern.lastIndex = 0;
      while ((match = pattern.exec(line)) !== null) {
        const name = match[1] || match[2];
        if (!name) continue;
        let kind = "function";
        if (line.includes("class")) kind = "class";
        else if (line.includes("interface")) kind = "interface";
        else if (line.includes("struct")) kind = "struct";
        else if (line.includes("trait")) kind = "trait";
        else if (line.includes("enum")) kind = "enum";

        const sym: CodeIndexSymbol = { name, kind, file: rel, line: i + 1 };
        const arr = index.symbols.get(rel) ?? [];
        arr.push(sym);
        index.symbols.set(rel, arr);
        index.total++;
      }
    }
  } catch { /* ignore */ }
}

export function searchSymbols(index: CodeIndex, query: string, maxResults = 20): CodeIndexSymbol[] {
  const q = query.toLowerCase();
  const results: CodeIndexSymbol[] = [];
  for (const [_, syms] of index.symbols.entries()) {
    for (const sym of syms) {
      if (sym.name.toLowerCase().includes(q)) {
        results.push(sym);
        if (results.length >= maxResults) break;
      }
    }
    if (results.length >= maxResults) break;
  }
  return results;
}

export function formatCodeIndex(index: CodeIndex): string {
  const lines = [`## Code Index (${index.total} symbols)`];
  for (const [file, syms] of index.symbols.entries()) {
    lines.push(`\n**${file}**`);
    for (const sym of syms) {
      lines.push(`  - ${sym.kind} ${sym.name} (line ${sym.line})`);
    }
  }
  return lines.join("\n");
}
