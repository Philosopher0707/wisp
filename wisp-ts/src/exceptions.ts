/** Shared exceptions for Wisp TS */

export class ExitREPL extends Error {
  constructor(message = "Graceful REPL termination") {
    super(message);
    this.name = "ExitREPL";
  }
}

export class WispError extends Error {
  constructor(message: string, public readonly recoverable = true) {
    super(message);
    this.name = "WispError";
  }
}
