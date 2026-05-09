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
  structuredResult?: unknown;
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
  forkingMessageId: string | null;
  checkpoints: Checkpoint[];
  checkpointPanelOpen: boolean;
  subagentTasks: SubagentTask[];
  tokenUsagePercent: number;
  theme: ThemeMode;
  customThemePath: string | null;
  availableThemes: Array<{ name: string; path: string; isBuiltin: boolean }>;
  vimMode: boolean;
  contextFiles: ContextFile[];
  keybindings: Record<string, string>;
  inlineEdit: InlineEditState | null;
}

export interface InlineEditState {
  path: string;
  selection: string;
  newText: string | null;
  diff: string | null;
  isProcessing: boolean;
  error: string | null;
}

export type ThemeMode = 'dark' | 'light' | 'custom';

export interface ContextFile {
  path: string;
  loaded: boolean;
  content?: string;
}

export interface Checkpoint {
  id: string;
  timestamp: string;
  description: string;
  toolName: string;
  fileCount: number;
}

export interface SubagentTask {
  id: string;
  name: string;
  description: string;
  status: 'pending' | 'running' | 'done' | 'failed';
  progress: string;
  filesChanged: string[];
  durationMs: number | null;
  error?: string;
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
  | { type: 'RECEIVE_TOOL_RESULT'; name: string; result: string; durationMs?: number; structuredResult?: unknown }
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
  | { type: 'REJECT_PLAN' }
  | { type: 'FORK_SESSION'; messageId: string }
  | { type: 'FORK_SESSION_DONE'; sessionId: string }
  | { type: 'CANCEL_FORK' }
  | { type: 'SET_CHECKPOINTS'; checkpoints: Checkpoint[] }
  | { type: 'ADD_CHECKPOINT'; checkpoint: Checkpoint }
  | { type: 'REMOVE_CHECKPOINT'; id: string }
  | { type: 'TOGGLE_CHECKPOINT_PANEL' }
  | { type: 'SUBAGENT_START'; id: string; name: string; description: string }
  | { type: 'SUBAGENT_PROGRESS'; id: string; progress: string }
  | { type: 'SUBAGENT_COMPLETE'; id: string; filesChanged: string[]; durationMs: number }
  | { type: 'SUBAGENT_FAIL'; id: string; error: string }
  | { type: 'CLEAR_SUBAGENT_TASKS' }
  | { type: 'SET_TOKEN_USAGE'; percent: number }
  | { type: 'SET_THEME'; theme: ThemeMode; customPath?: string }
  | { type: 'SET_AVAILABLE_THEMES'; themes: Array<{ name: string; path: string; isBuiltin: boolean }> }
  | { type: 'TOGGLE_VIM_MODE' }
  | { type: 'SET_KEYBINDINGS'; keybindings: Record<string, string> }
  | { type: 'SET_CONTEXT_FILES'; files: ContextFile[] }
  | { type: 'CONTEXT_LOADED'; path: string }
  | { type: 'START_INLINE_EDIT'; path: string; selection: string }
  | { type: 'INLINE_EDIT_RESULT'; newText: string; diff: string }
  | { type: 'INLINE_EDIT_ERROR'; error: string }
  | { type: 'CANCEL_INLINE_EDIT' };

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
    forkingMessageId: null,
    checkpoints: [],
    checkpointPanelOpen: false,
    subagentTasks: [],
    tokenUsagePercent: 0,
    theme: 'dark',
    customThemePath: null,
    availableThemes: [],
    vimMode: false,
    contextFiles: [],
    keybindings: {},
    inlineEdit: null,
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
          tcs[idx] = { ...tcs[idx], result: action.result, durationMs: action.durationMs, structuredResult: action.structuredResult };
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

    case 'FORK_SESSION':
      return { ...state, forkingMessageId: action.messageId };

    case 'FORK_SESSION_DONE':
      return { ...state, forkingMessageId: null };

    case 'CANCEL_FORK':
      return { ...state, forkingMessageId: null };

    case 'SET_CHECKPOINTS':
      return { ...state, checkpoints: action.checkpoints };

