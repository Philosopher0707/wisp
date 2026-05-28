/** CLI transport layer with structured output. */

import process from "node:process";
import readline from "node:readline";
import { Transport, TransportEvent } from "./base.js";
import { AgentEvent, EventType } from "../core/events.js";
import { WispConfig } from "../config.js";
import { ProgressTracker } from "./progress.js";
import { Spinner } from "./spinner.js";
import {
  renderToolCall,
  renderPhaseBar,
  renderTurnStats,
  renderFileTicker,
  renderThinkingBlock,
  renderContentBlock,
} from "./renderer.js";
import { success, error, warning, info, dim } from "../colors.js";
import { getOutputMode, displayWidth, isAccessible, BoxChars } from "../terminal_width.js";
import { AgentRuntime } from "../core/runtime.js";
import { Session } from "../core/session.js";
import { dispatch, AgentAdapter } from "../commands.js";
import { StdinQueue } from "./queue.js";

function termWidth(): number {
  return process.stdout.columns || 80;
}

function argsPreview(args: Record<string, unknown>): string {
  if (!args || Object.keys(args).length === 0) return "...";
  if (args.path) return String(args.path);
  if (args.command) return String(args.command);
  if (args.content) {
    const c = String(args.content);
    return c.length > 40 ? c.slice(0, 37) + "..." : c;
  }
  const [k, v] = Object.entries(args)[0];
  const sv = String(v);
  return sv.length > 40 ? `${k}=${sv.slice(0, 37)}...` : `${k}=${sv}`;
}

const FULL_OUTPUT_TOOLS = new Set([
  "run_bash",
  "git_status",
  "git_diff",
  "lsp_diagnostics",
  "diagnose",
  "run_tests",
  "search_codebase",
  "search_symbols",
]);

export class CLITransport implements Transport {
  runtime: AgentRuntime;
  config: WispConfig;
  private _thinkingBuffer: string[] = [];
  private _contentBuffer: string[] = [];
  private _inThinking = false;
  private _inContent = false;
  private _progress = new ProgressTracker();
  private _spinner: Spinner | null = null;
  private _turnNumber = 0;
  private _phase = "understand";
  private _interrupted = false;
  private _approvalState = { allowedTools: new Set<string>(), deniedTools: new Set<string>(), autoMode: false, blockMode: false };
  private _sigintHandler: (() => void) | null = null;
  private _stdinQueue = new StdinQueue();
  private _readerActive = false;
  private _stdinRl: readline.Interface | null = null;
  showThinking: boolean;
  showToolOutput: boolean;

  constructor(runtime: AgentRuntime, config: WispConfig) {
    this.runtime = runtime;
    this.config = config;
    this.showThinking = config.show_thinking;
    this.showToolOutput = config.show_tool_output;
  }

  async send(event: TransportEvent): Promise<void> {
    this._renderEvent(process.stdout, event as Record<string, unknown>);
  }

  async recv(): Promise<string | null> {
    process.stdout.write(dim("wisp> "));
    return this._stdinQueue.next();
  }

  async approve(toolCall: Record<string, unknown>): Promise<boolean> {
    const name = String(toolCall.name ?? "unknown");
    const args = (toolCall.arguments as Record<string, unknown>) ?? {};
    const argsText = argsPreview(args);

    // Check session-level policy
    if (this._approvalState.blockMode) return false;
    if (this._approvalState.autoMode) return true;
    if (this._approvalState.allowedTools.has(name)) return true;
    if (this._approvalState.deniedTools.has(name)) return false;

    // Non-interactive / piped = auto-deny unless auto
    if (!process.stdin.isTTY) return false;

    this._spinner?.stop();
    process.stdout.write("\n");
    process.stdout.write(warning(`⚠️  ${name}(${argsText})`) + "\n");
    process.stdout.write(
      dim("     [y] yes  [Y] always this  [a] all on  [n] no  [N] always no  [d] all off  [c] cancel") + "\n"
    );

    // Pause background stdin reader so it doesn't steal approval input
    this._stdinRl?.close();
    this._stdinRl = null;
    this._readerActive = false;

    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    return new Promise((resolve) => {
      rl.question(dim("Approve? "), (raw) => {
        rl.close();
        const choice = raw.trim();
        let result = false;
        if (choice === "y") {
          result = true;
        } else if (choice === "Y") {
          this._approvalState.allowedTools.add(name);
          result = true;
        } else if (choice === "a") {
          this._approvalState.autoMode = true;
          result = true;
        } else if (choice === "n") {
          result = false;
        } else if (choice === "N") {
          this._approvalState.deniedTools.add(name);
          result = false;
        } else if (choice === "d") {
          this._approvalState.blockMode = true;
          result = false;
        }
        if (result) {
          this._getSpinner().start(`${name} ${argsText}`);
        }
        // Resume background reader
        this._startStdinReader();
        resolve(result);
      });
    });
  }

