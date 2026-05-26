/** SecurityPolicy — unified security decision layer. */

import path from "node:path";
import { PermissionMode } from "../config.js";

const SAFE_READ_TOOLS = new Set([
  "read_file", "list_files", "search_codebase", "search_symbols",
  "git_status", "git_diff", "lsp_diagnostics", "lsp_definition",
  "lsp_references", "lsp_hover", "lsp_symbols", "web_fetch",
  "web_search", "recall",
]);

const ASK_ALL_BLOCK_TOOLS = new Set([
  "write_file", "edit_file", "edit_file_multi", "run_bash",
  "git_branch", "git_commit", "git_push", "gh_pr_create",
  "spawn", "plan_task", "mark_step_done", "update_plan",
]);

const AUTO_EDIT_BLOCK_TOOLS = new Set([
  "run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create", "spawn",
]);

export interface Action {
  name: string;
  args: Record<string, unknown>;
}

export interface SecurityContext {
  workspace: string;
}

export interface SecurityDecision {
  allowed: boolean;
  reason: string;
  modifiedArgs?: Record<string, unknown>;
}

export class SecurityPolicy {
  permissionMode: PermissionMode;
  trustedWorkspaces: Set<string>;
  private _auditLog: Array<{ action: string; allowed: boolean; reason: string; timestamp: number }> = [];

  constructor(permissionMode: PermissionMode = PermissionMode.FULL, trustedWorkspaces?: string[]) {
    this.permissionMode = permissionMode;
    this.trustedWorkspaces = new Set(trustedWorkspaces ?? []);
  }

  check(action: Action, context: SecurityContext): SecurityDecision {
    const trust = this._checkTrust(context);
    if (!trust.allowed) {
      this._audit(action, trust);
      return trust;
    }

    const modeResult = this._checkMode(action);
    if (!modeResult.allowed) {
      this._audit(action, modeResult);
      return modeResult;
    }

    const decision: SecurityDecision = { allowed: true, reason: "" };
    this._audit(action, decision);
    return decision;
  }

  private _checkTrust(context: SecurityContext): SecurityDecision {
    if (this.trustedWorkspaces.size === 0) return { allowed: true, reason: "" };
    const ws = path.resolve(context.workspace);
    for (const trusted of this.trustedWorkspaces) {
      if (path.resolve(trusted) === ws) return { allowed: true, reason: "" };
    }
    return { allowed: false, reason: `Untrusted workspace: ${context.workspace}` };
  }

  private _checkMode(action: Action): SecurityDecision {
    const name = action.name;
    const mode = this.permissionMode;

    if (mode === PermissionMode.FULL) return { allowed: true, reason: "" };
    if (mode === PermissionMode.READ_ONLY) {
      if (SAFE_READ_TOOLS.has(name)) return { allowed: true, reason: "" };
      return { allowed: false, reason: `READ_ONLY mode: ${name} is not a read-only tool` };
    }
    if (mode === PermissionMode.ASK_ALL) {
      if (ASK_ALL_BLOCK_TOOLS.has(name)) {
        return { allowed: false, reason: `ASK_ALL mode: ${name} requires approval` };
      }
      return { allowed: true, reason: "" };
    }
    if (mode === PermissionMode.AUTO_EDIT) {
      if (AUTO_EDIT_BLOCK_TOOLS.has(name)) {
        return { allowed: false, reason: `AUTO_EDIT mode: ${name} requires approval` };
      }
      return { allowed: true, reason: "" };
    }
    return { allowed: true, reason: "" };
  }

  private _audit(action: Action, decision: SecurityDecision): void {
    this._auditLog.push({
      action: action.name,
      allowed: decision.allowed,
      reason: decision.reason,
      timestamp: Date.now() / 1000,
    });
    if (this._auditLog.length > 1000) this._auditLog.shift();
  }

  auditLog(): typeof this._auditLog {
    return [...this._auditLog];
  }
}
