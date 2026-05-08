import { useCallback } from 'react';
import type { SessionSummary } from '../state/types.js';

interface ApiClient {
  fetchSessions: () => Promise<SessionSummary[]>;
  healthCheck: () => Promise<boolean>;
}

export function useApi(serverUrl: string, apiKey: string): ApiClient {
  const authParams = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';

  const apiFetch = useCallback(
    async (path: string): Promise<unknown> => {
      const url = serverUrl.replace(/\/$/, '') + path;
      const resp = await fetch(url);
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

  const healthCheck = useCallback(async (): Promise<boolean> => {
    try {
      const data = await apiFetch('/api/health') as { status: string };
      return data.status === 'ok';
    } catch {
      return false;
    }
  }, [apiFetch]);

  return { fetchSessions, healthCheck };
}
