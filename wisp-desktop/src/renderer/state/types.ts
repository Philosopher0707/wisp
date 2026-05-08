// ── Types ──

export interface SessionSummary {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  msg_count: number;
}

export interface ChatSummary {
  id: string;
  title: string;
  timestamp: string;
  pinned: boolean;
  externalLink?: boolean;
}

export interface ProjectFolder {
  id: string;
  name: string;
  chats: ChatSummary[];
}

export interface ToolCallItem {
  name: string;
  args: Record<string, unknown>;
  result?: string;
  durationMs?: number;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  toolCalls?: ToolCallItem[];
  timestamp?: number;
  tokens?: number;
}

export interface ApprovalRequest {
  callId: string;
  toolName: string;
  args: Record<string, unknown>;
  reason: string;
}

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface AppState {
  serverUrl: string;
  apiKey: string;
  connection: ConnectionState;
  connectionError: string | null;
  sidebarExpandedProjects: Set<string>;
  pinnedSessionIds: Set<string>;
  pinnedChats: ChatSummary[];
  projects: ProjectFolder[];
  sessions: SessionSummary[];
  sessionsVersion: number;
  sessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  approvalPending: ApprovalRequest | null;
  inputValue: string;
  selectedModel: string;
  reasoningLevel: 'low' | 'medium' | 'high';
  selectedProject: string | null;
  activeDropdown: string | null;
  permissionMode: 'full' | 'ask_all' | 'auto_edit' | 'read_only';
  showThinking: boolean;
  sidebarCollapsed: boolean;
  availableModels: string[];
  rightPanelOpen: boolean;
  workspacePath: string;
  uiOverlay: string | null;
  convSearchActive: boolean;
  systemPrompt: string;
  selectedFilePath: string | null;
  gitCommitBanner: { branch: string; changedFiles: string[] } | null;
  planMode: boolean;
  pendingPlan: string | null;
}

// ── Actions ──

export type Action =
  | { type: 'SET_CONNECTION'; status: ConnectionState; error?: string }
  | { type: 'SET_SESSIONS'; sessions: SessionSummary[] }
  | { type: 'SET_SESSION_ID'; id: string | null }
  | { type: 'SET_MESSAGES'; messages: Message[] }
  | { type: 'TOGGLE_PROJECT_FOLDER'; projectId: string }
  | { type: 'SELECT_CHAT'; chatId: string }
  | { type: 'NEW_CHAT' }
  | { type: 'SET_INPUT'; value: string }
  | { type: 'SUBMIT_MESSAGE'; content: string }
  | { type: 'RECEIVE_TOKEN'; text: string; phase: 'thinking' | 'content' }
  | { type: 'RECEIVE_TOOL_CALL'; name: string; arguments: Record<string, unknown> }
  | { type: 'RECEIVE_TOOL_RESULT'; name: string; result: string; durationMs?: number }
  | { type: 'RECEIVE_APPROVAL_REQUEST'; callId: string; name: string; arguments: Record<string, unknown>; reason: string }
  | { type: 'RECEIVE_DONE' }
  | { type: 'RECEIVE_COMPLETE'; sessionId?: string }
  | { type: 'RECEIVE_ERROR'; message: string }
  | { type: 'RECEIVE_STATUS'; message: string; level: string }
  | { type: 'APPROVE_TOOL'; callId: string }
  | { type: 'DENY_TOOL'; callId: string }
  | { type: 'INTERRUPT' }
  | { type: 'SET_MODEL'; model: string }
  | { type: 'SET_REASONING'; level: 'low' | 'medium' | 'high' }
  | { type: 'OPEN_DROPDOWN'; id: string }
  | { type: 'CLOSE_DROPDOWN' }
  | { type: 'SET_PERMISSION_MODE'; mode: 'full' | 'ask_all' | 'auto_edit' | 'read_only' }
  | { type: 'CLEAR_CHAT' }
  | { type: 'TOGGLE_THINKING' }
  | { type: 'SET_PROJECT'; projectId: string | null }
  | { type: 'OPEN_OVERLAY'; overlay: string }
  | { type: 'CLOSE_OVERLAY' }
  | { type: 'TOGGLE_SIDEBAR' }
  | { type: 'TOGGLE_RIGHT_PANEL' }
  | { type: 'SET_WORKSPACE'; path: string }
  | { type: 'SET_MODELS'; models: string[] }
  | { type: 'TOGGLE_PIN_SESSION'; sessionId: string }
  | { type: 'TOGGLE_CONV_SEARCH' }
  | { type: 'SET_SYSTEM_PROMPT'; prompt: string }
  | { type: 'SELECT_FILE'; path: string }
  | { type: 'SHOW_GIT_BANNER'; branch: string; changedFiles: string[] }
  | { type: 'DISMISS_GIT_BANNER' }
  | { type: 'TOGGLE_PLAN_MODE' }
  | { type: 'RECEIVE_PLAN'; content: string }
  | { type: 'APPROVE_PLAN'; planContext: string }
  | { type: 'REJECT_PLAN' };

