import React from 'react';
import { AppShell } from './components/layout/AppShell.js';
import { ErrorBoundary } from './components/ErrorBoundary.js';
import { AppContext } from './state/context.js';
import { appReducer, createInitialState } from './state/types.js';
import { useWebSocket } from './hooks/useWebSocket.js';
import { applyTheme, DARK_THEME, LIGHT_THEME, loadCustomThemeAsync } from './utils/themes.js';
import { loadKeybindings } from './utils/keybindings.js';

import { useMenuIPC } from './hooks/useMenuIPC.js';

interface Props {
  serverUrl: string;
  apiKey: string;
}

function getPersistedConfig(defaults: { serverUrl: string; apiKey: string }) {
  const url = localStorage.getItem('wisp_server_url') || defaults.serverUrl;
  const key = localStorage.getItem('wisp_api_key') || defaults.apiKey;
  const model = localStorage.getItem('wisp_selected_model') || '';
  const systemPrompt = localStorage.getItem('wisp_system_prompt') || '';
  const permissionMode = (localStorage.getItem('wisp_permission_mode') || 'full') as 'full' | 'ask_all' | 'auto_edit' | 'read_only';
  let pinnedSessionIds: string[] = [];
  try {
    const raw = localStorage.getItem('wisp_pinned_sessions');
    if (raw) pinnedSessionIds = JSON.parse(raw);
  } catch { /* ignore corrupt data */ }
  // Restore last active session ID for cross-session persistence
  const lastSessionId = localStorage.getItem('wisp_last_session_id') || null;
  return { serverUrl: url, apiKey: key, selectedModel: model, pinnedSessionIds, systemPrompt, permissionMode, lastSessionId };
}

