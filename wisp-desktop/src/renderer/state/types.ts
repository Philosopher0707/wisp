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

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

export interface AppState {
  sidebarExpandedProjects: Set<string>;
  pinnedChats: ChatSummary[];
  projects: ProjectFolder[];
  currentChatId: string | null;
  messages: Message[];
  isStreaming: boolean;
  inputValue: string;
  selectedModel: string;
  reasoningLevel: 'low' | 'medium' | 'high';
  selectedProject: string | null;
  activeDropdown: string | null;
  isFullAccessEnabled: boolean;
  showThinking: boolean;
}

export type Action =
  | { type: 'TOGGLE_PROJECT_FOLDER'; projectId: string }
  | { type: 'SELECT_CHAT'; chatId: string }
  | { type: 'NEW_CHAT' }
  | { type: 'SET_INPUT'; value: string }
  | { type: 'SUBMIT_MESSAGE'; content: string }
  | { type: 'RECEIVE_TOKEN'; text: string }
  | { type: 'RECEIVE_DONE' }
  | { type: 'SET_MODEL'; model: string }
  | { type: 'SET_REASONING'; level: 'low' | 'medium' | 'high' }
  | { type: 'OPEN_DROPDOWN'; id: string }
  | { type: 'CLOSE_DROPDOWN' }
  | { type: 'TOGGLE_FULL_ACCESS' }
  | { type: 'CLEAR_CHAT' }
  | { type: 'TOGGLE_THINKING' }
  | { type: 'SET_PROJECT'; projectId: string | null };

export function createInitialState(): AppState {
  return {
    sidebarExpandedProjects: new Set<string>(),
    pinnedChats: [],
    projects: [],
    currentChatId: null,
    messages: [],
    isStreaming: false,
    inputValue: '',
    selectedModel: 'claude-sonnet-4-6',
    reasoningLevel: 'medium',
    selectedProject: null,
    activeDropdown: null,
    isFullAccessEnabled: true,
    showThinking: false,
  };
}

export function appReducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'TOGGLE_PROJECT_FOLDER': {
      const next = new Set(state.sidebarExpandedProjects);
      if (next.has(action.projectId)) {
        next.delete(action.projectId);
      } else {
        next.add(action.projectId);
      }
      return { ...state, sidebarExpandedProjects: next };
    }
    case 'SELECT_CHAT':
      return { ...state, currentChatId: action.chatId };
    case 'NEW_CHAT':
      return { ...state, currentChatId: null, messages: [], isStreaming: false };
    case 'SET_INPUT':
      return { ...state, inputValue: action.value };
    case 'SUBMIT_MESSAGE':
      return { ...state, inputValue: '', isStreaming: true };
    case 'RECEIVE_TOKEN':
      return state;
    case 'RECEIVE_DONE':
      return { ...state, isStreaming: false };
    case 'SET_MODEL':
      return { ...state, selectedModel: action.model, activeDropdown: null };
    case 'SET_REASONING':
      return { ...state, reasoningLevel: action.level, activeDropdown: null };
    case 'OPEN_DROPDOWN':
      return { ...state, activeDropdown: action.id };
    case 'CLOSE_DROPDOWN':
      return { ...state, activeDropdown: null };
    case 'TOGGLE_FULL_ACCESS':
      return { ...state, isFullAccessEnabled: !state.isFullAccessEnabled };
    case 'CLEAR_CHAT':
      return { ...state, messages: [] };
    case 'TOGGLE_THINKING':
      return { ...state, showThinking: !state.showThinking };
    case 'SET_PROJECT':
      return { ...state, selectedProject: action.projectId };
    default:
      return state;
  }
}
