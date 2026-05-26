/** Terminal spinner with inline updates via \r.
 * Mode-aware: braille spinner in unicode, ascii art in ascii mode,
 * text label in accessible, silent in minimal.
 */

import process from "node:process";
import { OutputMode } from "../terminal_width.js";

const BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const ASCII_FRAMES = ["|", "/", "-", "\\"];
const ACCESSIBLE_FRAMES = ["[busy]"];

function termWidth(): number {
  return process.stdout.columns || 80;
}

function successIcon(mode: OutputMode): string {
  if (mode === OutputMode.ACCESSIBLE) return "[PASS]";
  if (mode === OutputMode.ASCII) return "[OK]";
  return "✓";
}

function failIcon(mode: OutputMode): string {
  if (mode === OutputMode.ACCESSIBLE) return "[FAIL]";
  if (mode === OutputMode.ASCII) return "[ERR]";
  return "✗";
}

export class Spinner {
  private _stdout: NodeJS.WriteStream;
  private _mode: OutputMode;
  private _active = false;
  private _index = 0;
  private _currentLabel = "";
  private _timer: ReturnType<typeof setInterval> | null = null;
  private _frames: string[];

  constructor(stdout?: NodeJS.WriteStream, mode = OutputMode.UNICODE) {
    this._stdout = stdout ?? process.stdout;
    this._mode = mode;
    if (mode === OutputMode.MINIMAL) {
      this._frames = [""];
    } else if (mode === OutputMode.ACCESSIBLE) {
      this._frames = ACCESSIBLE_FRAMES;
    } else if (mode === OutputMode.ASCII) {
      this._frames = ASCII_FRAMES;
    } else {
      this._frames = BRAILLE_FRAMES;
    }
  }

  start(label: string): void {
    this._active = true;
    this._currentLabel = label;
    this._index = 0;
    this._writeFrame();
    if (this._frames.length > 1) {
      this._timer = setInterval(() => {
        if (!this._active) return;
        this._index = (this._index + 1) % this._frames.length;
        this._writeFrame();
      }, 120);
    }
  }

  update(label: string): void {
    if (!this._active) return;
    this._currentLabel = label;
  }

  succeed(label: string): void {
    this._stop();
    if (this._mode === OutputMode.MINIMAL) return;
    this._writeLine(`\r${successIcon(this._mode)} ${label}\n`);
  }

  fail(label: string): void {
    this._stop();
    if (this._mode === OutputMode.MINIMAL) return;
    this._writeLine(`\r${failIcon(this._mode)} ${label}\n`);
  }

  stop(): void {
    this._stop();
    this._writeLine("\r\u001b[K");
  }

  private _stop(): void {
    this._active = false;
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  private _writeFrame(): void {
    if (!this._active) return;
    const frame = this._frames[this._index];
    const label = this._currentLabel;
    const maxWidth = termWidth();
    const prefix = `${frame} `;
    let maxLabel = maxWidth - prefix.length - 1;
    if (maxLabel < 10) maxLabel = 10;
    let displayLabel = label;
    if (label.length > maxLabel) {
      displayLabel = label.slice(0, maxLabel - 1) + "…";
    }
    this._stdout.write(`\r${prefix}${displayLabel}\u001b[K`);
  }

  private _writeLine(text: string): void {
    this._stdout.write(text);
  }
}
