/** UnifiedStore — single SQLite persistence layer for all Wisp state.
 *  Features: WAL mode, connection pooling, migration versioning, backup/restore.
 */

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const _require = createRequire(import.meta.url);

// Local type alias for better-sqlite3 Database to avoid ESM/CJS interop issues
interface SqliteDatabase {
  exec(sql: string): void;
  prepare(sql: string): SqliteStatement;
  close(): void;
  pragma(source: string, options?: { simple?: boolean }): unknown;
  backup(destination: string): Promise<{ totalPages: number; remainingPages: number }>;
}

interface SqliteStatement {
  run(...params: unknown[]): { changes: number; lastInsertRowid: number | bigint };
  get(...params: unknown[]): Record<string, unknown> | undefined;
  all(...params: unknown[]): Record<string, unknown>[];
}

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

const CURRENT_SCHEMA_VERSION = 1;

export class UnifiedStore {
  dbPath: string;
  private _db: import("better-sqlite3").Database | null = null;
  private _closed = false;
  private _inMemoryIdempotency = new Map<string, { events: Array<Record<string, unknown>>; timestamp: number }>();

  constructor(dbPath: string) {
    this.dbPath = dbPath;
    const dir = path.dirname(dbPath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    this._open();
    this._runMigrations();
  }

  /** Open a persistent connection with WAL mode. */
  private _open(): void {
    if (this._closed || this._db) return;
    try {
      const Database = _require("better-sqlite3");
      const db = new Database(this.dbPath);
      this._db = db;
      // WAL mode enables concurrent reads while a write is in progress
      db.exec("PRAGMA journal_mode = WAL;");
      db.exec("PRAGMA synchronous = NORMAL;");
      db.exec("PRAGMA foreign_keys = ON;");
    } catch {
      this._db = null;
    }
  }

  /** Close the persistent connection. */
  close(): void {
    this._closed = true;
    try {
      this._db?.close();
    } catch {
      // ignore
    }
    this._db = null;
  }

  /** Execute a function with the persistent DB connection, reopening if needed. */
  private _withDb<T>(fn: (db: import("better-sqlite3").Database) => T): T | null {
    if (!this._db || this._closed) {
      this._open();
    }
    if (!this._db) return null;
    try {
      return fn(this._db);
    } catch {
      return null;
    }
  }

  /** Run schema migrations with versioning. */
  private _runMigrations(): void {
    this._withDb((db) => {
      // Migration tracking table
      db.exec(`
        CREATE TABLE IF NOT EXISTS _schema_version (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          version INTEGER NOT NULL DEFAULT 0,
          migrated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
      `);

      const row = db.prepare("SELECT version FROM _schema_version WHERE id = 1").get() as { version: number } | undefined;
      let version = row?.version ?? 0;

      if (version < 1) {
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
          CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace);
          CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
        `);
        version = 1;
      }

      db.prepare(
        "INSERT INTO _schema_version (id, version, migrated_at) VALUES (1, ?, datetime('now')) ON CONFLICT(id) DO UPDATE SET version=excluded.version, migrated_at=excluded.migrated_at"
      ).run(version);
    });
  }

  /** Get current schema version. */
  schemaVersion(): number {
    return this._withDb((db) => {
      const row = db.prepare("SELECT version FROM _schema_version WHERE id = 1").get() as { version: number } | undefined;
      return row?.version ?? 0;
    }) ?? 0;
  }

  /** Backup the database to a file. Returns the backup path. */
  async backup(destination?: string): Promise<string> {
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const dest = destination ?? `${this.dbPath}.backup-${ts}.db`;
    return new Promise((resolve, reject) => {
      this._withDb((db) => {
        try {
          // better-sqlite3 backup() is available at runtime but not in all type defs
          (db as unknown as { backup(destination: string): Promise<{ totalPages: number; remainingPages: number }> }).backup(dest)
            .then(() => resolve(dest))
            .catch((e: unknown) => reject(e));
        } catch (e) {
          reject(e);
        }
      });
      if (!this._db) reject(new Error("Database not available"));
    });
  }

  /** Restore the database from a backup file. */
  restore(backupPath: string): void {
    if (!fs.existsSync(backupPath)) {
      throw new Error(`Backup not found: ${backupPath}`);
    }
    this.close();
    fs.copyFileSync(backupPath, this.dbPath);
    this._closed = false;
    this._open();
    this._runMigrations();
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
    this._withDb((db) => {
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
    });
  }

  loadSession(sessionId: string): Record<string, unknown> | null {
    return this._withDb((db) => {
      const row = db.prepare("SELECT * FROM sessions WHERE id = ?").get(sessionId) as SessionRecord | undefined;
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
    });
  }

  listSessions(): Array<Record<string, unknown>> {
    const rows = this._withDb((db) => {
      return db.prepare("SELECT * FROM sessions ORDER BY updated_at DESC").all() as unknown as SessionRecord[];
    });
    if (!rows) return [];
    return rows.map((r) => ({
      id: r.id, model: r.model, workspace: r.workspace, title: r.title,
      messages: JSON.parse(r.messages),
      compaction_history: JSON.parse(r.compaction_history),
      created_at: r.created_at, updated_at: r.updated_at,
    }));
  }

  deleteSession(sessionId: string): void {
    this._withDb((db) => {
      db.prepare("DELETE FROM sessions WHERE id = ?").run(sessionId);
    });
  }

  getIdempotency(key: string): { events: Array<Record<string, unknown>>; timestamp: number } | null {
    const result = this._withDb((db) => {
      const row = db.prepare("SELECT result, created_at FROM idempotency WHERE key = ?").get(key) as { result: string; created_at: number } | undefined;
      if (!row) return null;
      const age = Date.now() - row.created_at;
      if (age > 300_000) return null; // 5 min TTL
      return JSON.parse(row.result) as { events: Array<Record<string, unknown>>; timestamp: number };
    });
    return result ?? this._inMemoryIdempotency.get(key) ?? null;
  }

  setIdempotency(key: string, result: { events: Array<Record<string, unknown>>; timestamp: number }): void {
    this._withDb((db) => {
      const stmt = db.prepare("INSERT INTO idempotency (key, result, created_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET result=excluded.result, created_at=excluded.created_at");
      stmt.run(key, JSON.stringify(result), Date.now());
    });
    this._inMemoryIdempotency.set(key, result);
    if (this._inMemoryIdempotency.size > 256) {
      const first = this._inMemoryIdempotency.keys().next().value;
      if (first) this._inMemoryIdempotency.delete(first);
    }
  }
}
