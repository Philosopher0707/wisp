declare module "better-sqlite3" {
  class Database {
    constructor(filename: string, options?: Record<string, unknown>);
    exec(sql: string): void;
    prepare(sql: string): Statement;
    close(): void;
  }
  class Statement {
    run(...params: unknown[]): { changes: number; lastInsertRowid: number | bigint };
    get(...params: unknown[]): Record<string, unknown> | undefined;
    all(...params: unknown[]): Record<string, unknown>[];
  }
  export { Database, Statement };
}
