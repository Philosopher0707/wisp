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

    const params = new URLSearchParams();
    if (apiKey) params.set('api_key', apiKey);
    const fullUrl = params.toString() ? wsUrl + '?' + params.toString() : wsUrl;

    const ws = new WebSocket(fullUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      dispatch({ type: 'SET_CONNECTION', status: 'connected' });
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
          case 'tool_result':
            dispatch({
              type: 'RECEIVE_TOOL_RESULT',
              name: msg.name || '',
              result: msg.result || '',
              durationMs: msg.duration_ms,
            });
            break;
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
          case 'complete':
            dispatch({ type: 'RECEIVE_COMPLETE' });
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
