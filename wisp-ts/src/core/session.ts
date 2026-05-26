/** Session aggregate + event types for event-sourced session management. */

export enum SessionEventType {
  USER_MESSAGE = "user_message",
  ASSISTANT_MESSAGE = "assistant_message",
  TOOL_CALL = "tool_call",
  TOOL_RESULT = "tool_result",
  COMPACTED = "compacted",
  ERROR = "error",
  DONE = "done",
}

export interface SessionEventPayload {
  content?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: string;
  duration_ms?: number;
  before_count?: number;
  after_count?: number;
  summary?: string;
  message?: string;
  recoverable?: boolean;
  turns?: number;
  reason?: string;
  tool_calls?: Record<string, unknown>[];
}

export class SessionEvent {
  constructor(
    public readonly eventType: SessionEventType,
    public readonly sequenceNum: number,
    public readonly payload: SessionEventPayload = {},
    public readonly timestamp: number = Date.now() / 1000
  ) {}

  static userMessage(seq: number, content: string): SessionEvent {
    return new SessionEvent(SessionEventType.USER_MESSAGE, seq, { content });
  }

  static assistantMessage(seq: number, content: string, toolCalls?: Record<string, unknown>[]): SessionEvent {
    return new SessionEvent(SessionEventType.ASSISTANT_MESSAGE, seq, { content, tool_calls: toolCalls });
  }

  static toolCall(seq: number, name: string, args: Record<string, unknown>): SessionEvent {
    return new SessionEvent(SessionEventType.TOOL_CALL, seq, { name, arguments: args });
  }

  static toolResult(seq: number, name: string, result: string, durationMs = 0): SessionEvent {
    return new SessionEvent(SessionEventType.TOOL_RESULT, seq, { name, result, duration_ms: durationMs });
  }

  static compacted(seq: number, beforeCount: number, afterCount: number, summary = ""): SessionEvent {
    return new SessionEvent(SessionEventType.COMPACTED, seq, { before_count: beforeCount, after_count: afterCount, summary });
  }

  static error(seq: number, message: string, recoverable = true): SessionEvent {
    return new SessionEvent(SessionEventType.ERROR, seq, { message, recoverable });
  }

  static done(seq: number, turns = 0, reason = "natural"): SessionEvent {
    return new SessionEvent(SessionEventType.DONE, seq, { turns, reason });
  }
}

export interface SessionMessage {
  role: string;
  content: string;
  tool_calls?: Record<string, unknown>[];
  tool_call_id?: string;
  name?: string;
}

export interface CompactionRecord {
  before_count: number;
  after_count: number;
  summary: string;
  timestamp: number;
}

export class Session {
  sessionId: string;
  model = "";
  workspace = "";
  messages: SessionMessage[] = [];
  compactionHistory: CompactionRecord[] = [];
  sequenceNum = 0;
  turnCount = 0;
  createdAt = Date.now() / 1000;
  updatedAt = Date.now() / 1000;

  constructor(sessionId: string, model = "", workspace = "") {
    this.sessionId = sessionId;
    this.model = model;
    this.workspace = workspace;
  }

  apply(event: SessionEvent): void {
    this.sequenceNum = Math.max(this.sequenceNum, event.sequenceNum);
    this.updatedAt = event.timestamp;

    switch (event.eventType) {
      case SessionEventType.USER_MESSAGE:
        this.messages.push({ role: "user", content: event.payload.content ?? "" });
        this.turnCount += 1;
        break;
      case SessionEventType.ASSISTANT_MESSAGE:
        this.messages.push({ role: "assistant", content: event.payload.content ?? "", tool_calls: event.payload.tool_calls });
        break;
      case SessionEventType.TOOL_RESULT:
        this.messages.push({ role: "tool", content: event.payload.result ?? "", name: event.payload.name });
        break;
      case SessionEventType.COMPACTED:
        this.compactionHistory.push({
          before_count: event.payload.before_count ?? 0,
          after_count: event.payload.after_count ?? 0,
          summary: event.payload.summary ?? "",
          timestamp: event.timestamp,
        });
        break;
      case SessionEventType.ERROR:
        this.messages.push({ role: "system", content: `[Error] ${event.payload.message ?? ""}` });
        break;
      case SessionEventType.DONE:
        break;
    }
  }

  replay(events: SessionEvent[]): void {
    this.messages = [];
    this.compactionHistory = [];
    this.sequenceNum = 0;
    this.turnCount = 0;
    for (const ev of [...events].sort((a, b) => a.sequenceNum - b.sequenceNum)) {
      this.apply(ev);
    }
  }

  toDict(): Record<string, unknown> {
    return {
      id: this.sessionId,
      model: this.model,
      workspace: this.workspace,
      messages: this.messages,
      compaction_history: this.compactionHistory,
      created_at: this.createdAt,
      updated_at: this.updatedAt,
    };
  }
}
