/** Minimal ANSI color support for Wisp terminal output.
 * Zero dependencies. Respects the NO_COLOR environment variable.
 * https://no-color.org/
 */

import process from "node:process";

// ── Color mode detection ─────────────────────────────────────────

const HIGH_CONTRAST = process.env.WISP_HIGH_CONTRAST !== undefined;

function isDisabled(): boolean {
  return (
    process.env.NO_COLOR !== undefined ||
    process.env.WISP_NO_COLOR !== undefined ||
    !process.stdout.isTTY
  );
}

class Style {
  constructor(private code: string) {}

  apply(text: string): string {
    if (isDisabled() || !text) return text;
    return `\u001b[${this.code}m${text}\u001b[0m`;
  }

  raw(text: string): string {
    return `\u001b[${this.code}m${text}\u001b[0m`;
  }
}

function makeStyle(code: string) {
  const style = new Style(code);
  return (text: string): string => style.apply(text);
}

// ── Semantic palette ─────────────────────────────────────────────

// High-contrast (colorblind-safe): blue/yellow instead of red/green
let success: (text: string) => string;
let error: (text: string) => string;
let warning: (text: string) => string;
let info: (text: string) => string;
let dim: (text: string) => string;
let bold: (text: string) => string;
let accent: (text: string) => string;
let muted: (text: string) => string;
let border: (text: string) => string;
let highlight: (text: string) => string;

if (HIGH_CONTRAST) {
  success = makeStyle("34;1");   // bright blue
  error = makeStyle("31;1");     // bold red
  warning = makeStyle("93");     // bright yellow
  info = makeStyle("96");        // bright cyan
  dim = makeStyle("90");         // bright black
  bold = makeStyle("1");         // bold
  accent = makeStyle("95");      // bright magenta
  muted = makeStyle("37");       // default foreground
  border = makeStyle("94");      // blue for borders
  highlight = makeStyle("97");   // bright white
} else {
  success = makeStyle("32");     // green
  error = makeStyle("31");       // red
  warning = makeStyle("33");     // yellow
  info = makeStyle("36");        // cyan
  dim = makeStyle("90");         // bright black
  bold = makeStyle("1");         // bold
  accent = makeStyle("35");      // magenta
  muted = makeStyle("37");       // default foreground
  border = makeStyle("90");      // same as dim
  highlight = makeStyle("97");   // bright white
}

// ── Helpers ──────────────────────────────────────────────────────────

export function stripAnsi(text: string): string {
  return text.replace(/\u001b\[[0-9;]*m/g, "");
}

export function isEnabled(): boolean {
  return !isDisabled();
}

export function isHighContrast(): boolean {
  return HIGH_CONTRAST;
}

export { success, error, warning, info, dim, bold, accent, muted, border, highlight };
