import { stripAnsi } from "../terminal_width.js";

const _facts = new Map<string, string[]>();

export function toolRemember(fact: string, workspace?: string): string {
  const key = workspace ?? "global";
  const trimmed = stripAnsi(fact.trim());
  if (trimmed.length > 500) return "⚠ Fact too long (max 500 chars).";
  if (!trimmed) return "⚠ Fact is empty.";
  const arr = _facts.get(key) ?? [];
  if (arr.includes(trimmed)) return `(Already remembered: ${trimmed})`;
  arr.push(trimmed);
  _facts.set(key, arr);
  return `✓ Remembered: ${trimmed}`;
}

export function toolRecall(query: string, workspace?: string, limit = 10): string {
  const key = workspace ?? "global";
  const allFacts = _facts.get(key) ?? [];
  const qLower = query.toLowerCase();
  const qWords = qLower.split(/\s+/).filter((w) => w.length > 2);

  const results: Array<{ score: number; text: string }> = [];
  for (const fact of allFacts) {
    let score = 0;
    const fLower = fact.toLowerCase();
    if (fLower.includes(qLower)) score += 5;
    for (const w of qWords) {
      if (fLower.includes(w)) score += 1;
    }
    if (score > 0) results.push({ score, text: fact });
  }

  results.sort((a, b) => b.score - a.score);
  const deduped = results.slice(0, limit);
  if (deduped.length === 0) return "No relevant memories found for this query.";
  return deduped.map((r) => `(${r.score}) ${r.text}`).join("\n");
}

export function listAllFacts(): Array<{ workspace: string; content: string }> {
  const out: Array<{ workspace: string; content: string }> = [];
  for (const [ws, facts] of _facts.entries()) {
    for (const f of facts) out.push({ workspace: ws, content: f });
  }
  return out;
}
