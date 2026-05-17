import { useEffect, useRef, useCallback } from 'react';
import { Dispatch } from 'react';
import WebSocket from 'ws';
import type { Action } from '../state/types.js';
import type { ServerMessage, ClientMessage } from '../state/types.js';

export interface WebSocketHandle {
  send: (msg: ClientMessage) => void;
  close: () => void;
}

export function useWebSocket(
  serverUrl: string,
  dispatch: Dispatch<Action>,
): WebSocketHandle {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    dispatch({ type: 'CONNECT' });
    const httpUrl = serverUrl.replace(/\/$/, '');
    const wsUrl = httpUrl.replace(/^http/, 'ws') + '/ws/agent';
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.on('open', () => {
      attemptRef.current = 0;
      dispatch({ type: 'CONNECT' });
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30_000);
    });

    ws.on('message', (data) => {
      try {
        const msg: ServerMessage = JSON.parse(data.toString());
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
            dispatch({ type: 'RECEIVE_DONE', sessionId: msg.session_id || '' });
            break;
          case 'complete':
            dispatch({ type: 'RECEIVE_COMPLETE', sessionId: msg.session_id || '' });
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
    });

    ws.on('close', () => {
      dispatch({ type: 'DISCONNECT' });
      if (pingRef.current) {
        clearInterval(pingRef.current);
        pingRef.current = null;
      }
      const delay = Math.min(1000 * Math.pow(2, attemptRef.current), 30_000);
      attemptRef.current += 1;
      reconnectRef.current = setTimeout(connect, delay);
    });

    ws.on('error', () => {
      dispatch({ type: 'CONNECTION_ERROR', error: 'WebSocket connection failed' });
    });
  }, [serverUrl, dispatch]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (pingRef.current) clearInterval(pingRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((msg: ClientMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { send, close: () => wsRef.current?.close() };
}
