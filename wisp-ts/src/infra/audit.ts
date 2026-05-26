/** Immutable audit trail — append-only, hash-chained, tamper-evident log. */

import process from "node:process";
import os from "node:os";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const DEFAULT_AUDIT_PATH = path.join(os.homedir(), ".config", "wisp", "audit.jsonl");
const SENSITIVE_KEYS = new Set(["api_key", "token", "password", "secret", "ssh_key", "private_key"]);

export class AuditTrail {
  private _path: string;
  private _lastHash = "";
  private _entryCount = 0;

  constructor(customPath?: string) {
    this._path = customPath ?? (process.env.WISP_AUDIT_LOG || DEFAULT_AUDIT_PATH);
    const dir = path.dirname(this._path);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    this._initState();
  }

  private _initState(): void {
    if (!fs.existsSync(this._path)) return;
    const lines = fs.readFileSync(this._path, "utf-8").split("\n");
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const entry = JSON.parse(line);
        this._lastHash = entry._hash ?? "";
        this._entryCount++;
      } catch {
        // skip corrupted
      }
    }
  }

  private _redactValue(key: string, value: unknown): unknown {
    const keyLower = key.toLowerCase().replace(/-/g, "_");
    if (Array.from(SENSITIVE_KEYS).some((s) => keyLower.includes(s))) {
      if (typeof value === "string" && value.length > 4) return `${value.slice(0, 4)}***`;
      return "***";
    }
    return value;
  }

  record(action: string, options?: {
    actor?: string;
    key?: string;
    oldValue?: unknown;
    newValue?: unknown;
    metadata?: Record<string, unknown>;
  }): string {
    const entry: Record<string, unknown> = {
      timestamp: Date.now() / 1000,
      action,
      actor: options?.actor ?? "system",
      key: options?.key ?? null,
      old_value: this._redactValue(options?.key ?? "", options?.oldValue),
      new_value: this._redactValue(options?.key ?? "", options?.newValue),
      _prev_hash: this._lastHash,
    };
    if (options?.metadata) entry.metadata = options.metadata;

    const payload = JSON.stringify(entry, Object.keys(entry).sort());
    const hash = crypto.createHash("sha256").update(payload).digest("hex");
    entry._hash = hash;
    this._lastHash = hash;
    this._entryCount++;

    fs.appendFileSync(this._path, JSON.stringify(entry) + "\n");
    return hash;
  }

  verify(): number | null {
    if (!fs.existsSync(this._path)) return null;
    let prevHash = "";
    const lines = fs.readFileSync(this._path, "utf-8").split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      try {
        const entry = JSON.parse(line);
        if (entry._prev_hash !== prevHash) return i + 1;
        const storedHash = entry._hash;
        const verifyEntry = { ...entry };
        delete verifyEntry._hash;
        const payload = JSON.stringify(verifyEntry, Object.keys(verifyEntry).sort());
        const expected = crypto.createHash("sha256").update(payload).digest("hex");
        if (storedHash !== expected) return i + 1;
        prevHash = storedHash;
      } catch {
        return i + 1;
      }
    }
    return null;
  }
}