    case 'ADD_CHECKPOINT': {
      const existing = state.checkpoints.find((c) => c.id === action.checkpoint.id);
      if (existing) return state;
      return { ...state, checkpoints: [action.checkpoint, ...state.checkpoints] };
    }

    case 'REMOVE_CHECKPOINT':
      return {
        ...state,
        checkpoints: state.checkpoints.filter((c) => c.id !== action.id),
      };

    case 'TOGGLE_CHECKPOINT_PANEL': {
      const next = !state.checkpointPanelOpen;
      localStorage.setItem('wisp_checkpoint_panel', String(next));
      return { ...state, checkpointPanelOpen: next };
    }

    case 'SUBAGENT_START':
      return {
        ...state,
        subagentTasks: [
          ...state.subagentTasks,
          {
            id: action.id,
            name: action.name,
            description: action.description,
            status: 'running' as const,
            progress: '',
            filesChanged: [],
            durationMs: null,
          },
        ],
      };

    case 'SUBAGENT_PROGRESS':
      return {
        ...state,
        subagentTasks: state.subagentTasks.map((t) =>
          t.id === action.id ? { ...t, progress: action.progress } : t,
        ),
      };

    case 'SUBAGENT_COMPLETE':
      return {
        ...state,
        subagentTasks: state.subagentTasks.map((t) =>
          t.id === action.id
            ? { ...t, status: 'done' as const, filesChanged: action.filesChanged, durationMs: action.durationMs }
            : t,
        ),
      };

    case 'SUBAGENT_FAIL':
      return {
        ...state,
        subagentTasks: state.subagentTasks.map((t) =>
          t.id === action.id
            ? { ...t, status: 'failed' as const, error: action.error }
            : t,
        ),
      };

    case 'CLEAR_SUBAGENT_TASKS':
      return {
        ...state,
        subagentTasks: state.subagentTasks.filter((t) => t.status === 'running'),
      };

    case 'SET_TOKEN_USAGE':
      return { ...state, tokenUsagePercent: action.percent };

    case 'SET_THEME': {
      localStorage.setItem('wisp_theme', action.theme);
      if (action.customPath) {
        localStorage.setItem('wisp_custom_theme_path', action.customPath);
      } else {
        localStorage.removeItem('wisp_custom_theme_path');
      }
      return {
        ...state,
        theme: action.theme,
        customThemePath: action.customPath || null,
      };
    }

    case 'SET_AVAILABLE_THEMES':
      return { ...state, availableThemes: action.themes };

    case 'TOGGLE_VIM_MODE': {
      const nextVim = !state.vimMode;
      localStorage.setItem('wisp_vim_mode', String(nextVim));
      return { ...state, vimMode: nextVim };
    }

    case 'SET_KEYBINDINGS': {
      localStorage.setItem('wisp_keybindings', JSON.stringify(action.keybindings));
      return { ...state, keybindings: action.keybindings };
    }

    case 'SET_CONTEXT_FILES':
      return { ...state, contextFiles: action.files };

    case 'CONTEXT_LOADED':
      return {
        ...state,
        contextFiles: state.contextFiles.map((f) =>
          f.path === action.path ? { ...f, loaded: true } : f,
        ),
      };

    case 'START_INLINE_EDIT':
      return {
        ...state,
        inlineEdit: {
          path: action.path,
          selection: action.selection,
          newText: null,
          diff: null,
          isProcessing: true,
          error: null,
        },
      };

    case 'INLINE_EDIT_RESULT':
      return {
        ...state,
        inlineEdit: state.inlineEdit
          ? { ...state.inlineEdit, newText: action.newText, diff: action.diff, isProcessing: false }
          : null,
      };

    case 'INLINE_EDIT_ERROR':
      return {
        ...state,
        inlineEdit: state.inlineEdit
          ? { ...state.inlineEdit, error: action.error, isProcessing: false }
          : null,
      };

    case 'CANCEL_INLINE_EDIT':
      return { ...state, inlineEdit: null };

    default:
      return state;
  }
}