export const App: React.FC<Props> = ({ serverUrl, apiKey }) => {
  const persisted = getPersistedConfig({ serverUrl, apiKey });
  const [state, dispatch] = React.useReducer(
    appReducer,
    persisted,
    (opts) => createInitialState(opts),
  );
  const ws = useWebSocket(serverUrl, apiKey, dispatch);
  useMenuIPC(dispatch, serverUrl, apiKey);

  // Persist last active session ID for cross-session memory
  React.useEffect(() => {
    if (state.sessionId) {
      localStorage.setItem('wisp_last_session_id', state.sessionId);
    }
  }, [state.sessionId]);

  // Auto-resume last session on startup: load messages AND set sessionId
  React.useEffect(() => {
    console.log('[App] Auto-resume effect running:', {
      sessionId: state.sessionId,
      lastSessionId: persisted.lastSessionId,
      workspacePath: state.workspacePath,
    });
    if (state.sessionId || !persisted.lastSessionId || !state.workspacePath) {
      console.log('[App] Skipping auto-resume:', {
        reason: state.sessionId ? 'already has sessionId' : !persisted.lastSessionId ? 'no lastSessionId' : 'no workspacePath',
      });
      return;
    }
    const baseUrl = serverUrl.replace(/\/$/, '');
    const params = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';
    const url = `${baseUrl}/api/sessions/${encodeURIComponent(persisted.lastSessionId)}${params}`;
    console.log('[App] Fetching session:', url);
    fetch(url, {
      headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined,
    })
      .then((r) => {
        console.log('[App] Fetch response:', r.status);
        if (!r.ok) throw new Error('Session not found');
        return r.json();
      })
      .then((data: any) => {
        const msgs = data?.session?.messages || [];
        console.log('[App] Session data:', { messagesCount: msgs.length });
        if (msgs.length > 0) {
          // Convert backend messages to frontend format
          const frontendMsgs: Array<{
            id: string;
            role: 'user' | 'assistant' | 'system';
            content: string;
            thinking?: string;
            toolCalls?: Array<{ name: string; args: Record<string, unknown>; result?: string }>;
          }> = [];
          for (let i = 0; i < msgs.length; i++) {
            const m = msgs[i];
            if (m.role === 'user') {
              frontendMsgs.push({
                id: `hist-${i}`,
                role: 'user',
                content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
              });
            } else if (m.role === 'assistant') {
              const toolCalls = (m.tool_calls || []).map((tc: any) => ({
                name: tc.function?.name || '',
                args: tc.function?.arguments || {},
              }));
              frontendMsgs.push({
                id: `hist-${i}`,
                role: 'assistant',
                content: typeof m.content === 'string' ? (m.content || '') : '',
                thinking: m.thinking,
                toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
              });
            } else if (m.role === 'tool') {
              // Attach tool result to previous assistant
              const last = frontendMsgs[frontendMsgs.length - 1];
              if (last && last.role === 'assistant' && last.toolCalls) {
                const tcs = [...last.toolCalls];
                const idx = tcs.findIndex((tc) => !tc.result);
                if (idx >= 0) {
                  tcs[idx] = { ...tcs[idx], result: typeof m.content === 'string' ? m.content : JSON.stringify(m.content) };
                  frontendMsgs[frontendMsgs.length - 1] = { ...last, toolCalls: tcs };
                }
              }
            }
          }
          console.log('[App] Loaded messages:', frontendMsgs.length);
          dispatch({ type: 'SET_MESSAGES', messages: frontendMsgs });
          dispatch({ type: 'SET_SESSION_ID', id: persisted.lastSessionId });
          dispatch({
            type: 'RECEIVE_STATUS',
            message: `Resumed previous session (${frontendMsgs.length} messages).`,
            level: 'info',
          });
          console.log('[App] Session resumed successfully');
        } else {
          console.log('[App] Session has no messages, not restoring');
        }
      })
      .catch((err) => {
        console.error('[App] Failed to resume session:', err);
        localStorage.removeItem('wisp_last_session_id');
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.workspacePath]);

  // Persist settings to localStorage
  React.useEffect(() => {
    localStorage.setItem('wisp_pinned_sessions', JSON.stringify([...state.pinnedSessionIds]));
  }, [state.pinnedSessionIds]);

  React.useEffect(() => {
    localStorage.setItem('wisp_selected_model', state.selectedModel);
  }, [state.selectedModel]);

  React.useEffect(() => {
    localStorage.setItem('wisp_system_prompt', state.systemPrompt);
  }, [state.systemPrompt]);

  React.useEffect(() => {
    localStorage.setItem('wisp_permission_mode', state.permissionMode);
  }, [state.permissionMode]);

  React.useEffect(() => {
    localStorage.setItem('wisp_input_draft', state.inputValue);
  }, [state.inputValue]);

  // Initial API fetches (models, workspace)
  React.useEffect(() => {
    const baseUrl = serverUrl.replace(/\/$/, '');
    const params = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';
    const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined;

    fetch(`${baseUrl}/api/models${params}`, { headers })
      .then((r) => r.json())
      .then((data) => {
        if (data.models?.length > 0) {
          dispatch({ type: 'SET_MODELS', models: data.models });
        }
      })
      .catch(() => {});

    fetch(`${baseUrl}/api/workspace${params}`, { headers })
      .then((r) => r.json())
      .then((data: { path?: string }) => {
        if (data.path) {
          dispatch({ type: 'SET_WORKSPACE', path: data.path });
        }
      })
      .catch(() => {});
  }, [serverUrl, apiKey]);

  // Restore theme
  React.useEffect(() => {
    const storedTheme = localStorage.getItem('wisp_theme') as 'dark' | 'light' | 'custom' | null;
    const storedPath = localStorage.getItem('wisp_custom_theme_path');

    if (storedTheme === 'dark') {
      applyTheme(DARK_THEME);
    } else if (storedTheme === 'light') {
      applyTheme(LIGHT_THEME);
    } else if (storedTheme === 'custom' && storedPath) {
      loadCustomThemeAsync(storedPath).then((data) => {
        if (data) applyTheme(data);
        else applyTheme(DARK_THEME);
      });
    }
  }, []);

  // Poll for edit suggestions every 30s when panel is open
  React.useEffect(() => {
    if (!state.suggestionsPanelOpen || state.connection !== 'connected') return;
    const baseUrl = serverUrl.replace(/\/$/, '');
    const params = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';
    const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined;

    const poll = () => {
      fetch(`${baseUrl}/api/suggestions${params}`, { headers })
        .then((r) => r.json())
        .then((data: { suggestions?: Array<{ path: string; mtime: number; diagnostic_count: number; severities: Record<string, number> }> }) => {
          if (data.suggestions) {
            dispatch({ type: 'SET_SUGGESTIONS', suggestions: data.suggestions });
          }
        })
        .catch(() => {});
    };

    poll();
    const interval = setInterval(poll, 30_000);
    return () => clearInterval(interval);
  }, [state.suggestionsPanelOpen, state.connection, serverUrl, apiKey]);

  // Restore vim mode
  React.useEffect(() => {
    const storedVim = localStorage.getItem('wisp_vim_mode');
    if (storedVim === 'true') {
      dispatch({ type: 'TOGGLE_VIM_MODE' });
    }
  }, []);

  // Restore keybindings
  React.useEffect(() => {
    const bindings = loadKeybindings();
    dispatch({ type: 'SET_KEYBINDINGS', keybindings: bindings });
  }, []);

  const ctx = React.useMemo(
    () => ({ state, dispatch, sendMessage: (msg: Record<string, unknown>) => ws.send(msg) }),
    [state, dispatch, ws.send],
  );

  return (
    <ErrorBoundary>
      <AppContext.Provider value={ctx}>
        <AppShell />
      </AppContext.Provider>
    </ErrorBoundary>
  );
};
