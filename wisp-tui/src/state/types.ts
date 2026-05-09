// WebSocket message protocol types — matches Wisp Python server

export interface ServerMessage {
  type: string;
  phase?: 'thinking' | 'content';
  text?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: string;
  duration_ms?: number;
  call_id?: string;
  reason?: string;
  message?: string;
  recoverable?: boolean;
  level?: 'info' | 'warn' | 'error';
  session_id?: string;
  turns?: number;
}

export interface ClientMessage {
  type: string;
  content?: string;
  model?: string;
  session_id?: string;
  show_thinking?: boolean;
  id?: string;
  approved?: boolean;
  reason?: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  model: string;
  workspace: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface UIMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  toolCalls?: ToolCallItem[];
}

export interface ToolCallItem {
  name: string;
  args: Record<string, unknown>;
  result?: string;
  durationMs?: number;
}

export interface ApprovalRequest {
  callId: string;
  toolName: string;
  args: Record<string, unknown>;
  reason: string;
}

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error';

export type ViewMode = 'session-picker' | 'chat';

export interface AppState {
  connection: ConnectionState;
  connectionError: string | null;
  serverUrl: string;
  sessions: SessionSummary[];
  sessionId: string | null;
  messages: UIMessage[];
  isStreaming: boolean;
  approvalPending: ApprovalRequest | null;
  inputValue: string;
  showThinking: boolean;
  selectedModel: string;
  viewMode: ViewMode;
}

let _msgCounter = 0;
function nextMsgId(): string {
  _msgCounter += 1;
  return `msg-${_msgCounter}`;
}

export function createInitialState(serverUrl: string): AppState {
  return {
    connection: 'disconnected',
    connectionError: null,
    serverUrl,
    sessions: [],
    sessionId: null,
    messages: [],
    isStreaming: false,
    approvalPending: null,
    inputValue: '',
    showThinking: false,
    selectedModel: '',
    viewMode: 'session-picker',
  };
}

// ── Actions ──

export type Action =
  | { type: 'CONNECT' }
  | { type: 'DISCONNECT' }
  | { type: 'CONNECTION_ERROR'; error: string }
  | { type: 'SET_SESSIONS'; sessions: SessionSummary[] }
  | { type: 'SELECT_SESSION'; sessionId: string; messages?: UIMessage[] }
  | { type: 'NEW_SESSION' }
  | { type: 'SUBMIT_PROMPT'; content: string }
  | { type: 'RECEIVE_TOKEN'; phase: 'thinking' | 'content'; text: string }
  | { type: 'RECEIVE_TOOL_CALL'; name: string; arguments: Record<string, unknown> }
  | { type: 'RECEIVE_TOOL_RESULT'; name: string; result: string; durationMs?: number }
  | { type: 'RECEIVE_APPROVAL_REQUEST'; callId: string; name: string; arguments: Record<string, unknown>; reason: string }
  | { type: 'RECEIVE_DONE'; sessionId: string }
  | { type: 'RECEIVE_COMPLETE'; sessionId: string }
  | { type: 'RECEIVE_ERROR'; message: string }
  | { type: 'RECEIVE_STATUS'; message: string; level: string }
  | { type: 'APPROVE_TOOL'; callId: string }
  | { type: 'DENY_TOOL'; callId: string }
  | { type: 'SET_MODEL'; model: string }
  | { type: 'TOGGLE_THINKING' }
  | { type: 'CLEAR_CHAT' }
  | { type: 'SET_INPUT'; value: string }
  | { type: 'INTERRUPT' };

export function appReducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'CONNECT':
      return { ...state, connection: 'connected', connectionError: null };

    case 'DISCONNECT':
      return { ...state, connection: 'disconnected', isStreaming: false };

    case 'CONNECTION_ERROR':
      return { ...state, connection: 'error', connectionError: action.error };

    case 'SET_SESSIONS':
      return { ...state, sessions: action.sessions };

    case 'SELECT_SESSION':
      return {
        ...state,
        sessionId: action.sessionId,
        messages: action.messages || [],
        viewMode: 'chat',
      };

    case 'NEW_SESSION':
      return {
        ...state,
        sessionId: null,
        messages: [],
        viewMode: 'chat',
        approvalPending: null,
        isStreaming: false,
      };

    case 'SUBMIT_PROMPT': {
      const userMsg: UIMessage = {
        id: nextMsgId(),
        role: 'user',
        content: action.content,
      };
      const assistantMsg: UIMessage = {
        id: nextMsgId(),
        role: 'assistant',
        content: '',
        toolCalls: [],
      };
      return {
        ...state,
        messages: [...state.messages, userMsg, assistantMsg],
        isStreaming: true,
        inputValue: '',
      };
    }

    case 'RECEIVE_TOKEN': {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        if (action.phase === 'thinking') {
          msgs[msgs.length - 1] = {
            ...last,
            thinking: (last.thinking || '') + action.text,
          };
        } else {
          msgs[msgs.length - 1] = {
            ...last,
            content: last.content + action.text,
          };
        }
      }
      return { ...state, messages: msgs };
    }

    case 'RECEIVE_TOOL_CALL': {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        const tc: ToolCallItem = { name: action.name, args: action.arguments };
        msgs[msgs.length - 1] = {
          ...last,
          toolCalls: [...(last.toolCalls || []), tc],
        };
      }
      return { ...state, messages: msgs };
    }

    case 'RECEIVE_TOOL_RESULT': {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant' && last.toolCalls) {
        const tcs = [...last.toolCalls];
        const idx = tcs.length - 1;
        if (idx >= 0 && !tcs[idx].result) {
          tcs[idx] = {
            ...tcs[idx],
            result: action.result,
            durationMs: action.durationMs,
          };
        }
        msgs[msgs.length - 1] = { ...last, toolCalls: tcs };
      }
      return { ...state, messages: msgs };
    }

    case 'RECEIVE_APPROVAL_REQUEST':
      return {
        ...state,
        approvalPending: {
          callId: action.callId,
          toolName: action.name,
          args: action.arguments,
          reason: action.reason,
        },
      };

    case 'RECEIVE_DONE':
      return { ...state, isStreaming: false };

    case 'RECEIVE_COMPLETE':
      return { ...state, isStreaming: false };

    case 'RECEIVE_ERROR':
      return {
        ...state,
        isStreaming: false,
        messages: [
          ...state.messages,
          { id: nextMsgId(), role: 'system', content: `Error: ${action.message}` },
        ],
      };

    case 'RECEIVE_STATUS': {
      const msgs = [...state.messages];
      // Don't add debug-level compaction messages to chat
      if (action.level !== 'debug') {
        msgs.push({
          id: nextMsgId(),
          role: 'system',
          content: action.message,
        });
      }
      return { ...state, messages: msgs };
    }

    case 'APPROVE_TOOL':
    case 'DENY_TOOL':
      return { ...state, approvalPending: null };

    case 'SET_MODEL':
      return { ...state, selectedModel: action.model };

    case 'TOGGLE_THINKING':
      return { ...state, showThinking: !state.showThinking };

    case 'CLEAR_CHAT':
      return { ...state, messages: [] };

    case 'SET_INPUT':
      return { ...state, inputValue: action.value };

    case 'INTERRUPT':
      return { ...state, isStreaming: false, approvalPending: null };

    default:
      return state;
  }
}
