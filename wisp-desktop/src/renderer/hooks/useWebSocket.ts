import { useEffect, useRef, useCallback, Dispatch } from 'react';
import type { Action } from '../state/types.js';

interface ServerMessage {
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
  level?: string;
  session_id?: string;
}

export interface WsHandle {
  send: (msg: Record<string, unknown>) => void;
}

export function useWebSocket(
  serverUrl: string,
  apiKey: string,
  dispatch: Dispatch<Action>,
): WsHandle {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    if (wsRef.current?.readyState === WebSocket.CONNECTING) return;

    dispatch({ type: 'SET_CONNECTION', status: 'connecting' });

    const httpUrl = serverUrl.replace(/\/$/, '');
    const wsUrl = httpUrl.replace(/^http/, 'ws') + '/ws/agent';
    const fullUrl = wsUrl;

    const ws = new WebSocket(fullUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      dispatch({ type: 'SET_CONNECTION', status: 'connected' });

      // Send auth message after connection open
      if (apiKey) {
        ws.send(JSON.stringify({ type: 'auth', api_key: apiKey }));
      }

      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30_000);
    };

    ws.onmessage = (event) => {
      try {
        const msg: ServerMessage = JSON.parse(event.data as string);
        switch (msg.type) {
          case 'token':
            if (msg.phase && msg.text) {
              dispatch({ type: 'RECEIVE_TOKEN', phase: msg.phase, text: msg.text });
            }
            break;
          case 'tool_call':
            dispatch({
              type: 'RECEIVE_TOOL_CALL',
              name: msg.name || '',
              arguments: msg.arguments || {},
            });
            break;
          case 'tool_result': {
            let structuredResult: unknown = undefined;
            try {
              const parsed = JSON.parse(msg.result || '');
              if (parsed && typeof parsed === 'object' && parsed.status) {
                structuredResult = parsed;
              }
            } catch { /* not JSON, keep as plain text */ }
            dispatch({
              type: 'RECEIVE_TOOL_RESULT',
              name: msg.name || '',
              result: msg.result || '',
              durationMs: msg.duration_ms,
              structuredResult,
            });
            break;
          }
          case 'tool_approval_request':
            dispatch({
              type: 'RECEIVE_APPROVAL_REQUEST',
              callId: msg.call_id || '',
              name: msg.name || '',
              arguments: msg.arguments || {},
              reason: msg.reason || '',
            });
            break;
          case 'done':
            dispatch({ type: 'RECEIVE_DONE' });
            break;
          case 'plan_ready':
            dispatch({ type: 'RECEIVE_COMPLETE', sessionId: msg.session_id || undefined });
            dispatch({ type: 'RECEIVE_PLAN', content: msg.content || '' });
            break;
          case 'complete':
            dispatch({ type: 'RECEIVE_COMPLETE', sessionId: msg.session_id || undefined });
            // Check git status after agent completes
            (async () => {
              try {
                const base = serverUrl.replace(/\/$/, '');
                const params = '';
                const resp = await fetch(`${base}/api/git`, {
                  headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined,
                });
                if (resp.ok) {
                  const git = await resp.json() as { git: boolean; branch?: string; dirty?: boolean; changed_files?: string[] };
                  if (git.git && git.dirty && git.changed_files && git.changed_files.length > 0) {
                    dispatch({ type: 'SHOW_GIT_BANNER', branch: git.branch || 'unknown', changedFiles: git.changed_files });
                  }
                }
              } catch { /* ignore git check failures */ }
            })();
            break;
          case 'error':
            dispatch({ type: 'RECEIVE_ERROR', message: msg.message || '' });
            break;
          case 'status':
            dispatch({
              type: 'RECEIVE_STATUS',
              message: msg.message || '',
              level: msg.level || 'info',
            });
            break;
          case 'hook_executed':
            // Hook execution log — store locally when hook log panel needs it
            break;
          case 'subagent_start':
            dispatch({
              type: 'SUBAGENT_START',
              id: (msg as any).subagent_id || '',
              name: (msg as any).name || '',
              description: (msg as any).description || '',
            });
            break;
          case 'subagent_progress':
            dispatch({
              type: 'SUBAGENT_PROGRESS',
              id: (msg as any).subagent_id || '',
              progress: (msg as any).progress || '',
            });
            break;
          case 'subagent_complete':
            dispatch({
              type: 'SUBAGENT_COMPLETE',
              id: (msg as any).subagent_id || '',
              filesChanged: (msg as any).files_changed || [],
              durationMs: (msg as any).duration_ms || 0,
            });
            break;
          case 'subagent_fail':
            dispatch({
              type: 'SUBAGENT_FAIL',
              id: (msg as any).subagent_id || '',
              error: (msg as any).error || 'Unknown error',
            });
            break;
          case 'context_loaded':
            if ((msg as any).files) {
              const files: Array<{ path: string; loaded: boolean; content?: string }> = (msg as any).files;
              dispatch({ type: 'SET_CONTEXT_FILES', files });
            }
            break;
          case 'token_usage':
            dispatch({
              type: 'SET_TOKEN_USAGE',
              percent: typeof (msg as any).percent === 'number' ? (msg as any).percent : 0,
            });
            break;
          case 'steering_paused':
            dispatch({ type: 'AGENT_PAUSED' });
            break;
          case 'steering_resumed':
            dispatch({ type: 'AGENT_RESUMED' });
            break;
          case 'steering_inject':
            dispatch({
              type: 'RECEIVE_STATUS',
              message: `Steering: ${(msg as any).text || ''}`,
              level: 'info',
            });
            break;
          case 'pong':
            break;
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      dispatch({ type: 'SET_CONNECTION', status: 'disconnected' });
      if (pingRef.current) {
        clearInterval(pingRef.current);
        pingRef.current = null;
      }
      const delay = Math.min(1000 * 2 ** attemptRef.current, 30_000);
      attemptRef.current += 1;
      reconnectRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      dispatch({ type: 'SET_CONNECTION', status: 'error', error: 'WebSocket connection failed' });
    };
  }, [serverUrl, apiKey, dispatch]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (pingRef.current) clearInterval(pingRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((msg: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { send };
}