  start(): void {
    this._interrupted = false;
    this._sigintHandler = () => {
      this._interrupted = true;
      this._spinner?.stop();
      process.stdout.write(error("\n\n⏹  Interrupted. Finishing current step... (Ctrl+C again to force quit)\n"));
      // Remove ourselves and install a one-shot force-quit handler
      if (this._sigintHandler) {
        process.removeListener("SIGINT", this._sigintHandler);
      }
      process.once("SIGINT", () => process.exit(130));
    };
    process.on("SIGINT", this._sigintHandler);
    this._startStdinReader();
  }

  stop(): void {
    if (this._sigintHandler) {
      process.removeListener("SIGINT", this._sigintHandler);
      this._sigintHandler = null;
    }
    this._stdinQueue.close();
    this._readerActive = false;
    this._stdinRl?.close();
    this._stdinRl = null;
  }

  isInterrupted(): boolean {
    return this._interrupted;
  }

  resetBuffers(): void {
    this._thinkingBuffer = [];
    this._contentBuffer = [];
    this._inThinking = false;
    this._inContent = false;
    this._spinner?.stop();
    this._interrupted = false;
    this._turnNumber += 1;
    this._progress.startTurn(this._turnNumber);
    this._phase = "understand";
  }

  printBanner(session: Session, model: string, skill?: string): void {
    const width = Math.min(72, termWidth() - 4);
    const sid = session.sessionId;
    const ws = session.workspace;
    const msgCount = session.messages.length;
    const lines = [
      `  Model:      ${model}`,
      `  Session:    ${sid}`,
      `  Workspace:  ${ws}`,
    ];
    if (msgCount) lines.push(`  History:    ${msgCount} messages`);
    if (skill) lines.push(`  Skill:      ${skill}`);
    lines.push("");
    lines.push("  /help for commands  ·  Ctrl+C/D to exit");
    const boxChars = new BoxChars();
    const top = boxChars.top(width, "Wisp TS");
    const body = lines.map((l) => boxChars.line(width, l)).join("\n");
    const bottom = boxChars.bottom(width);
    process.stdout.write(dim([top, body, bottom].join("\n")) + "\n\n");
  }

  private _startStdinReader(): void {
    if (this._readerActive) return;
    this._readerActive = true;
    this._stdinRl = readline.createInterface({ input: process.stdin });
    this._stdinRl.on("line", (line) => {
      const trimmed = line.trim();
      if (trimmed.toLowerCase() === "exit" || trimmed.toLowerCase() === "quit") {
        this._stdinQueue.close();
        this._stdinRl?.close();
      } else {
        this._stdinQueue.push(trimmed);
      }
    });
    this._stdinRl.on("close", () => {
      this._stdinQueue.close();
    });
  }

  async runRepl(session: Session): Promise<void> {
    this.printBanner(session, this.config.model);
    while (true) {
      let prompt = await this.recv();
      if (prompt === null) break;
      if (!prompt.trim()) continue;
      if (prompt.startsWith("/")) {
        if (prompt === "/exit" || prompt === "/quit") break;
        // Dispatch to commands.ts — handles /help, /clear, /tokens, etc.
        const adapter = new AgentAdapter(this.config, this.runtime, session);
        adapter.messages = session.messages as Array<{ role: string; content: string }> ?? [];
        adapter.activeSkill = (session as any)._activeSkill;
        const result = dispatch(prompt, adapter);
        if (result === true) continue;
        if (typeof result === "string") {
          // /continue returns a prompt string to run as follow-up
          prompt = result;
        } else {
          continue;
        }
      }

      this.resetBuffers();
      try {
        const handler = this.config.auto_approve ? undefined : this.approve.bind(this);
        for await (const event of this.runtime.runTurn(session, prompt, handler)) {
          this._renderEvent(process.stdout, event);
        }
        this._flushThinking(process.stdout);
        this._flushContent(process.stdout);
        const stats = this._progress.onDone();
        const statsLine = renderTurnStats(stats, termWidth());
        if (statsLine) process.stdout.write(statsLine + "\n");
        const ticker = renderFileTicker(stats.filesChanged, termWidth());
        if (ticker) process.stdout.write(ticker + "\n");
        process.stdout.write(dim("─".repeat(termWidth())) + "\n\n");
      } catch (exc) {
        this._flushThinking(process.stdout);
        this._flushContent(process.stdout);
        // Do NOT resetBuffers() here — failed turns shouldn't increment the turn counter
        process.stdout.write(error(`Error: ${exc}`) + "\n");
      }
    }
    process.stdout.write("\nExiting. Session saved.\n");
  }

