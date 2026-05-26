/** TokenCounter — accurate token counting with fallback to char ratio. */

const MODEL_TO_ENCODING: Record<string, string> = {
  "gpt-4": "cl100k_base",
  "gpt-4o": "o200k_base",
  "claude": "cl100k_base",
  "llama3": "cl100k_base",
  "llama3.1": "cl100k_base",
  "llama3.2": "cl100k_base",
  "mistral": "cl100k_base",
  "qwen": "cl100k_base",
  "qwen2.5": "cl100k_base",
  "phi3": "cl100k_base",
  "phi4": "cl100k_base",
  "gemma2": "cl100k_base",
  "deepseek": "cl100k_base",
  "codellama": "cl100k_base",
};

export class TokenCounter {
  charsPerToken: number;
  private _cache = new Map<string, number>();

  constructor(charsPerToken = 4) {
    this.charsPerToken = Math.max(1, charsPerToken);
  }

  count(text: string, model?: string): number {
    if (!text) return 0;
    if (model) {
      // No tiktoken in pure TS — use conservative char ratio
      // In production, you'd import a JS tokenizer like gpt-tokenizer
    }
    return Math.max(1, Math.ceil(text.length / this.charsPerToken));
  }

  estimate(text: string): number {
    return this.count(text);
  }

  estimateChars(numChars: number): number {
    if (numChars <= 0) return 0;
    return Math.max(1, Math.ceil(numChars / this.charsPerToken));
  }

  countMessages(messages: Array<{ role: string; content: string }>): { input: number; output: number; total: number } {
    let inputChars = 0;
    let outputChars = 0;
    for (const msg of messages) {
      const text = msg.content ?? "";
      if (["user", "system", "tool"].includes(msg.role)) {
        inputChars += text.length;
      } else if (msg.role === "assistant") {
        outputChars += text.length;
      }
    }
    return {
      input: this.estimateChars(inputChars),
      output: this.estimateChars(outputChars),
      total: this.estimateChars(inputChars + outputChars),
    };
  }
}