// ── Helpers ──

let _msgCounter = 0;
function nextMsgId(): string {
  _msgCounter += 1;
  return `msg-${_msgCounter}`;
}

export function createInitialState(overrides?: {
  serverUrl?: string;
  apiKey?: string;
  selectedModel?: string;
  pinnedSessionIds?: string[];
  systemPrompt?: string;
  permissionMode?: 'full' | 'ask_all' | 'auto_edit' | 'read_only';
}): AppState {
  return {
    serverUrl: overrides?.serverUrl || 'http://localhost:8000',
    apiKey: overrides?.apiKey || '',
    connection: 'disconnected',
    connectionError: null,
    sidebarExpandedProjects: new Set<string>(),
    pinnedSessionIds: new Set<string>(overrides?.pinnedSessionIds || []),
    pinnedChats: [],
    projects: [],
    sessions: [],
    sessionsVersion: 0,
    sessionId: null,
    messages: [],
    isStreaming: false,
    approvalPending: null,
    inputValue: '',
    selectedModel: overrides?.selectedModel || 'claude-sonnet-4-6',
    reasoningLevel: 'medium',
    selectedProject: null,
    activeDropdown: null,
    showThinking: false,
    sidebarCollapsed: false,
    availableModels: [],
    rightPanelOpen: false,
    workspacePath: '',
    uiOverlay: null,
    convSearchActive: false,
    systemPrompt: overrides?.systemPrompt || '',
    selectedFilePath: null,
    permissionMode: overrides?.permissionMode || 'full',
    gitCommitBanner: null,
    planMode: false,
    pendingPlan: null,
  };
}

// ── Reducer ──

