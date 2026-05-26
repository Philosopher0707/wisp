/** CLI rendering utilities — pure functions for formatting terminal output.
 * Mode-aware: unicode, ascii, accessible, minimal.
 */

import { AgentEvent } from "../core/events.js";
import { dim, error, warning, success } from "../colors.js";
import {
  displayWidth,
  wrapTextWide,
  BoxChars,
  OutputMode,
  getOutputMode,
  isAccessible,
} from "../terminal_width.js";

export function formatDuration(durationMs: number | null | undefined): string {
  if (durationMs === null || durationMs === undefined) return "";
  if (durationMs < 1) return `${(durationMs * 1000).toFixed(0)}μs`;
  if (durationMs < 1000) return `${durationMs.toFixed(0)}ms`;
  if (durationMs < 60000) return `${(durationMs / 1000).toFixed(1)}s`;
  const mins = Math.floor(durationMs / 60000);
  const secs = (durationMs % 60000) / 1000;
  return `${mins}m ${secs.toFixed(0)}s`;
}

export function formatArgValue(key: string, value: unknown): string {
  if (["path", "command", "pattern", "filepath"].includes(key)) {
    const s = String(value);
    return s.length > 60 ? s.slice(0, 57) + "..." : s;
  }
  if (["content", "text", "old", "new"].includes(key)) {
    if (typeof value === "string") return `(${value.length} chars)`;
    return String(value).slice(0, 60);
  }
  if (["arguments", "args"].includes(key)) {
    if (value && typeof value === "object" && !Array.isArray(value))
      return `(${Object.keys(value).length} keys)`;
    return String(value).slice(0, 40);
  }
  const s = String(value);
  return s.length > 80 ? s.slice(0, 77) + "..." : s;
}

export function wrapText(text: string, width: number, indent = ""): string[] {
  return wrapTextWide(text, width, indent);
}

export function renderToolCall(name: string, args: Record<string, unknown>): string {
  const box = new BoxChars();
  let lines: string[];
  if (box.mode === OutputMode.ACCESSIBLE) {
    lines = [dim(`  [TOOL] ${name}`)];
  } else if (box.mode === OutputMode.MINIMAL) {
    lines = [`  tool: ${name}`];
  } else {
    lines = [dim(`  🔧 ${name}`)];
  }
  if (args) {
    for (const [key, value] of Object.entries(args)) {
      const valStr = formatArgValue(key, value);
      lines.push(dim(`  │  ${key}: ${valStr}`));
    }
  }
  return lines.join("\n");
}

export function renderPhaseBar(phase: string, width: number): string {
  const phases = ["understand", "plan", "execute", "verify"];
  const idx = phases.indexOf(phase);
  const mode = getOutputMode();
  if (mode === OutputMode.MINIMAL) return `[${phase}]`;

  const parts: string[] = [];
  for (let i = 0; i < phases.length; i++) {
    const label = phases[i];
    if (i === idx) {
      parts.push(`[${label}]`);
    } else if (i < idx) {
      parts.push(mode === OutputMode.ACCESSIBLE ? `(${label})` : `✓ ${label}`);
    } else {
      parts.push(mode === OutputMode.ACCESSIBLE ? `  ${label}  ` : `○ ${label}`);
    }
  }
  const line = parts.join("  ");
  const pad = " ".repeat(Math.max(0, width - displayWidth(line)));
  return dim(line + pad);
}

export function renderTurnStats(
  stats: { turnNumber: number; phase: string; toolsRun: number; toolsSucceeded: number; toolsFailed: number; elapsed: number },
  width: number
): string {
  const mode = getOutputMode();
  if (mode === OutputMode.MINIMAL) {
    return `turn ${stats.turnNumber} | ${stats.toolsRun} tools | ${stats.elapsed.toFixed(1)}s`;
  }
  const parts: string[] = [];
  parts.push(`Turn ${stats.turnNumber}`);
  parts.push(`${stats.toolsRun} tool${stats.toolsRun === 1 ? "" : "s"}`);
  if (stats.toolsSucceeded > 0) parts.push(`${stats.toolsSucceeded} ok`);
  if (stats.toolsFailed > 0) parts.push(`${stats.toolsFailed} fail`);
  parts.push(`${stats.elapsed.toFixed(1)}s`);
  const line = parts.join(" · ");
  const pad = " ".repeat(Math.max(0, width - displayWidth(line)));
  return dim(line + pad);
}

