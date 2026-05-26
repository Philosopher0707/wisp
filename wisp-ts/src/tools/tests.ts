import { spawn } from "node:child_process";

export async function toolRunTests(files: string[] = [], workspace = ".", timeout = 120): Promise<string> {
  const args = files.length > 0
    ? [...files]
    : [];

  const cmd = files.length > 0 ? "npx jest --no-coverage" : "npx jest --no-coverage";
  const child = spawn(cmd.split(" ")[0], [...cmd.split(" ").slice(1), ...args], {
    cwd: workspace,
    timeout: timeout * 1000,
    shell: true,
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (d) => { stdout += String(d); });
  child.stderr.on("data", (d) => { stderr += String(d); });

  return new Promise((resolve) => {
    child.on("close", (code) => {
      const output = stdout + stderr;
      const summary = output.split("\n").slice(-10).join("\n");
      if (code === 0) return resolve(`✓ Tests passed\n\n${summary}`);
      resolve(`✗ Tests failed (exit ${code})\n\n${summary}\n\nFull output:\n${output.slice(0, 5000)}`);
    });
    child.on("error", (err) => {
      resolve(`✗ Test runner error: ${err.message}`);
    });
  });
}
