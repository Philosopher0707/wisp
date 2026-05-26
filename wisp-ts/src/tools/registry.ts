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

export const TOOL_IMPLS: Record<string, (args: Record<string, unknown>, workspace: string) => unknown> = {
  read_file: (args, ws) => toolReadFile(String(args.path), ws, Number(args.offset ?? 0), Number(args.limit ?? 1000000)),
  write_file: (args, ws) => toolWriteFile(String(args.path), ws, String(args.content)),
  edit_file: (args, ws) => toolEditFile(String(args.path), ws, String(args.old_text), String(args.new_text)),
  edit_file_multi: (args, ws) => toolEditFileMulti(String(args.path), ws, (args.edits as Array<{ old_text: string; new_text: string }>) ?? []),
  list_files: (args, ws) => toolListFiles(String(args.path ?? "."), ws, String(args.pattern ?? "*")),
  run_bash: async (args, ws) => asyncToolRunBash(String(args.command), ws, Number(args.timeout ?? 60)),
  git_status: (_args, ws) => toolGitStatus(ws),
  git_diff: (args, ws) => toolGitDiff(ws, String(args.path ?? ""), Boolean(args.staged ?? false)),
  git_branch: (args, ws) => toolGitBranch(ws, String(args.action), String(args.name ?? "")),
  git_commit: (args, ws) => toolGitCommit(ws, String(args.message), String(args.files ?? "")),
  git_push: (args, ws) => toolGitPush(ws, Boolean(args.set_upstream ?? false)),
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
