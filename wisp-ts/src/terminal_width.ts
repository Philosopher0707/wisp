/** Width-aware terminal rendering with ASCII fallback and accessible modes. */

import process from "node:process";
import { TextDecoder } from "node:util";

export enum OutputMode {
  UNICODE = "unicode",
  ASCII = "ascii",
  ACCESSIBLE = "accessible",
  MINIMAL = "minimal",
}

// ── Mode detection ───────────────────────────────────────────────

let OUTPUT_MODE: OutputMode = detectMode();

function detectMode(): OutputMode {
  if (process.env.WISP_ACCESSIBLE || process.env.ACCESSIBLE) return OutputMode.ACCESSIBLE;
  const explicit = (process.env.WISP_OUTPUT_MODE || "").toLowerCase();
  if (["ascii", "a"].includes(explicit)) return OutputMode.ASCII;
  if (["accessible", "acc", "screen-reader", "a11y"].includes(explicit)) return OutputMode.ACCESSIBLE;
  if (["minimal", "min", "plain", "raw"].includes(explicit)) return OutputMode.MINIMAL;
  if (["unicode", "fancy", "full"].includes(explicit)) return OutputMode.UNICODE;
  if (process.env.NO_COLOR || !process.stdout.isTTY) return OutputMode.ASCII;
  const term = process.env.TERM || "";
  if (term.includes("dumb") || term.toLowerCase().includes("vt100")) return OutputMode.ASCII;
  return OutputMode.UNICODE;
}

export function getOutputMode(): OutputMode {
  return OUTPUT_MODE;
}

export function setOutputMode(mode: string | OutputMode): void {
  if (typeof mode === "string") {
    const map: Record<string, OutputMode> = {
      unicode: OutputMode.UNICODE, fancy: OutputMode.UNICODE,
      ascii: OutputMode.ASCII, a: OutputMode.ASCII,
      accessible: OutputMode.ACCESSIBLE, acc: OutputMode.ACCESSIBLE,
      a11y: OutputMode.ACCESSIBLE, "screen-reader": OutputMode.ACCESSIBLE,
      minimal: OutputMode.MINIMAL, min: OutputMode.MINIMAL,
      plain: OutputMode.MINIMAL, raw: OutputMode.MINIMAL,
    };
    OUTPUT_MODE = map[mode.toLowerCase()] ?? OutputMode.UNICODE;
  } else {
    OUTPUT_MODE = mode;
  }
}

export function isHighContrast(): boolean {
  return process.env.WISP_HIGH_CONTRAST !== undefined;
}

export function isAccessible(): boolean {
  return OUTPUT_MODE === OutputMode.ACCESSIBLE || isHighContrast();
}

// ── Display width ──────────────────────────────────────────────────

export function displayWidth(text: string): number {
  if (!text) return 0;
  const plain = stripAnsi(text);
  let total = 0;
  for (const ch of plain) {
    total += charWidth(ch.codePointAt(0) ?? 0);
  }
  return total;
}

function charWidth(cp: number): number {
  if (cp === 0x00AD) return 0; // soft hyphen
  if (cp < 0x1100) return 1;
  if (cp >= 0x1100 && cp <= 0x115F) return 2;
  if (cp >= 0x2E80 && cp <= 0x9FFF) return 2;
  if (cp >= 0xA960 && cp <= 0xA97F) return 2;
  if (cp >= 0xAC00 && cp <= 0xD7AF) return 2;
  if (cp >= 0xD7B0 && cp <= 0xD7FF) return 2;
  if (cp >= 0xF900 && cp <= 0xFAFF) return 2;
  if (cp >= 0xFE10 && cp <= 0xFE19) return 2;
  if (cp >= 0xFE30 && cp <= 0xFE6F) return 2;
  if (cp >= 0xFF00 && cp <= 0xFF60) return 2;
  if (cp >= 0xFFE0 && cp <= 0xFFE6) return 2;
  if (cp >= 0x1F300 && cp <= 0x1F64F) return 2;
  if (cp >= 0x1F900 && cp <= 0x1F9FF) return 2;
  if (cp >= 0x20000 && cp <= 0x2A6DF) return 2;
  if (cp >= 0x2A700 && cp <= 0x2B73F) return 2;
  if (cp >= 0x2B740 && cp <= 0x2B81F) return 2;
  if (cp >= 0x2B820 && cp <= 0x2CEAF) return 2;
  if (cp >= 0x2CEB0 && cp <= 0x2EBEF) return 2;
  if (cp >= 0x30000 && cp <= 0x3134F) return 2;
  if (cp >= 0x0300 && cp <= 0x036F) return 0; // combining diacriticals
  return 1;
}

