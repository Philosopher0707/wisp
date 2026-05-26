/** Tool registry for Wisp TS */

import { TOOL_SCHEMAS } from "./schemas.js";
import {
  toolReadFile,
  toolWriteFile,
  toolEditFile,
  toolEditFileMulti,
  toolListFiles,
} from "./filesystem.js";
import { asyncToolRunBash } from "./bash.js";
import {
  toolGitStatus,
  toolGitDiff,
  toolGitBranch,
  toolGitCommit,
  toolGitPush,
} from "./git.js";
import { toolWebFetch, toolWebSearch } from "./web.js";
import { toolSearchSymbols, toolSearchCodebase } from "./search.js";
import { toolRemember, toolRecall } from "./memory.js";
import { toolPlanTask, toolMarkStepDone, toolUpdatePlan } from "./plan.js";
import { toolDiagnose } from "./diagnose.js";
import { toolRunTests } from "./tests.js";
import {
  toolLspDiagnostics,
  toolLspDefinition,
  toolLspReferences,
  toolLspHover,
  toolLspSymbols,
} from "./lsp.js";

export const TOOL_IMPLS: Record<string, (args: Record<string, unknown>, workspace: string) => unknown> = {
  read_file: (args, ws) => toolReadFile(String(args.path), ws, Number(args.offset ?? 0), Number(args.limit ?? 1000000)),
  write_file: (args, ws) => toolWriteFile(String(args.path), ws, String(args.content)),
  edit_file: (args, ws) => toolEditFile(String(args.path), ws, String(args.old_text), String(args.new_text)),
  edit_file_multi: (args, ws) => toolEditFileMulti(String(args.path), ws, (args.edits as Array<{ old_text: string; new_text: string }>) ?? []),
  list_files: (args, ws) => toolListFiles(String(args.path ?? "."), ws, String(args.pattern ?? "*")),
  run_bash: async (args, ws) => asyncToolRunBash(String(args.command), ws, Number(args.timeout ?? 60)),
  web_fetch: async (args, _ws) => toolWebFetch(String(args.url), Number(args.max_chars ?? 10000)),
  web_search: async (args, _ws) => toolWebSearch(String(args.query), Number(args.num_results ?? 5)),
  git_status: (_args, ws) => toolGitStatus(ws),
  git_diff: (args, ws) => toolGitDiff(ws, String(args.path ?? ""), Boolean(args.staged ?? false)),
  git_branch: (args, ws) => toolGitBranch(ws, String(args.action), String(args.name ?? "")),
  git_commit: (args, ws) => toolGitCommit(ws, String(args.message), String(args.files ?? "")),
  git_push: (args, ws) => toolGitPush(ws, Boolean(args.set_upstream ?? false)),
  search_symbols: (args, ws) => toolSearchSymbols(String(args.query), ws, Number(args.max_results ?? 20)),
  search_codebase: (args, ws) => toolSearchCodebase(String(args.query), Number(args.top_k ?? 5), ws),
  remember: (args, ws) => toolRemember(String(args.fact), ws),
  recall: (args, ws) => toolRecall(String(args.query), ws, Number(args.limit ?? 10)),
  plan_task: (args, ws) => toolPlanTask(String(args.goal), String(args.tasks), ws),
  mark_step_done: (args, ws) => toolMarkStepDone(String(args.task_id), String(args.notes ?? ""), ws),
  update_plan: (args, ws) => toolUpdatePlan(String(args.task_id), String(args.status), String(args.notes ?? ""), ws),
  diagnose: (args, _ws) => toolDiagnose(String(args.error_output)),
  run_tests: async (args, ws) => toolRunTests((args.files as string[]) ?? [], ws, Number(args.timeout ?? 120)),
  lsp_diagnostics: (args, _ws) => toolLspDiagnostics(String(args.path)),
  lsp_definition: (args, _ws) => toolLspDefinition(String(args.path), Number(args.line ?? 0), Number(args.char ?? 0)),
  lsp_references: (args, _ws) => toolLspReferences(String(args.path), Number(args.line ?? 0), Number(args.char ?? 0)),
  lsp_hover: (args, _ws) => toolLspHover(String(args.path), Number(args.line ?? 0), Number(args.char ?? 0)),
  lsp_symbols: (args, _ws) => toolLspSymbols(String(args.path)),
};

export async function executeTool(name: string, args: Record<string, unknown>, workspace = "."): Promise<unknown> {
  const impl = TOOL_IMPLS[name];
  if (!impl) throw new Error(`Unknown tool: ${name}`);
  const result = impl(args, workspace);
  if (result instanceof Promise) return await result;
  return result;
}

export class ToolRegistry {
  private _impls: Record<string, (args: Record<string, unknown>, workspace: string) => unknown>;

  constructor(impls?: Record<string, (args: Record<string, unknown>, workspace: string) => unknown>) {
    this._impls = impls ?? { ...TOOL_IMPLS };
  }

  schemas(): typeof TOOL_SCHEMAS {
    return TOOL_SCHEMAS;
  }

  has(name: string): boolean {
    return name in this._impls;
  }

  async execute(name: string, args: Record<string, unknown>, workspace: string): Promise<unknown> {
    const impl = this._impls[name];
    if (!impl) throw new Error(`Unknown tool: ${name}`);
    const result = impl(args, workspace);
    if (result instanceof Promise) return await result;
    return result;
  }
}

export const defaultRegistry = new ToolRegistry();
