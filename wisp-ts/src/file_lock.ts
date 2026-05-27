/** Advisory file locking — in-process with JSON metadata for visibility.

Node.js lacks built-in advisory file locking (no fcntl equivalent).
This implementation uses a global in-process lock map plus JSON
metadata files for introspection. Suitable for single-process agents.
*/

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const DEFAULT_LOCK_TIMEOUT = 300; // 5 minutes

const _processLocks = new Map<string, string>(); // filepath → agent_id
const _processLockMutex = new Map<string, Promise<unknown>>();

function _agentId(): string {
  return `wisp-${crypto.randomBytes(4).toString("hex")}`;
}

function _readMeta(lockPath: string): { agent: string; since: string; expires: string } | null {
  try {
    const raw = fs.readFileSync(lockPath, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function _writeMeta(lockPath: string, agentId: string, timeoutSec: number): void {
  const now = new Date();
  const data = {
    agent: agentId,
    since: now.toISOString(),
    expires: new Date(now.getTime() + timeoutSec * 1000).toISOString(),
  };
  fs.writeFileSync(lockPath, JSON.stringify(data, null, 2), "utf-8");
}

async function _acquireMutex(lockPath: string): Promise<() => void> {
  while (_processLockMutex.has(lockPath)) {
    await _processLockMutex.get(lockPath);
  }
  let resolve: () => void;
  const p = new Promise<void>((r) => { resolve = r; });
  _processLockMutex.set(lockPath, p);
  return () => {
    _processLockMutex.delete(lockPath);
    resolve();
  };
}

export class FileLock {
  workspace: string;
  lockDir: string;
  agentId: string;

  constructor(workspace: string, agentId?: string) {
    this.workspace = path.resolve(workspace);
    this.lockDir = path.join(this.workspace, ".wisp", "locks");
    this.agentId = agentId ?? _agentId();
    if (!fs.existsSync(this.lockDir)) {
      fs.mkdirSync(this.lockDir, { recursive: true });
    }
  }

  acquire(filepath: string, timeoutSec = DEFAULT_LOCK_TIMEOUT): boolean {
    const lockPath = this._lockPath(filepath);
    const releaseMutex = _acquireMutex(lockPath);
    // Fire-and-forget mutex (simplified — in real code would await)
    // For sync API, we do best-effort

    const data = _readMeta(lockPath);
    if (data && data.agent !== this.agentId) {
      const expires = new Date(data.expires).getTime();
      if (expires > Date.now()) return false;
    }
    _processLocks.set(filepath, this.agentId);
    _writeMeta(lockPath, this.agentId, timeoutSec);
    return true;
  }

  async acquireAsync(filepath: string, timeoutSec = DEFAULT_LOCK_TIMEOUT): Promise<boolean> {
    const lockPath = this._lockPath(filepath);
    const release = await _acquireMutex(lockPath);
    try {
      const data = _readMeta(lockPath);
      if (data && data.agent !== this.agentId) {
        const expires = new Date(data.expires).getTime();
        if (expires > Date.now()) return false;
      }
      _processLocks.set(filepath, this.agentId);
      _writeMeta(lockPath, this.agentId, timeoutSec);
      return true;
    } finally {
      release();
    }
  }

  release(filepath: string): void {
    const lockPath = this._lockPath(filepath);
    const data = _readMeta(lockPath);
    if (data && data.agent === this.agentId) {
      try { fs.unlinkSync(lockPath); } catch { /* ignore */ }
      _processLocks.delete(filepath);
    }
  }

  isLocked(filepath: string): boolean {
    const lockPath = this._lockPath(filepath);
    const data = _readMeta(lockPath);
    if (!data) return false;
    const expires = new Date(data.expires).getTime();
    return expires > Date.now();
  }

  lockInfo(filepath: string): Record<string, unknown> | null {
    if (!this.isLocked(filepath)) return null;
    return _readMeta(this._lockPath(filepath));
  }

  listActiveLocks(): Array<Record<string, unknown>> {
    const locks: Array<Record<string, unknown>> = [];
    if (!fs.existsSync(this.lockDir)) return locks;
    for (const entry of fs.readdirSync(this.lockDir, { withFileTypes: true })) {
      if (!entry.isFile() || !entry.name.endsWith(".lock")) continue;
      const data = _readMeta(path.join(this.lockDir, entry.name));
      if (data) {
        const expires = new Date(data.expires).getTime();
        if (expires > Date.now()) {
          locks.push({ ...data, _file: entry.name.replace(/\.lock$/, "").replace(/__/g, "/") });
        }
      }
    }
    return locks;
  }

  releaseAll(): void {
    if (!fs.existsSync(this.lockDir)) return;
    for (const entry of fs.readdirSync(this.lockDir, { withFileTypes: true })) {
      if (!entry.isFile() || !entry.name.endsWith(".lock")) continue;
      const lockPath = path.join(this.lockDir, entry.name);
      const data = _readMeta(lockPath);
      if (data && data.agent === this.agentId) {
        try { fs.unlinkSync(lockPath); } catch { /* ignore */ }
      }
    }
    // Also clear in-process locks for this agent
    for (const [fp, agent] of _processLocks.entries()) {
      if (agent === this.agentId) _processLocks.delete(fp);
    }
  }

  private _lockPath(filepath: string): string {
    const p = path.resolve(filepath);
    let rel: string;
    try {
      rel = path.relative(this.workspace, p);
    } catch {
      rel = p.replace(/[/\\]/g, "__");
    }
    const safe = rel.replace(/[/\\]/g, "__");
    return path.join(this.lockDir, `${safe}.lock`);
  }
}
