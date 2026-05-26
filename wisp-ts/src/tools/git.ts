/** Git tools for Wisp TS */

import { execSync } from "node:child_process";
import path from "node:path";

function _git(args: string[], workspace: string): string {
  try {
    return execSync(`git ${args.join(" ")}`, {
      cwd: path.resolve(workspace),
      encoding: "utf-8",
      timeout: 30000,
      maxBuffer: 1024 * 1024,
    }).trim();
  } catch (e: unknown) {
    const err = e as { stderr?: string; message: string };
    return err.stderr ?? err.message;
  }
}

export function toolGitStatus(workspace: string): string {
  return _git(["status", "-sb"], workspace);
}

export function toolGitDiff(workspace: string, filePath = "", staged = false): string {
  const args = staged ? ["diff", "--staged"] : ["diff"];
  if (filePath) args.push(filePath);
  return _git(args, workspace);
}

export function toolGitBranch(workspace: string, action: string, name = ""): string {
  if (action === "list") return _git(["branch", "-a"], workspace);
  if (action === "create") return _git(["checkout", "-b", name], workspace);
  if (action === "switch") return _git(["checkout", name], workspace);
  return "Unknown action: use list, create, or switch";
}

export function toolGitCommit(workspace: string, message: string, files = ""): string {
  if (files) {
    return _git(["add", files], workspace) + "\n" + _git(["commit", "-m", message], workspace);
  }
  return _git(["commit", "-am", message], workspace);
}

export function toolGitPush(workspace: string, setUpstream = false): string {
  const args = setUpstream ? ["push", "-u", "origin", "HEAD"] : ["push"];
  return _git(args, workspace);
}
