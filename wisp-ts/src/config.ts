/** Configuration for Wisp — reads settings from environment, CLI args, and config files.
 * Settings are resolved with priority: env vars > config file > defaults.
 */

import process from "node:process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

export enum PermissionMode {
  FULL = "full",
  ASK_ALL = "ask_all",
  AUTO_EDIT = "auto_edit",
  READ_ONLY = "read_only",
}

export const DEFAULT_OLLAMA_URL = "http://localhost:11434";
export const DEFAULT_MODEL = "kimi-k2.6:cloud";
export const DEFAULT_MAX_CONTEXT_TOKENS = 256000;

const WISP_CONFIG_DIR = path.join(os.homedir(), ".config", "wisp");

interface SettingSchema {
  type: "string" | "number" | "boolean" | "list";
  default: unknown;
  description: string;
  envVar: string;
  min?: number;
  max?: number;
}

const SETTINGS_SCHEMA: Record<string, SettingSchema> = {
  provider: {
    type: "string",
    default: "ollama",
    description: "Model provider backend",
    envVar: "WISP_PROVIDER",
  },
  ollama_url: {
    type: "string",
    default: DEFAULT_OLLAMA_URL,
    description: "Ollama API endpoint URL",
    envVar: "WISP_OLLAMA_URL",
  },
  model: {
    type: "string",
    default: DEFAULT_MODEL,
    description: "Default Ollama model",
    envVar: "WISP_MODEL",
  },
  temperature: {
    type: "number",
    default: 0.2,
    description: "Model temperature (0.0–2.0)",
    envVar: "WISP_TEMPERATURE",
    min: 0.0,
    max: 2.0,
  },
  max_tokens: {
    type: "number",
    default: 32768,
    description: "Max tokens per response (null for no limit)",
    envVar: "WISP_MAX_TOKENS",
  },
  skill_dirs: {
    type: "list",
    default: [".agents/skills", ".warp/skills", ".claude/skills"],
    description: "Directories to scan for skills",
    envVar: "WISP_SKILL_DIRS",
  },
  context_files: {
    type: "list",
    default: ["CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"],
    description: "Project context files to load and inject into system prompt",
    envVar: "WISP_CONTEXT_FILES",
  },
  workspace: {
    type: "string",
    default: null,
    description: "Working directory (null = current dir)",
    envVar: "WISP_WORKSPACE",
  },
  auto_approve: {
    type: "boolean",
    default: true,
    description: "Auto-approve tool calls without prompting",
    envVar: "WISP_AUTO_APPROVE",
  },
  permission_mode: {
    type: "string",
    default: PermissionMode.AUTO_EDIT,
    description: "Permission level: full | ask_all | auto_edit | read_only",
    envVar: "WISP_PERMISSION_MODE",
  },
  show_thinking: {
    type: "boolean",
    default: true,
    description: "Show model reasoning trace inline",
    envVar: "WISP_SHOW_THINKING",
  },
  show_tool_output: {
    type: "boolean",
    default: true,
    description: "Show full tool output (when false, collapse to one-liners)",
    envVar: "WISP_SHOW_TOOL_OUTPUT",
  },
  compact_mode: {
    type: "boolean",
    default: false,
    description: "Minimal rendering mode — no boxes, flat output",
    envVar: "WISP_COMPACT_MODE",
  },
  log_format: {
    type: "string",
    default: "text",
    description: "Log output format: text or json",
    envVar: "WISP_LOG_FORMAT",
  },
  max_iterations: {
    type: "number",
    default: 30,
    description: "Max agent loop iterations per user turn",
    envVar: "WISP_MAX_ITERATIONS",
    min: 1,
    max: 100,
  },
  max_reflections: {
    type: "number",
    default: 3,
    description: "Max repeated identical tool calls before stopping",
    envVar: "WISP_MAX_REFLECTIONS",
    min: 0,
    max: 10,
  },
  max_context_tokens: {
    type: "number",
    default: DEFAULT_MAX_CONTEXT_TOKENS,
    description: "Context window size in tokens",
    envVar: "WISP_MAX_CONTEXT_TOKENS",
    min: 1024,
  },
  chars_per_token: {
    type: "number",
    default: 4,
    description: "Estimated chars per token for context budgeting",
    envVar: "WISP_CHARS_PER_TOKEN",
    min: 1,
    max: 10,
  },
  auto_compact: {
    type: "boolean",
    default: true,
    description: "Automatically compact sessions when they grow too long",
    envVar: "WISP_AUTO_COMPACT",
  },
  compact_threshold_tokens: {
    type: "number",
    default: 75,
    description: "Token usage percentage (0-100) to trigger auto-compaction",
    envVar: "WISP_COMPACT_THRESHOLD_TOKENS",
    min: 10,
    max: 95,
  },
  compact_keep_recent: {
    type: "number",
    default: 10,
    description: "Number of recent messages to preserve during compaction",
    envVar: "WISP_COMPACT_KEEP_RECENT",
    min: 4,
    max: 50,
  },
  tool_timeout: {
    type: "number",
    default: 120,
    description: "Timeout in seconds for individual tool calls",
    envVar: "WISP_TOOL_TIMEOUT",
    min: 1,
    max: 600,
  },
};

export function getSchema(): Record<string, SettingSchema> {
  return { ...SETTINGS_SCHEMA };
}

function typeName(schema: SettingSchema): string {
  return schema.type;
}