export function appReducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_CONNECTION':
      return { ...state, connection: action.status, connectionError: action.error || null };

    case 'SET_SESSIONS':
      return { ...state, sessions: action.sessions };

    case 'SET_SESSION_ID':
      return { ...state, sessionId: action.id, inputValue: '' };

    case 'SET_MESSAGES':
      return { ...state, messages: action.messages };

    case 'TOGGLE_PROJECT_FOLDER': {
      const next = new Set(state.sidebarExpandedProjects);
      if (next.has(action.projectId)) next.delete(action.projectId);
      else next.add(action.projectId);
      return { ...state, sidebarExpandedProjects: next };
    }

    case 'SELECT_CHAT':
      return { ...state, currentChatId: action.chatId };

    case 'NEW_CHAT':
      return {
        ...state,
        sessionId: null,
        messages: [],
        isStreaming: false,
        approvalPending: null,
      };

    case 'SET_INPUT':
      return { ...state, inputValue: action.value };

    case 'SUBMIT_MESSAGE': {
      const userMsg: Message = {
        id: nextMsgId(),
        role: 'user',
        content: action.content,
        timestamp: Date.now(),
      };
      const assistantMsg: Message = {
        id: nextMsgId(),
        role: 'assistant',
        content: '',
        toolCalls: [],
        timestamp: Date.now(),
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
          msgs[msgs.length - 1] = { ...last, thinking: (last.thinking || '') + action.text };
        } else {
          msgs[msgs.length - 1] = { ...last, content: last.content + action.text };
        }
      }
      return { ...state, messages: msgs };
    }

    case 'RECEIVE_TOOL_CALL': {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        const tc: ToolCallItem = { name: action.name, args: action.arguments };
        msgs[msgs.length - 1] = { ...last, toolCalls: [...(last.toolCalls || []), tc] };
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
          tcs[idx] = { ...tcs[idx], result: action.result, durationMs: action.durationMs };
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

    case 'RECEIVE_COMPLETE': {
      let msgs = state.messages;
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        const totalChars = (last.content?.length || 0) + (last.thinking?.length || 0);
        const estimatedTokens = Math.max(1, Math.ceil(totalChars / 4));
        msgs = [...msgs.slice(0, -1), { ...last, tokens: estimatedTokens }];
      }
      return {
        ...state,
        isStreaming: false,
        sessionId: action.sessionId || state.sessionId,
        sessionsVersion: state.sessionsVersion + 1,
        messages: msgs,
      };
    }

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
      if (action.level === 'debug') return state;
      return {
        ...state,
        messages: [
          ...state.messages,
          { id: nextMsgId(), role: 'system', content: action.message },
        ],
      };
    }

    case 'APPROVE_TOOL':
    case 'DENY_TOOL':
      return { ...state, approvalPending: null };

    case 'INTERRUPT':
      return { ...state, isStreaming: false, approvalPending: null };

    case 'SET_MODEL':
      return { ...state, selectedModel: action.model, activeDropdown: null };

    case 'SET_REASONING':
      return { ...state, reasoningLevel: action.level, activeDropdown: null };

    case 'OPEN_DROPDOWN':
      return { ...state, activeDropdown: action.id };

    case 'CLOSE_DROPDOWN':
      return { ...state, activeDropdown: null };

    case 'SET_PERMISSION_MODE':
      return { ...state, permissionMode: action.mode };

    case 'CLEAR_CHAT':
      return { ...state, messages: [] };

    case 'TOGGLE_THINKING':
      return { ...state, showThinking: !state.showThinking };

    case 'SET_PROJECT':
      return { ...state, selectedProject: action.projectId };

    case 'OPEN_OVERLAY':
      return { ...state, uiOverlay: action.overlay };

    case 'CLOSE_OVERLAY':
      return { ...state, uiOverlay: null };

    case 'TOGGLE_SIDEBAR':
      return { ...state, sidebarCollapsed: !state.sidebarCollapsed };

    case 'SET_MODELS':
      return { ...state, availableModels: action.models };

    case 'TOGGLE_PIN_SESSION': {
      const nextPins = new Set(state.pinnedSessionIds);
      if (nextPins.has(action.sessionId)) nextPins.delete(action.sessionId);
      else nextPins.add(action.sessionId);
      return { ...state, pinnedSessionIds: nextPins };
    }

    case 'TOGGLE_RIGHT_PANEL':
      return { ...state, rightPanelOpen: !state.rightPanelOpen };

    case 'SET_WORKSPACE':
      return { ...state, workspacePath: action.path };

    case 'TOGGLE_CONV_SEARCH':
      return { ...state, convSearchActive: !state.convSearchActive };

    case 'SET_SYSTEM_PROMPT':
      return { ...state, systemPrompt: action.prompt };

    case 'SELECT_FILE':
      return { ...state, rightPanelOpen: true, selectedFilePath: action.path };

    case 'SHOW_GIT_BANNER':
      return { ...state, gitCommitBanner: { branch: action.branch, changedFiles: action.changedFiles } };

    case 'DISMISS_GIT_BANNER':
      return { ...state, gitCommitBanner: null };

    case 'TOGGLE_PLAN_MODE':
      return { ...state, planMode: !state.planMode };

    case 'RECEIVE_PLAN':
      return { ...state, pendingPlan: action.content, isStreaming: false };

    case 'APPROVE_PLAN':
      return { ...state, pendingPlan: null, planMode: false };

    case 'REJECT_PLAN':
      return { ...state, pendingPlan: null, planMode: false };

    default:
      return state;
  }
}