  private _renderEvent(stdout: NodeJS.WriteStream, event: Record<string, unknown>): void {
    const etype = String(event.type ?? "");
    const ev = new AgentEvent(etype, event.data as Record<string, unknown> ?? {});
    const width = termWidth();

    const newPhase = this._progress.onEvent(ev);
    if (newPhase && newPhase !== this._phase) {
      this._phase = newPhase;
      const bar = renderPhaseBar(newPhase, width);
      if (bar) {
        stdout.write(bar + "\n");
        if (typeof (stdout as unknown as { flush?: () => void }).flush === "function") {
          (stdout as unknown as { flush: () => void }).flush();
        }
      }
    }

    switch (etype) {
      case EventType.THINKING:
        if (this._inContent) return;
        if (!this._inThinking) {
          this._flushContent(stdout, width);
          this._inThinking = true;
        }
        this._thinkingBuffer.push(ev.text);
        break;

      case EventType.CONTENT:
        if (this._inThinking) {
          this._flushThinking(stdout, width);
        }
        if (!this._inContent) {
          this._inContent = true;
        }
        this._contentBuffer.push(ev.text);
        break;

      case EventType.TOOL_CALL: {
        this._flushThinking(stdout, width);
        this._flushContent(stdout, width);
        const name = String(event.name ?? "");
        const args = (event.arguments as Record<string, unknown>) ?? {};
        const label = `${name} ${argsPreview(args)}`;
        this._getSpinner().start(label);
        break;
      }

      case EventType.TOOL_RESULT: {
        this._flushThinking(stdout, width);
        this._flushContent(stdout, width);
        const name = String(event.name ?? "");
        const result = event.result ?? "";
        const durationMs = typeof event.duration_ms === "number" ? event.duration_ms : undefined;
        const spinner = this._getSpinner();
        const isErr = this._isErrorResult(result);
        if (isErr) spinner.fail(name);
        else spinner.succeed(name);
        const rendered = this._renderToolResult(name, result, durationMs, width);
        if (rendered) {
          stdout.write(rendered + "\n");
          if (typeof (stdout as unknown as { flush?: () => void }).flush === "function") {
            (stdout as unknown as { flush: () => void }).flush();
          }
        }
        break;
      }

      case EventType.DONE:
        this._flushThinking(stdout, width);
        this._flushContent(stdout, width);
        break;

      case EventType.ERROR: {
        this._flushThinking(stdout, width);
        this._flushContent(stdout, width);
        const msg = String(event.message ?? "");
        stdout.write(error(`✗ ${msg}`) + "\n");
        if (typeof (stdout as unknown as { flush?: () => void }).flush === "function") {
          (stdout as unknown as { flush: () => void }).flush();
        }
        break;
      }

      case EventType.SYSTEM: {
        const level = String(event.level ?? "info");
        const msg = String(event.message ?? "");
        if (level === "warning") stdout.write(warning(`  ⚠ ${msg}\n`));
        else stdout.write(info(`  ℹ ${msg}\n`));
        if (typeof (stdout as unknown as { flush?: () => void }).flush === "function") {
          (stdout as unknown as { flush: () => void }).flush();
        }
        break;
      }

      default:
        break;
    }
  }

  private _flushThinking(stdout: NodeJS.WriteStream, width?: number): void {
    if (this._thinkingBuffer.length === 0) return;
    const full = this._thinkingBuffer.join("");
    this._thinkingBuffer = [];
    this._inThinking = false;
    if (!full.trim()) return;
    const w = width ?? termWidth();
    if (this.showThinking) {
      const rendered = renderThinkingBlock(full, true, w);
      if (rendered) stdout.write(rendered + "\n");
    } else {
      const lineCount = full.split("\n").length;
      const preview = full.trim().split("\n").find((l) => l.trim())?.slice(0, 60) ?? "";
      if (isAccessible()) {
        stdout.write(dim(`  [Thinking] "${preview}" — ${lineCount} lines\n`));
      } else {
        stdout.write(dim(`  🧠 Thinking: "${preview}" (${lineCount} lines)\n`));
      }
    }
    if (typeof (stdout as unknown as { flush?: () => void }).flush === "function") {
      (stdout as unknown as { flush: () => void }).flush();
    }
  }

