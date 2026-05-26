/** Event system for Wisp SDK — structured events emitted by the agent core.
 * All I/O is handled by transports that subscribe to these events.
 * The core itself is pure logic.
 */

export const EVENT_SCHEMA_VERSION = 1;

export enum EventType {
  THINKING = "thinking",
  TOOL_CALL = "tool_call",
  TOOL_RESULT = "tool_result",
  CONTENT = "content",
  ERROR = "error",
  DONE = "done",
  SYSTEM = "system",
  APPROVAL_REQUEST = "approval_request",
  STEERING_PAUSED = "steering_paused",
  STEERING_INJECT = "steering_inject",
  STEERING_RESUMED = "steering_resumed",
}

// Backward-compatible aliases
export const TYPE_THINKING = EventType.THINKING;
export const TYPE_TOOL_CALL = EventType.TOOL_CALL;
export const TYPE_TOOL_RESULT = EventType.TOOL_RESULT;
export const TYPE_CONTENT = EventType.CONTENT;
export const TYPE_ERROR = EventType.ERROR;
export const TYPE_DONE = EventType.DONE;
export const TYPE_SYSTEM = EventType.SYSTEM;
export const TYPE_APPROVAL_REQUEST = EventType.APPROVAL_REQUEST;
export const TYPE_STEERING_PAUSED = EventType.STEERING_PAUSED;
export const TYPE_STEERING_INJECT = EventType.STEERING_INJECT;
export const TYPE_STEERING_RESUMED = EventType.STEERING_RESUMED;

export interface AgentEventData {
  [key: string]: unknown;
}

export class AgentEvent {
  type: EventType | string;
  data: AgentEventData;
  timestamp: number;
  traceId: string;
  spanId: string;
  schemaVersion: number;

  constructor(
    type: EventType | string,
    data: AgentEventData = {},
    timestamp: number = performance.now(),
    traceId = "",
    spanId = "",
    schemaVersion = EVENT_SCHEMA_VERSION
  ) {
    this.type = type;
    this.data = data;
    this.timestamp = timestamp;
    this.traceId = traceId;
    this.spanId = spanId;
    this.schemaVersion = schemaVersion;
  }

  get text(): string {
    return typeof this.data.text === "string" ? this.data.text : "";
  }

  get toolName(): string {
    return typeof this.data.name === "string" ? this.data.name : "";
  }

  get isFinal(): boolean {
    return this.type === TYPE_DONE || this.type === TYPE_ERROR;
  }

  toDict(): Record<string, unknown> {
    const d: Record<string, unknown> = {
      type: String(this.type),
      data: this.data,
      timestamp: this.timestamp,
      schema_version: this.schemaVersion,
    };
    if (this.traceId) d.trace_id = this.traceId;
    if (this.spanId) d.span_id = this.spanId;
    return d;
  }

  static fromDict(data: Record<string, unknown>): AgentEvent {
    const evType = String(data.type || "");
    const evData = data.data as AgentEventData | undefined;
    const traceId = typeof data.trace_id === "string" ? data.trace_id : "";
    const spanId = typeof data.span_id === "string" ? data.span_id : "";
    const schemaVer = typeof data.schema_version === "number" ? data.schema_version : EVENT_SCHEMA_VERSION;

    if (evData !== undefined) {
      return new AgentEvent(
        evType,
        { ...evData },
        typeof data.timestamp === "number" ? data.timestamp : 0,
        traceId,
        spanId,
        schemaVer
      );
    }

    const flatData: AgentEventData = {};
    for (const [k, v] of Object.entries(data)) {
      if (!["type", "timestamp", "trace_id", "span_id", "schema_version"].includes(k)) {
        flatData[k] = v;
      }
    }
    return new AgentEvent(
      evType,
      flatData,
      typeof data.timestamp === "number" ? data.timestamp : 0,
      traceId,
      spanId,
      schemaVer
    );
  }
}

// ── Event normalizer ─────────────────────────────────────────────

export function normalizeEvent(event: unknown): AgentEvent {
  if (event instanceof AgentEvent) return event;
  if (event && typeof event === "object") {
    return AgentEvent.fromDict(event as Record<string, unknown>);
  }
  return new AgentEvent("unknown", { raw: event });
}

// Human-readable descriptions
const EVENT_DESCRIPTIONS: Record<string, string> = {
  [EventType.THINKING]: "Model reasoning trace",
  [EventType.TOOL_CALL]: "Tool invocation",
  [EventType.TOOL_RESULT]: "Tool execution result",
  [EventType.CONTENT]: "Assistant text response",
  [EventType.ERROR]: "Error occurred",
  [EventType.DONE]: "Turn complete",
  [EventType.SYSTEM]: "System notification",
  [EventType.APPROVAL_REQUEST]: "User approval required",
};

export function describeEventType(eventType: EventType | string): string {
  return EVENT_DESCRIPTIONS[String(eventType)] || "Unknown event";
}

// ── Trace context helpers ──────────────────────────────────────

let _traceId = "";
let _spanId = "";

export function setTraceContext(traceId: string, spanId: string): void {
  _traceId = traceId;
  _spanId = spanId;
}

function _traceCtx(): [string, string] {
  return [_traceId, _spanId];
}

function _makeEvent(eventType: EventType | string, data: AgentEventData): AgentEvent {
  const [tid, sid] = _traceCtx();
  return new AgentEvent(eventType, data, performance.now(), tid, sid, EVENT_SCHEMA_VERSION);
}

// ── Event builders (convenience factories) ──────────────────────

export function thinking(text: string): AgentEvent {
  return _makeEvent(TYPE_THINKING, { text });
}

export function toolCall(name: string, args: Record<string, unknown>): AgentEvent {
  return _makeEvent(TYPE_TOOL_CALL, { name, arguments: args });
}

export function toolResult(
  name: string,
  result: unknown,
  durationMs?: number,
  autoApproved = false,
  toolCallId?: string
): AgentEvent {
  const payload: AgentEventData = { name, result };
  if (durationMs !== undefined) payload.duration_ms = durationMs;
  if (autoApproved) payload.auto_approved = true;
  if (toolCallId !== undefined) payload.tool_call_id = toolCallId;
  return _makeEvent(TYPE_TOOL_RESULT, payload);
}

export function content(text: string): AgentEvent {
  return _makeEvent(TYPE_CONTENT, { text });
}

export function error(message: string, recoverable = true): AgentEvent {
  return _makeEvent(TYPE_ERROR, { message, recoverable });
}

export function done(sessionId: string, turns = 0, summary = "", reason = "natural"): AgentEvent {
  return _makeEvent(TYPE_DONE, { session_id: sessionId, turns, summary, reason });
}

export function system(message: string, level = "info"): AgentEvent {
  return _makeEvent(TYPE_SYSTEM, { message, level });
}

export function steeringPaused(reason = "User paused"): AgentEvent {
  return _makeEvent(TYPE_STEERING_PAUSED, { reason });
}

export function steeringResumed(): AgentEvent {
  return _makeEvent(TYPE_STEERING_RESUMED, {});
}

export function steeringFeedback(text: string): AgentEvent {
  return _makeEvent(TYPE_STEERING_INJECT, { text });
}

export function approvalRequest(toolName: string, args: Record<string, unknown>, reason = ""): AgentEvent {
  return _makeEvent(TYPE_APPROVAL_REQUEST, { name: toolName, arguments: args, reason });
}
