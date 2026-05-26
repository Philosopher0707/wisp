import fs from "node:fs";
import path from "node:path";

export function toolSearchSymbols(query: string, workspace = ".", maxResults = 20): string {
  const q = query.toLowerCase();
  const results: Array<{ name: string; kind: string; file: string; line: number }> = [];

  function scan(dir: string) {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== "node_modules" && !entry.name.startsWith(".")) {
        scan(full);
      } else if (entry.isFile() && /\.(ts|js|tsx|jsx|py|go|rs)$/.test(entry.name)) {
        const lines = fs.readFileSync(full, "utf-8").split("\n");
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i];
          // Very basic regex-based symbol detection
          const m = line.match(/^(?:export\s+)?(?:function|class|const|let|var|type|interface|enum)\s+([A-Za-z0-9_]+)/);
          if (m && m[1].toLowerCase().includes(q)) {
            results.push({ name: m[1], kind: "symbol", file: path.relative(workspace, full), line: i + 1 });
          }
        }
      }
    }
  }
  try { scan(path.resolve(workspace)); } catch { /* ignore */ }

  const out = results.slice(0, maxResults);
  if (out.length === 0) return `No symbols matching '${query}'.`;
  const lines = out.map((r) => `  ${r.name} in ${r.file}:${r.line}`);
  return `Found ${out.length} symbol(s) matching '${query}':\n\n${lines.join("\n")}`;
}

export function toolSearchCodebase(query: string, topK = 5, workspace = "."): string {
  // Stub: no embedding-based semantic search in pure Node
  const q = query.toLowerCase();
  const results: Array<{ file: string; line: number; content: string }> = [];

  function scan(dir: string) {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== "node_modules" && !entry.name.startsWith(".")) {
        scan(full);
      } else if (entry.isFile() && /\.(ts|js|tsx|jsx|py|md|txt|go|rs)$/.test(entry.name)) {
        const content = fs.readFileSync(full, "utf-8");
        if (content.toLowerCase().includes(q)) {
          const lines = content.split("\n");
          for (let i = 0; i < lines.length; i++) {
            if (lines[i].toLowerCase().includes(q)) {
              results.push({ file: path.relative(workspace, full), line: i + 1, content: lines[i].trim().slice(0, 100) });
              break; // only first match per file
            }
          }
        }
      }
    }
  }
  try { scan(path.resolve(workspace)); } catch { /* ignore */ }

  const out = results.slice(0, topK);
  if (out.length === 0) return `No code found matching '${query}'.`;
  const lines = out.map((r, i) => `${i + 1}. ${r.file}:${r.line}\n   | ${r.content}`);
  return `Text search results for '${query}':\n\n${lines.join("\n")}`;
}