  private _flushContent(stdout: NodeJS.WriteStream, width?: number): void {
    if (this._contentBuffer.length === 0) return;
    const full = this._contentBuffer.join("");
    this._contentBuffer = [];
    this._inContent = false;
    if (!full.trim()) return;
    const w = width ?? termWidth();
    const rendered = renderContentBlock(full, true, w);
    if (rendered) stdout.write(rendered + "\n");
    if (typeof (stdout as unknown as { flush?: () => void }).flush === "function") {
      (stdout as unknown as { flush: () => void }).flush();
    }
  }

  private _getSpinner(): Spinner {
    if (!this._spinner) {
      this._spinner = new Spinner(process.stdout, getOutputMode());
    }
    return this._spinner;
  }

  private _isErrorResult(result: unknown): boolean {
    if (result && typeof result === "object") {
      return (result as Record<string, unknown>).status === "error";
    }
    if (typeof result === "string") {
      return result.startsWith("Error") || result.startsWith("[");
    }
    return false;
  }

  private _renderToolResult(name: string, result: unknown, _durationMs: number | undefined, width: number, skipHeader = false): string | null {
    // Parse JSON
    let meta: Record<string, unknown> | null = null;
    let parsed: Record<string, unknown> | null = null;
    let resultText: string;
    if (typeof result === "string" && result.startsWith("{")) {
      try {
        const parsedLocal = JSON.parse(result) as Record<string, unknown>;
        meta = (parsedLocal.metadata as Record<string, unknown> | null) ?? null;
        resultText = String(parsedLocal.data ?? result);
        parsed = parsedLocal;
      } catch {
        resultText = result;
      }
    } else if (result && typeof result === "object") {
      meta = (result as Record<string, unknown>).metadata as Record<string, unknown> | null ?? null;
      resultText = String((result as Record<string, unknown>).data ?? result);
    } else {
      resultText = String(result ?? "");
    }

    const isError = (parsed != null && parsed.status === "error") || (result != null && typeof result === "object" && (result as Record<string, unknown>).status === "error") || resultText.startsWith("[") || resultText.startsWith("Error");
    const icon = isAccessible() ? (isError ? "[FAIL]" : "[PASS]") : (isError ? "✗" : "✓");
    const diffText = meta?.diff ? String(meta.diff) : "";
    const isEditTool = name === "write_file" || name === "edit_file" || name === "edit_file_multi";

    // Helper: header with dot padding
    const buildHeader = (iconStr: string, toolName: string) => {
      const prefix = `  ${iconStr} ${toolName} `;
      const prefixWidth = displayWidth(prefix);
      const fillWidth = width - prefixWidth - 2;
      if (fillWidth > 0) return dim(prefix + "·".repeat(fillWidth));
      return dim(prefix);
    };

    // Edit tools with diff
    if (isEditTool && diffText) {
      const summary = dim(`     → ${resultText.slice(0, 200).replace(/\n/g, " ")}`);
      const header = skipHeader ? "" : buildHeader(icon, name);
      const lines = [header, summary, dim(`     Diff (${diffText.split("\n").length} lines)`), dim(diffText.slice(0, 800))];
      return lines.filter(Boolean).join("\n");
    }

    // Full-output tools
    if (FULL_OUTPUT_TOOLS.has(name) && !isEditTool) {
      const outputStr = resultText;
      if (!this.showToolOutput) {
        const lineCount = outputStr.split("\n").length;
        if (isAccessible()) {
          return dim(`  ${icon} ${name} — ${lineCount} lines of output`);
        }
        const label = `${icon} ${name} — ${lineCount} lines`;
        const dots = " ·".repeat(Math.max(0, width - displayWidth(label) - 2));
        return dim(`  ${label}${dots}`);
      }
      const label = isAccessible() ? `${name} output` : `📤 ${name} output`;
      const rule = dim("  " + "─".repeat(Math.max(0, width - 4)));
      const wrapped = outputStr.split("\n").slice(0, 40);
      const indented = wrapped.map((line) => dim(`    ${line.slice(0, width - 8)}`)).join("\n");
      const header = skipHeader ? "" : buildHeader(icon, name);
      return [header, `  ${dim(label)}`, rule, indented].filter(Boolean).join("\n");
    }

    // Compact / default
    if (!this.showToolOutput) {
      if (skipHeader) return null;
      const fillContent = `  ${icon} ${name}`;
      const fillWidth = displayWidth(fillContent);
      const dots = " ·".repeat(Math.max(0, width - fillWidth - 2));
      return dim(fillContent + dots);
    }
    const preview = resultText.slice(0, 200).replace(/\n/g, " ");
    const suffix = resultText.length > 200 ? "..." : "";
    if (skipHeader) return dim(`     → ${preview}${suffix}`);
    return buildHeader(icon, name) + "\n" + dim(`     → ${preview}${suffix}`);
  }
}
