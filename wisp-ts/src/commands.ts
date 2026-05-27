/** Slash commands for Wisp REPL — local directives that bypass the LLM. */

import process from "node:process";
import path from "node:path";
import { spawn } from "node:child_process";
import { info, success, error, warning, dim, accent } from "./colors.js";
import { WispConfig } from "./config.js";
import { Session } from "./core/session.js";
import { AgentRuntime } from "./core/runtime.js";
import { toolRunBash } from "./tools/bash.js";
import { toolReadFile, toolListFiles } from "./tools/filesystem.js";
import { checkDangerousCommand } from "./tools/bash.js";
import { discoverSkills, findSkill } from "./skills.js";
import { SubagentContract } from "./multi_agent/task.js";
import { SubagentOrchestrator } from "./multi_agent/orchestrator.js";

export type CommandHandler = (agent: AgentAdapter, args: string) => string | true | void;

export interface CommandDef {
  name: string;
  description: string;
  handler: CommandHandler;
  aliases: string[];
  usage: string;
}

const _REGISTRY = new Map<string, CommandDef>();

export function register(
  name: string,
  description: string,
  handler: CommandHandler,
  aliases: string[] = [],
  usage = ""
): CommandDef {
  const cmd: CommandDef = { name, description, handler, aliases, usage };
  _REGISTRY.set(name, cmd);
  for (const alias of aliases) _REGISTRY.set(alias, cmd);
  return cmd;
}

export function lookup(name: string): CommandDef | undefined {
  return _REGISTRY.get(name);
}

export function allCommands(): CommandDef[] {
  const seen = new Set<string>();
  const result: CommandDef[] = [];
  for (const cmd of _REGISTRY.values()) {
    if (!seen.has(cmd.name)) {
      seen.add(cmd.name);
      result.push(cmd);
    }
  }
  return result.sort((a, b) => a.name.localeCompare(b.name));
}

export function dispatch(text: string, agent: AgentAdapter): string | true | false {
  text = text.trim();
  if (!text.startsWith("/")) return false;
  const body = text.slice(1).trim();
  if (!body) {
    process.stdout.write(info("Available commands:") + "\n");
    for (const cmd of allCommands()) {
      const aliasStr = cmd.aliases.length ? dim(` (aliases: ${cmd.aliases.join(", ")})`) : "";
      process.stdout.write(`  ${accent("/" + cmd.name).padEnd(14)}  ${cmd.description}${aliasStr}\n`);
    }
    process.stdout.write("\n" + dim("Commands run locally and do not send anything to the LLM.") + "\n");
    return true;
  }
  const parts = body.split(/\s+/, 1);
  const name = parts[0];
  const args = parts[1] ?? "";
  const cmd = lookup(name);
  if (!cmd) {
    process.stdout.write(error(`Unknown command: /${name}. Type /help for available commands.`) + "\n");
    return true;
  }
  try {
    const result = cmd.handler(agent, args);
    if (typeof result === "string" && result) return result;
  } catch (e) {
    process.stdout.write(error(`Command failed: ${e}`) + "\n");
  }
  return true;
}

export class AgentAdapter {
  config: WispConfig;
  runtime: AgentRuntime;
  session: Session;
  messages: Array<{ role: string; content: string }>;
  private _activeSkill?: string;

  constructor(config: WispConfig, runtime: AgentRuntime, session: Session) {
    this.config = config;
    this.runtime = runtime;
    this.session = session;
    this.messages = [];
  }

  get activeSkill(): string | undefined {
    return this._activeSkill;
  }

  set activeSkill(v: string | undefined) {
    this._activeSkill = v;
  }

  saveSession(): void {
    // Handled by runtime automatically
  }
}

// ── Command implementations ──────────────────────────────────────

register("help", "Show available slash commands", (agent, _args) => {
  process.stdout.write(info("Available commands:") + "\n");
  for (const cmd of allCommands()) {
    const aliasStr = cmd.aliases.length ? dim(` (aliases: ${cmd.aliases.join(", ")})`) : "";
    process.stdout.write(`  ${accent("/" + cmd.name).padEnd(14)}  ${cmd.description}${aliasStr}\n`);
  }
  process.stdout.write("\n" + dim("Commands run locally and do not send anything to the LLM.") + "\n");
}, ["h", "?"], "/help");

