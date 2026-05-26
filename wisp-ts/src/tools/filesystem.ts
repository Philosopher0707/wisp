/** Filesystem tools for Wisp TS */

import fs from "node:fs";
import path from "node:path";

const MAX_READ_SIZE = 50 * 1024 * 1024;
const MAX_WRITE_SIZE = 100 * 1024 * 1024;
const MAX_OLD_TEXT_LENGTH = 50000;

function _resolvePath(filePath: string, workspace: string): string {
  if (path.isAbsolute(filePath)) {
    const resolved = path.resolve(filePath);
    const ws = path.resolve(workspace);
    if (!resolved.startsWith(ws)) throw new Error(`Path ${filePath} escapes workspace`);
    return resolved;
  }
  return path.resolve(workspace, filePath);
}

export function toolReadFile(filePath: string, workspace: string, offset = 0, limit = 1000000): string {
  const fullPath = _resolvePath(filePath, workspace);
  if (!fs.existsSync(fullPath)) throw new Error(`File not found: ${filePath}`);
  const stat = fs.statSync(fullPath);
  if (!stat.isFile()) throw new Error(`Not a file: ${filePath}`);
  if (stat.size > MAX_READ_SIZE) throw new Error(`File too large: ${filePath}`);

  const content = fs.readFileSync(fullPath, "utf-8");
  const lines = content.split("\n");
  const total = lines.length;
  const selected = lines.slice(offset, offset + limit);
  const shown = Math.min(offset + limit, total);
  const header = `--- FILE: ${filePath} | LINES: ${total} | SHOWING: ${offset + 1}-${shown} ---\n`;
  return header + selected.join("\n");
}

export function toolWriteFile(filePath: string, workspace: string, content: string): Record<string, unknown> {
  const fullPath = _resolvePath(filePath, workspace);
  if (content.length > MAX_WRITE_SIZE) throw new Error(`Content too large: ${content.length} bytes`);

  const oldContent = fs.existsSync(fullPath) ? fs.readFileSync(fullPath, "utf-8") : null;
  fs.mkdirSync(path.dirname(fullPath), { recursive: true });
  fs.writeFileSync(fullPath, content, "utf-8");

  let diff = "";
  if (oldContent !== null && oldContent !== content) {
    const oldLines = oldContent.split("\n");
    const newLines = content.split("\n");
    diff = _generateSimpleDiff(oldLines, newLines);
  } else if (oldContent === null) {
    diff = content.split("\n").map((l, i) => `+${i + 1} ${l}`).join("\n");
  }

  return {
    status: "ok",
    data: `✓ Wrote ${content.length} bytes to ${filePath}`,
    metadata: { path: filePath, size: content.length, bytes_written: content.length, diff },
  };
}

export function toolEditFile(filePath: string, workspace: string, oldText: string, newText: string): Record<string, unknown> {
  if (oldText.length > MAX_OLD_TEXT_LENGTH) throw new Error("old_text too large");
  const fullPath = _resolvePath(filePath, workspace);
  if (!fs.existsSync(fullPath)) throw new Error(`File not found: ${filePath}`);

  const content = fs.readFileSync(fullPath, "utf-8");
  const idx = content.indexOf(oldText);
  if (idx === -1) throw new Error(`old_text not found in ${filePath}`);
  if (content.indexOf(oldText, idx + 1) !== -1) throw new Error(`old_text is not unique in ${filePath}`);

  const newContent = content.slice(0, idx) + newText + content.slice(idx + oldText.length);
  fs.writeFileSync(fullPath, newContent, "utf-8");

  const oldLines = content.split("\n");
  const newLines = newContent.split("\n");
  const diff = _generateSimpleDiff(oldLines, newLines);

  return {
    status: "ok",
    data: `✓ Edited ${filePath} — ${oldText.length} chars replaced with ${newText.length} chars`,
    metadata: { path: filePath, old_length: oldText.length, new_length: newText.length, diff },
  };
}

export function toolEditFileMulti(filePath: string, workspace: string, edits: Array<{ old_text: string; new_text: string }>): Record<string, unknown> {
  const fullPath = _resolvePath(filePath, workspace);
  if (!fs.existsSync(fullPath)) throw new Error(`File not found: ${filePath}`);
  let content = fs.readFileSync(fullPath, "utf-8");

  for (const edit of edits) {
    const idx = content.indexOf(edit.old_text);
    if (idx === -1) throw new Error(`old_text not found in ${filePath}`);
    if (content.indexOf(edit.old_text, idx + 1) !== -1) throw new Error(`old_text is not unique`);
    content = content.slice(0, idx) + edit.new_text + content.slice(idx + edit.old_text.length);
  }

  fs.writeFileSync(fullPath, content, "utf-8");
  return {
    status: "ok",
    data: `✓ Multi-edited ${filePath} — ${edits.length} edits applied`,
    metadata: { path: filePath, edits_applied: edits.length },
  };
}

export function toolListFiles(dirPath: string, workspace: string, pattern = "*"): string {
  const fullPath = _resolvePath(dirPath, workspace);
  if (!fs.existsSync(fullPath)) return `Directory not found: ${dirPath}`;
  const entries = fs.readdirSync(fullPath, { withFileTypes: true });
  const lines: string[] = [];
  for (const entry of entries.slice(0, 500)) {
    if (entry.isDirectory()) lines.push(`📁 ${entry.name}/`);
    else lines.push(`📄 ${entry.name}`);
  }
  return lines.join("\n") || "(empty directory)";
}

function _generateSimpleDiff(oldLines: string[], newLines: string[]): string {
  const maxLen = Math.max(oldLines.length, newLines.length);
  const parts: string[] = [];
  for (let i = 0; i < maxLen; i++) {
    const old = oldLines[i];
    const neu = newLines[i];
    if (old === undefined) parts.push(`+${i + 1} ${neu}`);
    else if (neu === undefined) parts.push(`-${i + 1} ${old}`);
    else if (old !== neu) {
      parts.push(`-${i + 1} ${old}`);
      parts.push(`+${i + 1} ${neu}`);
    }
  }
  return parts.slice(0, 100).join("\n"); // cap diff lines
}
