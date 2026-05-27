/** Compactor — LLM-powered conversation summarization.

Replaces simple truncation with structured summarization that preserves
decisions, errors, and task state. Falls back to truncation if the LLM
summarization fails.
*/

import { Provider } from "../providers/protocol.js";
import { TokenCounter } from "../infra/token_counter.js";

const COMPACTION_SYSTEM_PROMPT = `You are a conversation compressor. Summarize the conversation below.
Preserve ALL of the following:
1. Key decisions made and their rationale
2. Files modified, created, or deleted (with paths)
3. Errors encountered and how they were resolved
4. Current task state and what remains to be done
5. Any important context the assistant will need to continue

Output ONLY the summary. No preamble, no "here is a summary", just the compressed content.
Be thorough but concise — capture everything needed to resume the conversation seamlessly.`;

export interface CompactionResult {
  summary: string;
  decisionsMade: string[];
  filesTouched: string[];
  errorContext: string[];
  tokensBefore: number;
  tokensAfter: number;
  modelUsed: string;
  fallbackTruncation: boolean;
}

export class Compactor {
  provider: Provider;
  tokenCounter: TokenCounter;
  compactionModel: string;

  constructor(
    provider: Provider,
    tokenCounter: TokenCounter,
    compactionModel = ""
  ) {
    this.provider = provider;
    this.tokenCounter = tokenCounter;
    this.compactionModel = compactionModel;
  }

  async compact(
    messages: Array<{ role: string; content: string }>,
    keepRecent = 6
  ): Promise<CompactionResult> {
    const tokensBefore = messages.reduce(
      (sum, m) => sum + this.tokenCounter.count(m.content),
      0
    );

    if (messages.length <= keepRecent) {
      return {
        summary: "",
        decisionsMade: [],
        filesTouched: [],
        errorContext: [],
        tokensBefore,
        tokensAfter: tokensBefore,
        modelUsed: "",
        fallbackTruncation: true,
      };
    }

    const toSummarize = messages.slice(0, -keepRecent);
    const kept = messages.slice(-keepRecent);

    // Try LLM summarization
    try {
      const result = await this._llmSummarize(toSummarize, kept);
      if (result) return result;
    } catch {
      // fall through to truncation
    }

    return this._truncateFallback(toSummarize, kept, tokensBefore);
  }

  private async _llmSummarize(
    toSummarize: Array<{ role: string; content: string }>,
    kept: Array<{ role: string; content: string }>
  ): Promise<CompactionResult | null> {
    const conversationText = this._formatMessages(toSummarize);
    const keptText = this._formatMessages(kept);

    const userPrompt = `Recent context (WILL be preserved — do NOT repeat this):
${keptText}

Messages to compress:
${conversationText}`;

    const summaryParts: string[] = [];

    try {
      for await (const event of this.provider.generateStreamEvents(
        COMPACTION_SYSTEM_PROMPT,
        [{ role: "user", content: userPrompt }],
        null
      )) {
        const ev = event as Record<string, unknown>;
        if (ev.type === "content") {
          summaryParts.push(String(ev.text ?? ""));
        } else if (ev.type === "error") {
          throw new Error(String(ev.message ?? "Compaction failed"));
        }
      }
    } catch {
      return null;
    }

    const summary = summaryParts.join("").trim();
    if (!summary || summary.startsWith("[ERROR")) return null;

    const tokensAfter =
      this.tokenCounter.count(summary) +
      kept.reduce((s, m) => s + this.tokenCounter.count(m.content), 0);

    return {
      summary,
      decisionsMade: this._extractSection(summary, "decisions"),
      filesTouched: this._extractSection(summary, "files"),
      errorContext: this._extractSection(summary, "errors"),
      tokensBefore: toSummarize.reduce((s, m) => s + this.tokenCounter.count(m.content), 0) +
        kept.reduce((s, m) => s + this.tokenCounter.count(m.content), 0),
      tokensAfter,
      modelUsed: this.compactionModel || "default",
      fallbackTruncation: false,
    };
  }

  private _truncateFallback(
    toSummarize: Array<{ role: string; content: string }>,
    kept: Array<{ role: string; content: string }>,
    tokensBefore: number
  ): CompactionResult {
    const truncated = toSummarize.slice(-20);
    const excerpts: string[] = [];
    for (const msg of truncated) {
      let content = msg.content;
      if (content.length > 200) content = content.slice(0, 200) + "...";
      if (content.trim()) excerpts.push(`[${msg.role}]: ${content}`);
    }

    const summary = `[Compacted ${toSummarize.length} messages. Recent excerpts:\n${excerpts.join("\n")}\n]`;

    const tokensAfter =
      this.tokenCounter.count(summary) +
      kept.reduce((s, m) => s + this.tokenCounter.count(m.content), 0);

    return {
      summary,
      decisionsMade: [],
      filesTouched: [],
      errorContext: [],
      tokensBefore,
      tokensAfter,
      modelUsed: "",
      fallbackTruncation: true,
    };
  }

  private _formatMessages(
    messages: Array<{ role: string; content: string }>
  ): string {
    const lines: string[] = [];
    for (const msg of messages) {
      const content = msg.content || "";
      if (content.trim()) lines.push(`[${msg.role}]: ${content}`);
    }
    return lines.join("\n");
  }

  private _extractSection(text: string, label: string): string[] {
    const pattern = new RegExp(
      `${label}[:\\s]*(.*?)(?=\\n\\n|\\n[A-Z]|$)`,
      "is"
    );
    const match = pattern.exec(text);
    if (!match) return [];
    const items = match[1].trim();
    if (!items) return [];
    return items
      .split("\n")
      .map((item) => item.trim().replace(/^[-* ]+/, "").trim())
      .filter(Boolean);
  }
}
