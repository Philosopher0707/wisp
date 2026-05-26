/** Progress tracking for agent turns — pure data, no I/O.
 * Tracks phase transitions, tool execution counts, file changes,
 * and turn-level statistics for CLI rendering.
 */

import { AgentEvent } from "../core/events.js";

export const UNDERSTAND = "understand";
export const PLAN = "plan";
export const EXECUTE = "execute";
export const VERIFY = "verify";

const PHASE_ORDER: Record<string, number> = {
  [UNDERSTAND]: 0,
  [PLAN]: 1,
  [EXECUTE]: 2,
  [VERIFY]: 3,
};

const WRITE_TOOLS = new Set(["write_file", "edit_file", "edit_file_multi"]);
const EXECUTE_TOOLS = new Set([...WRITE_TOOLS, "run_bash"]);
const VERIFY_TOOLS = new Set(["run_tests", "lsp_diagnostics", "diagnose"]);
const PLAN_TOOLS = new Set(["plan_task"]);

export interface TurnProgressSnapshot {
  turnNumber: number;
  phase: string;
  toolsRun: number;
  toolsSucceeded: number;
  toolsFailed: number;
  filesChanged: string[];
  elapsed: number;
}

export class TurnProgress {
  turnNumber = 0;
  phase = UNDERSTAND;
  toolsRun = 0;
  toolsSucceeded = 0;
  toolsFailed = 0;
  filesChanged: string[] = [];
  startTime = 0;
  currentTool: string | null = null;
  currentToolArgs: Record<string, unknown> = {};
  currentToolStart = 0;
}

export class ProgressTracker {
  progress = new TurnProgress();
  private _hasWritten = false;
  private _seenFiles = new Set<string>();

  startTurn(turnNumber: number): void {
    this.progress = new TurnProgress();
    this.progress.turnNumber = turnNumber;
    this.progress.startTime = performance.now();
    this._hasWritten = false;
    this._seenFiles.clear();
  }

  get elapsed(): number {
    if (this.progress.startTime === 0) return 0;
    return (performance.now() - this.progress.startTime) / 1000;
  }

  onDone(): TurnProgressSnapshot {
    const p = this.progress;
    return {
      turnNumber: p.turnNumber,
      phase: p.phase,
      toolsRun: p.toolsRun,
      toolsSucceeded: p.toolsSucceeded,
      toolsFailed: p.toolsFailed,
      filesChanged: [...p.filesChanged],
      elapsed: this.elapsed,
    };
  }

  onEvent(event: AgentEvent): string | null {
    const etype = typeof event.type === "string" ? event.type : String(event.type);

    if (etype === "tool_call") {
      const name = typeof event.data.name === "string" ? event.data.name : "";
      const args = (event.data.arguments as Record<string, unknown>) ?? {};
      this.onToolCall(name, args);
      return this._maybeAdvancePhase(name);
    }

    if (etype === "tool_result") {
      const name = typeof event.data.name === "string" ? event.data.name : "";
      const result = event.data.result;
      const durationMs = typeof event.data.duration_ms === "number" ? event.data.duration_ms : undefined;
      this.onToolResult(name, result, durationMs);
      return null;
    }

    if (etype === "thinking") {
      const text = typeof event.data.text === "string" ? event.data.text : "";
      if (text.length > 500 && this.progress.phase === UNDERSTAND) {
        this.progress.phase = PLAN;
        return PLAN;
      }
      return null;
    }

    return null;
  }

  onToolCall(name: string, args?: Record<string, unknown>): void {
    this.progress.toolsRun += 1;
    this.progress.currentTool = name;
    this.progress.currentToolArgs = args ?? {};
    this.progress.currentToolStart = performance.now();
  }

  onToolResult(name: string, result: unknown, _durationMs?: number): void {
    this.progress.currentTool = null;
    this._classifyResult(result);
    this._trackFiles(name, this.progress.currentToolArgs);
    this.progress.currentToolArgs = {};
  }

  private _maybeAdvancePhase(toolName: string): string | null {
    const current = PHASE_ORDER[this.progress.phase] ?? 0;

    if (PLAN_TOOLS.has(toolName) && current < PHASE_ORDER[PLAN]) {
      this.progress.phase = PLAN;
      return PLAN;
    }

    if (EXECUTE_TOOLS.has(toolName)) {
      this._hasWritten = true;
      if (current < PHASE_ORDER[EXECUTE]) {
        this.progress.phase = EXECUTE;
      }
    }

    if (VERIFY_TOOLS.has(toolName) && current < PHASE_ORDER[VERIFY]) {
      this.progress.phase = VERIFY;
      return VERIFY;
    }

    return null;
  }

  private _classifyResult(result: unknown): void {
    if (
      result &&
      typeof result === "object" &&
      (result as Record<string, unknown>).status === "error"
    ) {
      this.progress.toolsFailed += 1;
    } else {
      this.progress.toolsSucceeded += 1;
    }
  }

  private _trackFiles(toolName: string, args: Record<string, unknown>): void {
    if (!WRITE_TOOLS.has(toolName)) return;
    const pathArg = args.path ?? args.filepath ?? args.file;
    if (typeof pathArg === "string") {
      if (!this._seenFiles.has(pathArg)) {
        this._seenFiles.add(pathArg);
        this.progress.filesChanged.push(pathArg);
      }
    }
  }
}
