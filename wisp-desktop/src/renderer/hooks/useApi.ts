import { useCallback } from 'react';
import type { SessionSummary, Message, ToolCallItem } from '../state/types.js';

interface ApiClient {
  fetchSessions: () => Promise<SessionSummary[]>;
  fetchSession: (id: string) => Promise<Message[]>;
  deleteSession: (id: string) => Promise<boolean>;
  healthCheck: () => Promise<boolean>;
}

interface RawMessage {
  role: string;
  content: string;
  thinking?: string;
  tool_calls?: Array<{ function: { name: string; arguments: Record<string, unknown> } }>;
  name?: string;
}

export function useApi(serverUrl: string, apiKey: string): ApiClient {
  const authParams = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';

  const apiFetch = useCallback(
    async (path: string, opts?: { method?: string }): Promise<unknown> => {
      const url = serverUrl.replace(/\/$/, '') + path;
      const resp = await fetch(url, { method: opts?.method || 'GET' });
      if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
      return resp.json();
    },
    [serverUrl],
  );

  const fetchSessions = useCallback(async (): Promise<SessionSummary[]> => {
    const data = await apiFetch(`/api/sessions${authParams}`) as {
      sessions: SessionSummary[];
    };
    return data.sessions || [];
  }, [apiFetch, authParams]);

  const fetchSession = useCallback(async (id: string): Promise<Message[]> => {
    const data = await apiFetch(
      `/api/sessions/${encodeURIComponent(id)}${authParams}`,
    ) as { session?: { messages?: RawMessage[] } };

    const rawMessages = data.session?.messages || [];
    const messages: Message[] = [];

    for (let i = 0; i < rawMessages.length; i++) {
      const raw = rawMessages[i];
      const uid = `hist-${i}`;

      if (raw.role === 'user') {
        messages.push({ id: uid, role: 'user', content: raw.content });
      } else if (raw.role === 'assistant') {
        const toolCalls: ToolCallItem[] = (raw.tool_calls || []).map((tc) => ({
          name: tc.function.name,
          args: tc.function.arguments,
        }));
        messages.push({
          id: uid,
          role: 'assistant',
          content: raw.content || '',
          thinking: raw.thinking,
          toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        });
      } else if (raw.role === 'tool') {
        // Attach tool results to the previous assistant message
        const last = messages[messages.length - 1];
        if (last && last.role === 'assistant' && last.toolCalls) {
          const tcs = [...last.toolCalls];
          const idx = tcs.findIndex((tc) => !tc.result);
          if (idx >= 0) {
            tcs[idx] = { ...tcs[idx], result: raw.content };
            messages[messages.length - 1] = { ...last, toolCalls: tcs };
          }
        }
      }
    }

    return messages;
  }, [apiFetch, authParams]);

  const deleteSession = useCallback(async (id: string): Promise<boolean> => {
    try {
      const data = await apiFetch(
        `/api/sessions/${encodeURIComponent(id)}${authParams}`,
        { method: 'DELETE' },
      ) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const healthCheck = useCallback(async (): Promise<boolean> => {
    try {
      const data = await apiFetch('/api/health') as { status: string };
      return data.status === 'ok';
    } catch {
      return false;
    }
  }, [apiFetch]);

  return { fetchSessions, fetchSession, deleteSession, healthCheck };
}
