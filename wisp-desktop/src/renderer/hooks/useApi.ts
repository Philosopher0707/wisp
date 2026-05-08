import { useCallback, useMemo } from 'react';
import type { SessionSummary, Message, ToolCallItem } from '../state/types.js';

export interface GitStatus {
  git: boolean;
  branch?: string;
  dirty?: boolean;
}

interface ApiClient {
  fetchSessions: () => Promise<SessionSummary[]>;
  fetchSession: (id: string) => Promise<Message[]>;
  deleteSession: (id: string) => Promise<boolean>;
  renameSession: (id: string, title: string) => Promise<boolean>;
  fetchModels: () => Promise<string[]>;
  fetchFiles: (path?: string) => Promise<FileItems | null>;
  fetchGitStatus: () => Promise<GitStatus | null>;
  healthCheck: () => Promise<boolean>;
}

export interface FileItem {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size: number | null;
}

export interface FileItems {
  type: 'directory' | 'file';
  path: string;
  items?: FileItem[];
  content?: string;
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
    async (path: string, opts?: { method?: string; body?: unknown }): Promise<unknown> => {
      const url = serverUrl.replace(/\/$/, '') + path;
      const init: RequestInit = { method: opts?.method || 'GET' };
      if (opts?.body) {
        init.headers = { 'Content-Type': 'application/json' };
        init.body = JSON.stringify(opts.body);
      }
      const resp = await fetch(url, init);
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

  const renameSession = useCallback(async (id: string, title: string): Promise<boolean> => {
    try {
      const data = await apiFetch(
        `/api/sessions/${encodeURIComponent(id)}${authParams}`,
        { method: 'PATCH', body: { title } },
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

  const fetchModels = useCallback(async (): Promise<string[]> => {
    try {
      const data = await apiFetch(`/api/models${authParams}`) as { models?: string[] };
      return data.models || [];
    } catch {
      return [];
    }
  }, [apiFetch, authParams]);

  const fetchGitStatus = useCallback(async (): Promise<GitStatus | null> => {
    try {
      return await apiFetch(`/api/git${authParams}`) as GitStatus;
    } catch {
      return null;
    }
  }, [apiFetch, authParams]);

  const fetchFiles = useCallback(async (path = ''): Promise<FileItems | null> => {
    try {
      const qs = `${authParams}${authParams ? '&' : '?'}path=${encodeURIComponent(path)}`;
      return await apiFetch(`/api/files${qs}`) as FileItems;
    } catch {
      return null;
    }
  }, [apiFetch, authParams]);

  return useMemo(
    () => ({ fetchSessions, fetchSession, deleteSession, renameSession, fetchModels, fetchFiles, fetchGitStatus, healthCheck }),
    [fetchSessions, fetchSession, deleteSession, renameSession, fetchModels, fetchFiles, fetchGitStatus, healthCheck],
  );
}