export function stripAnsi(text: string): string {
  return text.replace(/\u001b\[[0-9;]*m/g, "");
}

// ── Width-aware text wrapping ─────────────────────────────────────

export function wrapTextWide(text: string, width: number, indent = ""): string[] {
  if (!text) return [""];
  const lines: string[] = [];
  for (const paragraph of text.split("\n")) {
    if (!paragraph.trim()) {
      lines.push("");
      continue;
    }
    if (displayWidth(paragraph) <= width) {
      lines.push(paragraph);
      continue;
    }
    const words = paragraph.split(" ");
    let current = "";
    let currentWidth = 0;
    let first = true;
    for (const word of words) {
      const w = displayWidth(word);
      if (w > width) {
        _breakLongWord(lines, current, word, width, indent, first);
        current = "";
        currentWidth = 0;
        first = false;
        continue;
      }
      const space = current ? 1 : 0;
      const newWidth = currentWidth + space + w;
      if (newWidth <= width) {
        current = current ? current + " " + word : word;
        currentWidth = newWidth;
      } else {
        if (first) {
          lines.push(current);
          first = false;
        } else {
          lines.push(indent + current);
        }
        current = word;
        currentWidth = w;
      }
    }
    if (current) {
      if (first) {
        lines.push(current);
      } else {
        lines.push(indent + current);
      }
    }
  }
  return lines;
}

function _breakLongWord(lines: string[], prefix: string, word: string, width: number, indent: string, isFirst: boolean): void {
  let current = prefix;
  let currentWidth = prefix ? displayWidth(prefix) : 0;
  for (const ch of word) {
    const cw = displayWidth(ch);
    if (current && currentWidth + cw > width) {
      if (isFirst && !lines.length) {
        lines.push(current);
        isFirst = false;
      } else {
        lines.push(indent + current);
      }
      current = ch;
      currentWidth = cw;
    } else {
      current += ch;
      currentWidth += cw;
    }
  }
  if (current) {
    if (isFirst && !lines.length) {
      lines.push(current);
    } else {
      lines.push(indent + current);
    }
  }
}

// ── Width-aware padding ─────────────────────────────────────────────

export function padRight(text: string, targetWidth: number, fill = " "): string {
  const current = displayWidth(text);
  if (current >= targetWidth) return text;
  const padWidth = displayWidth(fill);
  if (padWidth === 0) return text;
  const count = Math.floor((targetWidth - current) / padWidth);
  const remainder = (targetWidth - current) % padWidth;
  return text + fill.repeat(count) + " ".repeat(remainder);
}

export function padLeft(text: string, targetWidth: number, fill = " "): string {
  const current = displayWidth(text);
  if (current >= targetWidth) return text;
  const padWidth = displayWidth(fill);
  if (padWidth === 0) return text;
  const count = Math.floor((targetWidth - current) / padWidth);
  const remainder = (targetWidth - current) % padWidth;
  return fill.repeat(count) + " ".repeat(remainder) + text;
}

export function center(text: string, targetWidth: number, fill = " "): string {
  const current = displayWidth(text);
  if (current >= targetWidth) return text;
  const padWidth = displayWidth(fill);
  if (padWidth === 0) return text;
  const left = Math.floor((targetWidth - current) / 2);
  const right = targetWidth - current - left;
  const leftCount = Math.floor(left / padWidth);
  const rightCount = Math.floor(right / padWidth);
  return fill.repeat(leftCount) + text + fill.repeat(rightCount);
}

// ── Box-drawing characters per mode ──────────────────────────────

export class BoxChars {
  mode: OutputMode;
  private _chars: Record<string, string>;

  constructor(mode?: OutputMode) {
    this.mode = mode ?? OUTPUT_MODE;
    this._chars = this._getChars();
  }

  private _getChars(): Record<string, string> {
    switch (this.mode) {
      case OutputMode.ASCII:
        return { tl: "+", tr: "+", bl: "+", br: "+", hz: "-", vt: "|", rule_h: "-", rule_v: "|" };
      case OutputMode.MINIMAL:
        return { tl: "", tr: "", bl: "", br: "", hz: "", vt: "", rule_h: "=", rule_v: "" };
      case OutputMode.ACCESSIBLE:
        return { tl: "[", tr: "]", bl: "[", br: "]", hz: "-", vt: "|", rule_h: "-", rule_v: "|" };
      default:
        return { tl: "┌", tr: "┐", bl: "└", br: "┘", hz: "─", vt: "│", rule_h: "─", rule_v: "│" };
    }
  }

  get tl(): string { return this._chars.tl; }
  get tr(): string { return this._chars.tr; }
  get bl(): string { return this._chars.bl; }
  get br(): string { return this._chars.br; }
  get hz(): string { return this._chars.hz; }
  get vt(): string { return this._chars.vt; }
  get rule_h(): string { return this._chars.rule_h; }
  get rule_v(): string { return this._chars.rule_v; }

  horizontal(width: number): string {
    if (this.mode === OutputMode.MINIMAL) return "";
    return this.hz.repeat(width);
  }

  top(width: number, title = ""): string {
    if (this.mode === OutputMode.MINIMAL) return title ? `[${title}]` : "";
    const hz = this.hz;
    const available = width - 2;
    if (title) {
      const titleText = ` ${title} `;
      const titleWidth = displayWidth(titleText);
      if (titleWidth > available) {
        // truncate
      }
      const left = Math.floor((available - titleWidth) / 2);
      const right = available - titleWidth - left;
      return this.tl + hz.repeat(left) + titleText + hz.repeat(right) + this.tr;
    }
    return this.tl + hz.repeat(available) + this.tr;
  }

  bottom(width: number): string {
    if (this.mode === OutputMode.MINIMAL) return "";
    return this.bl + this.hz.repeat(width - 2) + this.br;
  }

  line(width: number, content = ""): string {
    if (this.mode === OutputMode.MINIMAL) return content;
    const innerWidth = width - 4; // 2 borders + 2 spaces
    const contentWidth = displayWidth(content);
    if (contentWidth > innerWidth) {
      let truncated = "";
      let currentW = 0;
      for (const ch of content) {
        const cw = displayWidth(ch);
        if (currentW + cw > innerWidth) break;
        truncated += ch;
        currentW += cw;
      }
      content = truncated;
    }
    const padding = " ".repeat(innerWidth - displayWidth(content));
    return this.vt + " " + content + padding + " " + this.vt;
  }
}

// ── Text helpers ─────────────────────────────────────────────────────

export function truncate(text: string, maxWidth: number, suffix = "..."): string {
  const w = displayWidth(text);
  if (w <= maxWidth) return text;
  const suffixW = displayWidth(suffix);
  const available = maxWidth - suffixW;
  let result = "";
  let currentW = 0;
  for (const ch of text) {
    const cw = displayWidth(ch);
    if (currentW + cw > available) return result + suffix;
    result += ch;
    currentW += cw;
  }
  return result;
}

export function truncateNoEllipsis(text: string, maxWidth: number): string {
  const w = displayWidth(text);
  if (w <= maxWidth) return text;
  let result = "";
  let currentW = 0;
  for (const ch of text) {
    const cw = displayWidth(ch);
    if (currentW + cw > maxWidth) return result;
    result += ch;
    currentW += cw;
  }
  return result;
}
