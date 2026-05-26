import { stripAnsi } from "../terminal_width.js";

export function toolDiagnose(errorOutput: string): string {
  const text = stripAnsi(errorOutput.trim());

  // Heuristic diagnosis patterns
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  const diag: { type: string; line?: number; file?: string; cause: string; fix: string } = {
    type: "unknown",
    cause: "Could not determine root cause.",
    fix: "Review the error output and fix the indicated issue.",
  };

  for (const line of lines) {
    // TypeScript error
    const tsMatch = line.match(/(.+?)\((\d+),(\d+)\): error (TS\d+):\s*(.+)/);
    if (tsMatch) {
      diag.type = "TypeScript";
      diag.file = tsMatch[1];
      diag.line = Number(tsMatch[2]);
      diag.cause = `TS${tsMatch[4]}: ${tsMatch[5]}`;
      diag.fix = `Fix the type error in ${tsMatch[1]} line ${tsMatch[2]}.`;
      break;
    }
    // Python traceback
    const pyMatch = line.match(/File "(.+?)", line (\d+)/);
    if (pyMatch) {
      diag.type = "Python";
      diag.file = pyMatch[1];
      diag.line = Number(pyMatch[2]);
      const msg = lines[lines.length - 1] ?? "Unknown";
      diag.cause = msg;
      diag.fix = `Fix the exception in ${pyMatch[1]} line ${pyMatch[2]}.`;
      break;
    }
    // Test failure
    if (line.includes("AssertionError") || line.includes("assert")) {
      diag.type = "Test";
      diag.cause = "Assertion failed.";
      diag.fix = "Check expected vs actual values in the test.";
      break;
    }
    // Syntax error
    if (line.includes("SyntaxError") || line.includes("ParseError")) {
      diag.type = "Syntax";
      diag.cause = "Syntax error in source code.";
      diag.fix = "Fix the syntax issue indicated in the error.";
      break;
    }
  }

  return [
    `Diagnosis: ${diag.type}`,
    diag.file ? `File:    ${diag.file}` : "",
    diag.line ? `Line:    ${diag.line}` : "",
    `Cause:   ${diag.cause}`,
    `Fix:     ${diag.fix}`,
  ].filter(Boolean).join("\n");
}