export function validateConfig(config: Record<string, unknown>): string[] {
  const errors: string[] = [];
  for (const [key, value] of Object.entries(config)) {
    if (!(key in SETTINGS_SCHEMA)) {
      errors.push(`Unknown setting: '${key}'`);
      continue;
    }
    const schema = SETTINGS_SCHEMA[key];
    // Type check
    let ok = false;
    if (schema.type === "string") ok = typeof value === "string" || value === null;
    else if (schema.type === "number") ok = typeof value === "number";
    else if (schema.type === "boolean") ok = typeof value === "boolean";
    else if (schema.type === "list") ok = Array.isArray(value);
    if (!ok) {
      errors.push(`'${key}': expected ${schema.type}, got ${typeof value} (${JSON.stringify(value)})`);
      continue;
    }
    // Range check
    if (typeof value === "number" && schema.min !== undefined && value < schema.min) {
      errors.push(`'${key}': ${value} is below minimum ${schema.min}`);
    }
    if (typeof value === "number" && schema.max !== undefined && value > schema.max) {
      errors.push(`'${key}': ${value} is above maximum ${schema.max}`);
    }
  }
  return errors;
}

export interface WispConfigData {
  provider: string;
  ollama_url: string;
  model: string;
  temperature: number;
  max_tokens: number | null;
  skill_dirs: string[];
  context_files: string[];
  workspace: string | null;
  auto_approve: boolean;
  permission_mode: PermissionMode;
  show_thinking: boolean;
  show_tool_output: boolean;
  compact_mode: boolean;
  log_format: string;
  max_iterations: number;
  max_reflections: number;
  max_context_tokens: number;
  chars_per_token: number;
  auto_compact: boolean;
  compact_threshold_tokens: number;
  compact_keep_recent: number;
  tool_timeout: number;
}

export class WispConfig implements WispConfigData {
  provider = "ollama";
  ollama_url = DEFAULT_OLLAMA_URL;
  model = DEFAULT_MODEL;
  temperature = 0.2;
  max_tokens: number | null = 32768;
  skill_dirs = [".agents/skills", ".warp/skills", ".claude/skills"];
  context_files = ["CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"];
  workspace: string | null = null;
  auto_approve = true;
  permission_mode = PermissionMode.AUTO_EDIT;
  show_thinking = true;
  show_tool_output = true;
  compact_mode = false;
  log_format = "text";
  max_iterations = 30;
  max_reflections = 3;
  max_context_tokens = DEFAULT_MAX_CONTEXT_TOKENS;
  chars_per_token = 4;
  auto_compact = true;
  compact_threshold_tokens = 75;
  compact_keep_recent = 10;
  tool_timeout = 120;

  constructor(overrides?: Partial<WispConfigData>) {
    this._loadFromEnv();
    this._loadFromFile();
    if (overrides) {
      Object.assign(this, overrides);
    }
  }

  private _loadFromEnv(): void {
    for (const [key, schema] of Object.entries(SETTINGS_SCHEMA)) {
      const envVal = process.env[schema.envVar];
      if (envVal === undefined) continue;
      (this as unknown as Record<string, unknown>)[key] = parseValue(envVal, schema.type);
    }
  }

  private _loadFromFile(): void {
    try {
      const configPath = path.join(WISP_CONFIG_DIR, "config.json");
      if (!fs.existsSync(configPath)) return;
      const raw = fs.readFileSync(configPath, "utf-8");
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      for (const [key, value] of Object.entries(parsed)) {
        if (key in SETTINGS_SCHEMA) {
          (this as unknown as Record<string, unknown>)[key] = value;
        }
      }
    } catch {
      // ignore file errors
    }
  }

  toJSON(): WispConfigData {
    return {
      provider: this.provider,
      ollama_url: this.ollama_url,
      model: this.model,
      temperature: this.temperature,
      max_tokens: this.max_tokens,
      skill_dirs: this.skill_dirs,
      context_files: this.context_files,
      workspace: this.workspace,
      auto_approve: this.auto_approve,
      permission_mode: this.permission_mode,
      show_thinking: this.show_thinking,
      show_tool_output: this.show_tool_output,
      compact_mode: this.compact_mode,
      log_format: this.log_format,
      max_iterations: this.max_iterations,
      max_reflections: this.max_reflections,
      max_context_tokens: this.max_context_tokens,
      chars_per_token: this.chars_per_token,
      auto_compact: this.auto_compact,
      compact_threshold_tokens: this.compact_threshold_tokens,
      compact_keep_recent: this.compact_keep_recent,
      tool_timeout: this.tool_timeout,
    };
  }
}

function parseValue(raw: string, type: string): unknown {
  if (type === "boolean") return raw === "true" || raw === "1" || raw === "";
  if (type === "number") {
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n;
  }
  if (type === "list") {
    try {
      return JSON.parse(raw);
    } catch {
      return raw.split(",").map((s) => s.trim());
    }
  }
  return raw;
}

export function loadConfig(): Record<string, unknown> {
  try {
    const configPath = path.join(WISP_CONFIG_DIR, "config.json");
    if (!fs.existsSync(configPath)) return {};
    return JSON.parse(fs.readFileSync(configPath, "utf-8")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export function saveConfig(config: Record<string, unknown>): void {
  const errors = validateConfig(config);
  if (errors.length > 0) {
    throw new Error(`Config validation failed:\n${errors.join("\n")}`);
  }
  fs.mkdirSync(WISP_CONFIG_DIR, { recursive: true });
  const configPath = path.join(WISP_CONFIG_DIR, "config.json");
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");
}