register("clear", "Clear conversation history", (agent, _args) => {
  const count = agent.messages.length;
  agent.messages.length = 0;
  process.stdout.write(success(`Cleared ${count} messages.`) + "\n");
}, ["cls"]);

register("session", "Show session info", (agent, _args) => {
  const s = agent.session;
  process.stdout.write(info("Session info:") + "\n");
  process.stdout.write(`  ${dim("Session ID:")}    ${s.sessionId}\n`);
  process.stdout.write(`  ${dim("Model:")}         ${agent.config.model}\n`);
  process.stdout.write(`  ${dim("Workspace:")}     ${s.workspace}\n`);
  process.stdout.write(`  ${dim("Active skill:")}  ${agent.activeSkill ?? "(none)"}\n`);
  process.stdout.write(`  ${dim("Messages:")}      ${agent.messages.length}\n`);
  process.stdout.write(`  ${dim("Auto-approve:")}  ${agent.config.auto_approve}\n`);
  process.stdout.write(`  ${dim("Show thinking:")} ${agent.config.show_thinking}\n`);
});

register("save", "Force-save the current session", (agent, _args) => {
  agent.saveSession();
  process.stdout.write(success(`Session saved: ${agent.session.sessionId}`) + "\n");
});

register("tokens", "Show estimated token usage", (agent, _args) => {
  const overhead = Math.ceil(800 / (agent.config.chars_per_token || 4));
  const msgTokens = Math.ceil(agent.messages.reduce((s, m) => s + (m.content?.length ?? 0), 0) / (agent.config.chars_per_token || 4));
  const budget = agent.config.max_context_tokens;
  const used = msgTokens + overhead;
  const pct = budget ? (used / budget) * 100 : 0;
  const filled = Math.min(20, Math.floor(pct / 5));
  const bar = "█".repeat(filled) + "░".repeat(20 - filled);
  process.stdout.write(info(`Context: [${bar}] ${used.toLocaleString()} / ${budget.toLocaleString()} (${pct.toFixed(1)}%)`) + "\n");
  process.stdout.write(`  ${dim("System overhead:")} ~${overhead.toLocaleString()} tokens\n`);
  process.stdout.write(`  ${dim("Messages:")}        ~${msgTokens.toLocaleString()} tokens\n`);
});

register("compact", "Compact session history to save context", (agent, _args) => {
  const msgCount = agent.messages.length;
  if (msgCount <= 10) {
    process.stdout.write(dim(`Session has only ${msgCount} messages — not enough to compact.`) + "\n");
    return;
  }
  const keep = 10;
  const removed = msgCount - keep;
  agent.messages.splice(0, removed);
  process.stdout.write(success(`Truncated: ${msgCount} → ${keep} messages (${removed} removed)`) + "\n");
}, ["c"]);

register("approve", "Toggle auto-approve for tool calls", (agent, _args) => {
  agent.config = new WispConfig({ ...agent.config, auto_approve: !agent.config.auto_approve });
  const state = agent.config.auto_approve ? "ON" : "OFF";
  process.stdout.write(success(`Auto-approve: ${state}`) + "\n");
}, ["y"]);

register("thinking", "Toggle reasoning trace display", (agent, _args) => {
  agent.config = new WispConfig({ ...agent.config, show_thinking: !agent.config.show_thinking });
  const state = agent.config.show_thinking ? "ON" : "OFF";
  process.stdout.write(success(`Show thinking: ${state}`) + "\n");
}, ["T"]);

register("bash", "Run a bash command directly", (agent, args) => {
  if (!args) {
    process.stdout.write(info("Usage: /bash <command>") + "\n");
    return;
  }
  const reason = checkDangerousCommand(args);
  if (reason) {
    if (!process.stdin.isTTY) {
      process.stdout.write(warning(`Blocked dangerous command (${reason})`) + "\n");
      return;
    }
    process.stdout.write(warning(`  DANGEROUS: ${reason}`) + "\n");
    // In real implementation would prompt; for now skip
    process.stdout.write(dim("  Skipped (dangerous command)") + "\n");
    return;
  }
  const ws = agent.config.workspace || ".";
  toolRunBash(args, ws).then((result) => process.stdout.write(result + "\n")).catch((e) => process.stdout.write(error(`${e}`) + "\n"));
}, ["!", "sh"]);

