/** Bash execution tool for Wisp TS */

import { spawn } from "node:child_process";
import path from "node:path";

const MAX_CMD_LENGTH = 4096;
const MAX_BASH_OUTPUT = 50 * 1024;
const DANGEROUS_PATTERNS = [
  /rm\s+-rf\s+\//,
  /mkfs\./,
  /dd\s+if=.*of=\/dev/,
  /\u003e\s*\/dev\/null.*\u0026/,
  /:(){ :|: \u0026 };:/,
];

export function checkDangerousCommand(command: string): string | null {
  for (const pattern of DANGEROUS_PATTERNS) {
    if (pattern.test(command)) return `Blocked dangerous pattern: ${pattern.source}`;
  }
  return null;
}

export async function asyncToolRunBash(command: string, workspace: string, timeout = 60): Promise<string> {
  if (command.length > MAX_CMD_LENGTH) throw new Error(`Command too long: ${command.length} chars`);
  if (command.includes("\x00")) throw new Error("Null bytes not allowed");

  const danger = checkDangerousCommand(command);
  if (danger) throw new Error(danger);

  const cwd = path.resolve(workspace);

  return new Promise((resolve, reject) => {
    const proc = spawn(command, { cwd, shell: true, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
      setTimeout(() => { if (!proc.killed) proc.kill("SIGKILL"); }, 2000);
      reject(new Error(`Command timed out after ${timeout}s`));
    }, timeout * 1000);

    proc.stdout.on("data", (data) => { stdout += data; });
    proc.stderr.on("data", (data) => { stderr += data; });

    proc.on("close", (code) => {
      clearTimeout(timer);
      if (killed) return;
      let output = "";
      if (code !== 0) output = `[exit code: ${code}]\n`;
      if (stdout) output += stdout;
      if (stderr) {
        if (stdout) output += "\n--- stderr ---\n";
        output += stderr;
      }
      output = output.replace(/\x1b\[[0-9;]*m/g, ""); // strip ANSI
      if (output.length > MAX_BASH_OUTPUT) {
        output = output.slice(0, MAX_BASH_OUTPUT) + "\n... [output truncated]";
      }
      resolve(output || "(no output)");
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`Command failed: ${err.message}`));
    });
  });
}

export function toolRunBash(command: string, workspace: string, timeout = 60): Promise<string> {
  return asyncToolRunBash(command, workspace, timeout);
}