export function renderFileTicker(files: string[], width: number): string {
  if (!files.length) return "";
  const mode = getOutputMode();
  const prefix = mode === OutputMode.ACCESSIBLE ? "Files: " : "📄 ";
  const names = files.join(", ");
  const line = prefix + names;
  if (displayWidth(line) > width) {
    return dim(line.slice(0, width - 3) + "...");
  }
  return dim(line);
}

export function renderThinkingBlock(text: string, boxMode: boolean, width: number): string | null {
  if (!text.trim()) return null;
  const innerW = width - 4;
  const wrapped = wrapText(text.trim(), innerW);

  if (isAccessible()) {
    const header = _rule("─", "Reasoning:", width);
    const body = wrapped.map((line) => dim(`  ${line}`)).join("\n");
    return `${header}\n${body}`;
  }

  const header = _rule("·", "🧠 Reasoning", width);
  const body = wrapped.map((line) => dim(`  ${line}`)).join("\n");
  return `${header}\n${body}`;
}

export function renderContentBlock(text: string, boxMode: boolean, width: number): string | null {
  if (!text.trim()) return null;
  const innerW = width - 4;
  const wrapped = wrapText(text.trim(), innerW);
  if (boxMode) {
    if (isAccessible()) {
      return "[Response]\n" + wrapped.join("\n");
    }
    const header = _rule("─", "Response", width);
    return `${header}\n` + wrapped.join("\n");
  }
  return wrapped.join("\n");
}

export function renderDoneReason(event: AgentEvent, iterations: number): string | null {
  const reason = typeof event.data.reason === "string" ? event.data.reason : "";
  if (isAccessible()) {
    if (reason === "max_iterations") {
      return warning(`\n  [WARNING] Max iterations (${iterations}) reached.`);
    }
    if (reason === "max_reflections") {
      return warning(`\n  [REFLECT] Reflective loop detected after ${iterations} iterations.`);
    }
    if (reason === "interrupted") return dim("\n  [INTERRUPTED]");
    if (reason === "error") return error("\n  [ERROR] Stream error — turn aborted.");
    return null;
  }
  if (reason === "max_iterations") {
    return warning(`\n  ⚠️  Max iterations (${iterations}) reached.`);
  }
  if (reason === "max_reflections") {
    return warning(`\n  🔄  Reflective loop detected after ${iterations} iterations.`);
  }
  if (reason === "interrupted") return dim("\n  ⏹  Interrupted.");
  if (reason === "error") return error("\n  ✗ Stream error — turn aborted.");
  return null;
}

function _rule(char: string, title: string, width: number): string {
  const titleText = ` ${title} `;
  const titleWidth = displayWidth(titleText);
  const available = width - 2;
  if (titleWidth > available) return titleText;
  const left = Math.floor((available - titleWidth) / 2);
  const right = available - titleWidth - left;
  return dim(char.repeat(left) + titleText + char.repeat(right));
}

export function _box(
  content: string,
  title = "",
  style: "dim" | "error" | "success" | "muted" = "dim",
  width = 80
): string {
  const box = new BoxChars();
  const mode = box.mode;

  if (mode === OutputMode.MINIMAL) {
    return title ? `[${title}]\n${content}` : content;
  }

  const innerWidth = width - 4;
  const styleFn = { dim, error, success, muted: dim }[style] ?? dim;

  let top: string;
  if (title) {
    if (mode === OutputMode.ACCESSIBLE) {
      const titleText = `[ ${title} ]`;
      top = styleFn(titleText + "-".repeat(Math.max(0, width - displayWidth(titleText))));
    } else {
      const titleText = ` ${title} `;
      const tw = displayWidth(titleText);
      const avail = width - 2;
      const left = Math.floor((avail - tw) / 2);
      const right = avail - tw - left;
      top = styleFn(box.tl + box.hz.repeat(left) + titleText + box.hz.repeat(right) + box.tr);
    }
  } else {
    top = styleFn(box.top(width));
  }

  const lines = content.split("\n").map((line) => {
    const cw = displayWidth(line);
    if (cw > innerWidth) {
      let truncated = "";
      let cur = 0;
      for (const ch of line) {
        const w = displayWidth(ch);
        if (cur + w > innerWidth) break;
        truncated += ch;
        cur += w;
      }
      line = truncated;
    }
    const pad = " ".repeat(innerWidth - displayWidth(line));
    return box.vt + " " + line + pad + " " + box.vt;
  });

  const bottom = styleFn(box.bottom(width));
  return [top, ...lines, bottom].join("\n");
}