register("workspace", "Change working directory", (agent, args) => {
  if (!args) {
    process.stdout.write(`Current workspace: ${accent(agent.config.workspace || ".")}\n`);
    return;
  }
  const newWs = path.resolve(args.trim());
  agent.config = new WispConfig({ ...agent.config, workspace: newWs });
  process.stdout.write(success(`Workspace: ${agent.config.workspace}`) + "\n");
}, ["cd", "w"]);

register("ls", "List files in a directory", (agent, args) => {
  const ws = agent.config.workspace || ".";
  const parts = args.split(/\s+/) || [];
  const dirPath = parts[0] || ".";
  const pattern = parts[1] || "*";
  try {
    const result = toolListFiles(dirPath, ws, pattern);
    process.stdout.write(result + "\n");
  } catch (e) {
    process.stdout.write(error(`${e}`) + "\n");
  }
}, ["files", "dir"]);

register("read", "Read a file", (agent, args) => {
  const ws = agent.config.workspace || ".";
  const parts = args.split(/\s+/);
  if (!parts.length) {
    process.stdout.write(info("Usage: /read <file> [offset] [limit]") + "\n");
    return;
  }
  const offset = Number(parts[1]) || 0;
  const limit = Number(parts[2]) || 2000;
  try {
    const result = toolReadFile(parts[0], ws, offset, limit);
    process.stdout.write(result + "\n");
  } catch (e) {
    process.stdout.write(error(`${e}`) + "\n");
  }
}, ["cat"]);

register("drop", "Remove the last message from history", (agent, _args) => {
  if (!agent.messages.length) {
    process.stdout.write(dim("History is empty.") + "\n");
    return;
  }
  const removed = agent.messages.pop()!;
  const role = removed.role ?? "?";
  const preview = (removed.content ?? "").slice(0, 60).replace(/\n/g, " ");
  process.stdout.write(success(`Dropped last message (${role}): ${preview}...`) + "\n");
}, ["pop", "undo"]);

register("skill", "Load or list skills", (agent, args) => {
  const ws = agent.config.workspace || ".";
  if (!args?.trim()) {
    const skills = discoverSkills(ws);
    if (!skills.length) {
      process.stdout.write(dim("No skills found.") + "\n");
      return;
    }
    for (const sk of skills) {
      const marker = agent.activeSkill === sk.name ? accent(" → ") : "   ";
      process.stdout.write(`${marker}${accent(sk.name)}: ${sk.description}\n`);
    }
    return;
  }
  const name = args.trim();
  const skill = findSkill(name, ws);
  if (!skill) {
    process.stdout.write(warning(`Skill '${name}' not found.`) + "\n");
    return;
  }
  agent.activeSkill = name;
  process.stdout.write(success(`Skill loaded: ${skill.name}`) + "\n");
}, ["s"]);

register("spawn", "Spawn a subagent for a scoped task", (agent, args) => {
  if (!args) {
    process.stdout.write(info("Usage: /spawn <task description>") + "\n");
    return;
  }
  const contract = new SubagentContract({ name: "spawn", task: args, timeoutSeconds: 120, maxIterations: 15 });
  const orch = new SubagentOrchestrator(agent.config, agent.config.workspace || ".");
  process.stdout.write(accent(`Spawning subagent: ${args.slice(0, 60)}...`) + "\n");
  orch.run(contract).then((result) => {
    const status = result.success ? success("✓") : error("✗");
    process.stdout.write(`${status} Subagent done (${result.elapsedSeconds.toFixed(1)}s, ${result.iterationsUsed} iterations)\n`);
    process.stdout.write("─".repeat(40) + "\n");
    process.stdout.write(result.output + "\n");
  }).catch((e) => process.stdout.write(error(`${e}`) + "\n"));
}, ["sub", "delegate"]);

register("new", "Start a new session", (agent, _args) => {
  agent.saveSession();
  agent.session = new Session(`sess-${Date.now()}`, agent.config.model, agent.config.workspace || ".");
  agent.messages.length = 0;
  process.stdout.write(success(`New session started: ${agent.session.sessionId}`) + "\n");
});

register("exit", "Exit Wisp", (_agent, _args) => {
  process.exit(0);
}, ["quit", "q", "bye"]);
