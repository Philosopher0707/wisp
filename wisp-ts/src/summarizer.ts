/** Extractive summarization for Wisp sessions — no ML model required. */

export interface SessionSummary {
  session_id: string;
  timestamp: string;
  workspace: string;
  summary: string;
  key_decisions: string[];
  user_preferences: string[];
  open_tasks: string[];
  files_touched: string[];
  thread_stack: Array<{ role: string; content: string }>;
}

const _DECISION_PATTERNS = /\b(decided to|decided on|we decided|let's use|going with|chose|will use|settled on|opted for|selected|picked)\b/i;
const _PREFERENCE_PATTERNS = /\b(i prefer|i like|i want|i need|i don't want|please use|always|never|make sure|ensure)\b/i;
const _TASK_PATTERNS = /\b(TODO|FIXME|HACK|next time|later|still need to|pending|not yet|up next|future|plan to|need to|will implement|will add|will fix)\b/i;
const _ACTION_VERBS = ["implemented", "added", "created", "fixed", "refactored", "built", "wrote", "updated", "removed", "completed"];
const _FILE_PATTERN = /[\w\-.\/]+\.(ts|js|tsx|jsx|py|rs|go|java|kt|swift|cpp|c|h|rb|php|md|json|yaml|yml|toml|ini|sh|sql)/gi;
const _SENTENCE_SPLIT = /(?<=[.!?])\s+/;

function _getContent(msg: Record<string, unknown>): string {
  const content = msg.content;
  return typeof content === "string" ? content : "";
}

function _dedupLimit(items: string[], limit: number): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    const normalized = item.toLowerCase().replace(/\s+/g, " ");
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(item);
    if (out.length >= limit) break;
  }
  return out;
}

export function summarizeSession(
  messages: Array<{ role: string; content: string }>,
  sessionId: string,
  workspace: string
): SessionSummary | null {
  if (!messages.length) return null;

  const summary: SessionSummary = {
    session_id: sessionId,
    timestamp: new Date().toISOString(),
    workspace,
    summary: _buildSummary(messages),
    key_decisions: _extractDecisions(messages),
    user_preferences: _extractPreferences(messages),
    open_tasks: _extractTasks(messages),
    files_touched: _extractFiles(messages),
    thread_stack: messages.slice(-5).map((m) => ({ role: m.role, content: m.content.slice(0, 200) })),
  };

  return summary;
}

function _buildSummary(messages: Array<{ role: string; content: string }>): string {
  const assistantContents = messages.filter((m) => m.role === "assistant").map((m) => m.content);
  if (!assistantContents.length) return "";

  const scored: Array<{ sentence: string; score: number; index: number }> = [];
  let idx = 0;
  const total = assistantContents.length;

  for (let msgIdx = 0; msgIdx < assistantContents.length; msgIdx++) {
    const content = assistantContents[msgIdx];
    const sentences = content.split(_SENTENCE_SPLIT);
    for (let sentIdx = 0; sentIdx < sentences.length; sentIdx++) {
      const sentence = sentences[sentIdx].trim();
      if (sentence.length < 10) continue;
      const score = _scoreSentence(sentence, msgIdx, sentIdx, total);
      scored.push({ sentence, score, index: idx });
      idx++;
    }
  }

  if (!scored.length) return "";
  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, 3).sort((a, b) => a.index - b.index);

  const deduped: string[] = [];
  for (const { sentence } of top) {
    const norm = sentence.toLowerCase().replace(/\s+/g, " ");
    if (!deduped.some((d) => d.toLowerCase().replace(/\s+/g, " ") === norm)) {
      deduped.push(sentence);
    }
  }
  return deduped.join(" ");
}

function _scoreSentence(sentence: string, msgIdx: number, sentIdx: number, total: number): number {
  let score = 0;
  const lower = sentence.toLowerCase();

  const positionRatio = (msgIdx + 1) / Math.max(total, 1);
  if (sentIdx === 0 && msgIdx > 0) score += 1.0 * positionRatio;
  if (msgIdx === total - 1) score += 2.0;

  for (const verb of _ACTION_VERBS) {
    if (lower.includes(verb)) { score += 1.5; break; }
  }

  if (/\d/.test(sentence)) score += 0.5;
  if (sentence.length < 20) score -= 1.0;
  else if (sentence.length > 300) score -= 0.5;

  return score;
}

function _extractDecisions(messages: Array<{ role: string; content: string }>): string[] {
  const results: string[] = [];
  for (const m of messages) {
    const content = m.content;
    for (const sentence of content.split(_SENTENCE_SPLIT)) {
      const s = sentence.trim();
      if (_DECISION_PATTERNS.test(s) && s.length > 15) results.push(s);
    }
  }
  return _dedupLimit(results, 5);
}

function _extractPreferences(messages: Array<{ role: string; content: string }>): string[] {
  const results: string[] = [];
  const correctionPattern = /\b(no,|actually,|correction:|wait,|instead,)\b/i;
  for (const m of messages) {
    if (m.role !== "user") continue;
    const content = m.content;
    for (const sentence of content.split(_SENTENCE_SPLIT)) {
      const s = sentence.trim();
      if (_PREFERENCE_PATTERNS.test(s) && s.length > 10) results.push(s);
      if (correctionPattern.test(s)) results.push(s);
    }
  }
  return _dedupLimit(results, 5);
}

function _extractTasks(messages: Array<{ role: string; content: string }>): string[] {
  const results: string[] = [];
  for (const m of messages) {
    const content = m.content;
    for (const sentence of content.split(_SENTENCE_SPLIT)) {
      const s = sentence.trim();
      if (_TASK_PATTERNS.test(s) && s.length > 5) results.push(s);
    }
  }
  return _dedupLimit(results, 5);
}

function _extractFiles(messages: Array<{ role: string; content: string }>): string[] {
  const files = new Set<string>();
  for (const m of messages) {
    const matches = m.content.matchAll(_FILE_PATTERN);
    for (const match of matches) {
      files.add(match[0]);
    }
  }
  return [...files].slice(0, 20);
}
