/** UnifiedStore — single SQLite persistence layer for all Wisp state. */

import fs from "node:fs";
import path from "node:path";

export interface SessionRecord {
  id: string;
  model: string;
  workspace: string;
  title: string;
  messages: string;
  compaction_history: string;
  created_at: string;
  updated_at: string;
}

export class UnifiedStore {
  dbPath: string;

  constructor(dbPath: string) {
    this.dbPath = dbPath;
    const dir = path.dirname(dbPath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    this._initSchema();
  }

  private _db(): any {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const Database = require("better-sqlite3");
      return new Database(this.dbPath);
    } catch {
      throw new Error("SQLite not available. Install better-sqlite3 for persistence.");
    }
  }

  private _initSchema(): void {
    try {
      const db = this._db();
      db.exec(`
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY,
          model TEXT NOT NULL,
          workspace TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          messages TEXT NOT NULL DEFAULT '[]',
          compaction_history TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          content TEXT NOT NULL,
          importance INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS idempotency (
          key TEXT PRIMARY KEY,
          result TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency(created_at);
      `);
      db.close();
    } catch {
      // SQLite not available — graceful degradation
    }
  }

  createSession(sessionId: string, model: string, workspace: string, title = ""): Record<string, unknown> {
    const now = new Date().toISOString();
    const session = {
      id: sessionId, model, workspace, title,
      messages: [], compaction_history: [],
      created_at: now, updated_at: now,
    };
    this.saveSession(session);
    return session;
  }

  saveSession(session: Record<string, unknown>): void {
    try {
      const db = this._db();
      const stmt = db.prepare(`
        INSERT INTO sessions (id, model, workspace, title, messages, compaction_history, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          model=excluded.model, workspace=excluded.workspace, title=excluded.title,
          messages=excluded.messages, compaction_history=excluded.compaction_history,
          updated_at=excluded.updated_at
      `);
      stmt.run(
        session.id,
        session.model ?? "",
        session.workspace ?? "",
        session.title ?? "",
        JSON.stringify(session.messages ?? []),
        JSON.stringify(session.compaction_history ?? []),
        session.created_at ?? new Date().toISOString(),
        session.updated_at ?? new Date().toISOString(),
      );
      db.close();
    } catch {
      // graceful degradation
    }
  }

  loadSession(sessionId: string): Record<string, unknown> | null {
    try {
      const db = this._db();
      const row = db.prepare("SELECT * FROM sessions WHERE id = ?").get(sessionId) as SessionRecord | undefined;
      db.close();
      if (!row) return null;
      return {
        id: row.id,
        model: row.model,
        workspace: row.workspace,
        title: row.title,
        messages: JSON.parse(row.messages),
        compaction_history: JSON.parse(row.compaction_history),
        created_at: row.created_at,
        updated_at: row.updated_at,
      };
    } catch {
      return null;
    }
  }

  listSessions(): Array<Record<string, unknown>> {
    try {
      const db = this._db();
      const rows = db.prepare("SELECT * FROM sessions ORDER BY updated_at DESC").all() as SessionRecord[];
      db.close();
      return rows.map((r) => ({
        id: r.id, model: r.model, workspace: r.workspace, title: r.title,
        messages: JSON.parse(r.messages),
        compaction_history: JSON.parse(r.compaction_history),
        created_at: r.created_at, updated_at: r.updated_at,
      }));
    } catch {
      return [];
    }
  }

  deleteSession(sessionId: string): void {
    try {
      const db = this._db();
      db.prepare("DELETE FROM sessions WHERE id = ?").run(sessionId);
      db.close();
    } catch {
      // graceful
    }
  }

  private _inMemoryIdempotency = new Map<string, { events: Array<Record<string, unknown>>; timestamp: number }>();

  getIdempotency(key: string): { events: Array<Record<string, unknown>>; timestamp: number } | null {
    try {
      const db = this._db();
      const row = db.prepare("SELECT result, created_at FROM idempotency WHERE key = ?").get(key) as { result: string; created_at: number } | undefined;
      db.close();
      if (!row) return this._inMemoryIdempotency.get(key) ?? null;
      const age = Date.now() - row.created_at;
      if (age > 300_000) return null; // 5 min TTL
      return JSON.parse(row.result) as { events: Array<Record<string, unknown>>; timestamp: number };
    } catch {
      return this._inMemoryIdempotency.get(key) ?? null;
    }
  }

  setIdempotency(key: string, result: { events: Array<Record<string, unknown>>; timestamp: number }): void {
    try {
      const db = this._db();
      const stmt = db.prepare("INSERT INTO idempotency (key, result, created_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET result=excluded.result, created_at=excluded.created_at");
      stmt.run(key, JSON.stringify(result), Date.now());
      db.close();
    } catch {
      // graceful — fallback to in-memory
    }
    this._inMemoryIdempotency.set(key, result);
    if (this._inMemoryIdempotency.size > 256) {
      const first = this._inMemoryIdempotency.keys().next().value;
      if (first) this._inMemoryIdempotency.delete(first);
    }
  }
}
