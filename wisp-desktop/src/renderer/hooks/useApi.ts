import { useCallback, useMemo } from 'react';
import type { SessionSummary, Message, ToolCallItem } from '../state/types.js';

/**
 * The single authority for the `Authorization` header.
 *
 * Every request this client makes must be built from this function. A request
 * that constructs its own headers silently ships unauthenticated when the key
 * is configured — and because the key is deliberately never sent as a query
 * param (`authParams` is empty, so it cannot leak into logs), there is no
 * fallback that would make the omission visible.
 */
function makeAuthHeaders(apiKey: string): Record<string, string> {
  const headers: Record<string, string> = {};
  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`;
  }
  return headers;
}

/**
 * Turn a failed response into a message that says *why*.
 *
 * The server returns a JSON `detail` for policy denials (403) and validation
 * failures (400/404). Without this, every refusal reads as a bare
 * "API 403: Forbidden" — which hides the reason now that the executable-config
 * routes (hooks, MCP servers, plugins) carry the permission-policy gate: the
 * useful text is "Blocked by server policy (read-only session); no approver is
 * present over REST".
 */
async function describeApiError(resp: Response): Promise<string> {
  const base = `API ${resp.status}: ${resp.statusText}`;
  try {
    const body = (await resp.clone().json()) as { detail?: unknown };
    const detail = body?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return `${base} — ${detail}`;
    }
  } catch {
    // Non-JSON body (empty, HTML error page) — the status line is all we have.
  }
  return base;
}

export interface GitStatus {
  git: boolean;
  branch?: string;
  dirty?: boolean;
}

export interface PluginInfo {
  name: string;
  version: string;
  author: string;
  enabled: boolean;
  description?: string;
}

export interface MarketplaceItem {
  name: string;
  version: string;
  author: string;
  description: string;
  downloads: number;
}

export interface MCPServerInfo {
  name: string;
  transport: string;
  status: 'connected' | 'disconnected' | 'error';
  tool_count: number;
  latency_ms: number | null;
}

export interface HookInfo {
  name: string;
  event: string;
  command: string;
  enabled: boolean;
  matcher?: string;
  timeout?: number;
}

export interface HookLogEntry {
  hook_name: string;
  event: string;
  result: string;
  timestamp: string;
}

export interface CheckpointEntry {
  id: string;
  created_at: string;
  label?: string;
  session_id?: string;
}

export interface ContextResponse {
  content: string;
  files_found: string[];
}

/** One entry from the unfiltered tool registry. */
export interface CapabilityTool {
  name: string;
  [key: string]: unknown;
}

/**
 * The principal as the server sees it. Mirrors `_principal_view` in
 * wisp/server/routes/capabilities.py -- credential_handle is omitted there
 * by design and must not appear here.
 */
export interface CapabilityPrincipal {
  id: string;
  kind: string;
  os_user: string;
  workspace: string;
  profile: string;
  org_id?: string | null;
  parent_principal_id?: string | null;
  unbounded: boolean;
}

/**
 * GET /api/capabilities -- the effective tool surface for the active principal.
 *
 * WARNING: `unbounded` here is the INVERSE of the same-named field on the
 * subagent_start event. At principal level, `capabilities is None` means the
 * principal is UNRESTRICTED (capabilities.py:107); at subagent level, null
 * means the backend never reported a surface. Never render one with the
 * component built for the other.
 */
export interface CapabilityResponse {
  principal: CapabilityPrincipal;
  tools: CapabilityTool[];
  tool_count: number;
  registry_count: number;
  authority_note: string;
  // NOTE: there is deliberately NO top-level `unbounded`. The endpoint's
  // return dict carries only principal/tools/tool_count/registry_count/
  // authority_note; the boolean lives at `principal.unbounded`. Declaring it
  // here would type-check while reading `undefined` at runtime, which is
  // falsy and would render every unrestricted principal as bounded.
}

interface ApiClient {
  fetchSessions: () => Promise<SessionSummary[]>;
  fetchSession: (id: string) => Promise<Message[]>;
  deleteSession: (id: string) => Promise<boolean>;
  renameSession: (id: string, title: string) => Promise<boolean>;
  fetchModels: () => Promise<string[]>;
  fetchFiles: (path?: string) => Promise<FileItems | null>;
  fetchGitStatus: () => Promise<GitStatus | null>;
  forkSession: (messages: Message[], title?: string) => Promise<string | null>;
  healthCheck: () => Promise<boolean>;
  // Plugins
  fetchPlugins: () => Promise<PluginInfo[]>;
  installPlugin: (path: string) => Promise<boolean>;
  uninstallPlugin: (name: string) => Promise<boolean>;
  togglePlugin: (name: string, enabled: boolean) => Promise<boolean>;
  searchMarketplace: (query: string) => Promise<MarketplaceItem[]>;
  // MCP
  fetchMCPServers: () => Promise<MCPServerInfo[]>;
  addMCPServer: (config: Record<string, unknown>) => Promise<boolean>;
  removeMCPServer: (name: string) => Promise<boolean>;
  testMCPServer: (name: string) => Promise<{ ok: boolean; latency_ms: number }>;
  // Hooks
  fetchHooks: () => Promise<HookInfo[]>;
  addHook: (config: Record<string, unknown>) => Promise<boolean>;
  removeHook: (name: string) => Promise<boolean>;
  testHook: (name: string, context: Record<string, unknown>) => Promise<{ result: string }>;
  fetchHookLogs: () => Promise<HookLogEntry[]>;
  // Checkpoints
  fetchCheckpoints: () => Promise<CheckpointEntry[]>;
  restoreCheckpoint: (id: string) => Promise<boolean>;
  dropCheckpoint: (id: string) => Promise<boolean>;
  getCheckpointDiff: (id: string) => Promise<string>;
  // Context
  fetchContext: () => Promise<ContextResponse>;
  updateContext: (content: string) => Promise<boolean>;
  /**
   * Returns `null` when the surface could not be read. `null` here means
   * UNKNOWN, and the caller must render it as unknown -- not as zero tools
   * and not as unrestricted.
   */
  fetchCapabilities: () => Promise<CapabilityResponse | null>;
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
  content: string | Array<{ type: string; text?: string; image_url?: { url: string } }>;
  thinking?: string;
  tool_calls?: Array<{ function: { name: string; arguments: Record<string, unknown> } }>;
  name?: string;
}

function extractText(content: string | unknown[]): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter((p): p is { type: 'text'; text: string } => p?.type === 'text' && 'text' in p)
      .map((p) => p.text)
      .join('');
  }
  return '';
}

export function useApi(serverUrl: string, apiKey: string): ApiClient {
  // API key is sent via Authorization header, NOT query param (leaks to logs).
  const authParams = '';

  const apiFetch = useCallback(
    async (path: string, opts?: { method?: string; body?: unknown }): Promise<unknown> => {
      const url = serverUrl.replace(/\/$/, '') + path;
      const init: RequestInit = { method: opts?.method || 'GET' };
      init.headers = makeAuthHeaders(apiKey);
      if (opts?.body) {
        (init.headers as Record<string, string>)['Content-Type'] = 'application/json';
        init.body = JSON.stringify(opts.body);
      }
      const resp = await fetch(url, init);
      if (!resp.ok) throw new Error(await describeApiError(resp));
      return resp.json();
    },
    [serverUrl, apiKey],
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
        messages.push({ id: uid, role: 'user', content: extractText(raw.content) });
      } else if (raw.role === 'assistant') {
        const toolCalls: ToolCallItem[] = (raw.tool_calls || []).map((tc) => ({
          name: tc.function.name,
          args: tc.function.arguments,
        }));
        messages.push({
          id: uid,
          role: 'assistant',
          content: extractText(raw.content || ''),
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
            tcs[idx] = { ...tcs[idx], result: extractText(raw.content) };
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

  const forkSession = useCallback(async (messages: Message[], title?: string): Promise<string | null> => {
    try {
      const rawMessages: Record<string, unknown>[] = [];
      for (const m of messages) {
        if (m.role === 'user') {
          rawMessages.push({ role: 'user', content: m.content });
        } else if (m.role === 'assistant') {
          const raw: Record<string, unknown> = { role: 'assistant', content: m.content };
          if (m.thinking) raw.thinking = m.thinking;
          if (m.toolCalls) {
            raw.tool_calls = m.toolCalls.map((tc) => ({
              function: { name: tc.name, arguments: tc.args },
            }));
          }
          rawMessages.push(raw);
          // Emit separate tool-result messages for each completed tool call
          if (m.toolCalls) {
            for (let i = 0; i < m.toolCalls.length; i++) {
              const tc = m.toolCalls[i];
              if (tc.result !== undefined) {
                rawMessages.push({
                  role: 'tool',
                  content: tc.result,
                  name: tc.name,
                  tool_call_id: `fc-${i}`,
                });
              }
            }
          }
        }
      }
      const data = await apiFetch(`/api/sessions/fork${authParams}`, {
        method: 'POST',
        body: { messages: rawMessages, title: title || null },
      }) as { session_id: string };
      return data.session_id || null;
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

  const fetchCheckpoints = useCallback(async (): Promise<CheckpointEntry[]> => {
    try {
      const data = await apiFetch(`/api/checkpoints${authParams}`) as { checkpoints?: CheckpointEntry[] };
      return data.checkpoints || [];
    } catch {
      return [];
    }
  }, [apiFetch, authParams]);

  const restoreCheckpoint = useCallback(async (id: string): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/checkpoints/${encodeURIComponent(id)}/restore${authParams}`, {
        method: 'POST',
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const dropCheckpoint = useCallback(async (id: string): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/checkpoints/${encodeURIComponent(id)}${authParams}`, {
        method: 'DELETE',
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const getCheckpointDiff = useCallback(async (id: string): Promise<string> => {
    try {
      const url = serverUrl.replace(/\/$/, '') + `/api/checkpoints/${encodeURIComponent(id)}/diff${authParams}`;
      // This is the one request that cannot go through `apiFetch` (it reads a
      // plain-text diff, not JSON), so it must build its headers from the same
      // authority — and it must be handed the key to do so.
      const resp = await fetch(url, { headers: makeAuthHeaders(apiKey) });
      if (!resp.ok) throw new Error(await describeApiError(resp));
      return await resp.text();
    } catch {
      return '';
    }
  }, [serverUrl, apiKey, authParams]);

  // ── Plugins ──

  const fetchPlugins = useCallback(async (): Promise<PluginInfo[]> => {
    try {
      const data = await apiFetch(`/api/plugins${authParams}`) as { plugins?: PluginInfo[] };
      return data.plugins || [];
    } catch {
      return [];
    }
  }, [apiFetch, authParams]);

  const installPlugin = useCallback(async (sourcePath: string): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/plugins/install${authParams}`, {
        method: 'POST',
        body: { path: sourcePath },
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const uninstallPlugin = useCallback(async (name: string): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/plugins/${encodeURIComponent(name)}${authParams}`, {
        method: 'DELETE',
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const togglePlugin = useCallback(async (name: string, enabled: boolean): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/plugins/${encodeURIComponent(name)}/toggle${authParams}`, {
        method: 'POST',
        body: { enabled },
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const searchMarketplace = useCallback(async (query: string): Promise<MarketplaceItem[]> => {
    try {
      const qs = `${authParams}${authParams ? '&' : '?'}q=${encodeURIComponent(query)}`;
      const data = await apiFetch(`/api/plugins/marketplace${qs}`) as { results?: MarketplaceItem[] };
      return data.results || [];
    } catch {
      return [];
    }
  }, [apiFetch, authParams]);

  // ── MCP ──

  const fetchMCPServers = useCallback(async (): Promise<MCPServerInfo[]> => {
    try {
      const data = await apiFetch(`/api/mcp/servers${authParams}`) as { servers?: MCPServerInfo[] };
      return data.servers || [];
    } catch {
      return [];
    }
  }, [apiFetch, authParams]);

  const addMCPServer = useCallback(async (config: Record<string, unknown>): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/mcp/servers${authParams}`, {
        method: 'POST',
        body: config,
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const removeMCPServer = useCallback(async (name: string): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/mcp/servers/${encodeURIComponent(name)}${authParams}`, {
        method: 'DELETE',
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const testMCPServer = useCallback(async (name: string): Promise<{ ok: boolean; latency_ms: number }> => {
    try {
      return await apiFetch(`/api/mcp/servers/${encodeURIComponent(name)}/test${authParams}`, {
        method: 'POST',
      }) as { ok: boolean; latency_ms: number };
    } catch {
      return { ok: false, latency_ms: 0 };
    }
  }, [apiFetch, authParams]);

  // ── Hooks ──

  const fetchHooks = useCallback(async (): Promise<HookInfo[]> => {
    try {
      const data = await apiFetch(`/api/hooks${authParams}`) as { hooks?: HookInfo[] };
      return data.hooks || [];
    } catch {
      return [];
    }
  }, [apiFetch, authParams]);

  const addHook = useCallback(async (config: Record<string, unknown>): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/hooks${authParams}`, {
        method: 'POST',
        body: config,
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const removeHook = useCallback(async (name: string): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/hooks/${encodeURIComponent(name)}${authParams}`, {
        method: 'DELETE',
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  const testHook = useCallback(async (name: string, context: Record<string, unknown>): Promise<{ result: string }> => {
    try {
      return await apiFetch(`/api/hooks/${encodeURIComponent(name)}/test${authParams}`, {
        method: 'POST',
        body: context,
      }) as { result: string };
    } catch {
      return { result: 'Error: test failed' };
    }
  }, [apiFetch, authParams]);

  const fetchHookLogs = useCallback(async (): Promise<HookLogEntry[]> => {
    try {
      const data = await apiFetch(`/api/hooks/logs${authParams}`) as { logs?: HookLogEntry[] };
      return data.logs || [];
    } catch {
      return [];
    }
  }, [apiFetch, authParams]);

  // ── Context ──

  const fetchContext = useCallback(async (): Promise<ContextResponse> => {
    try {
      return await apiFetch(`/api/context${authParams}`) as ContextResponse;
    } catch {
      return { content: '', files_found: [] };
    }
  }, [apiFetch, authParams]);

  const fetchCapabilities = useCallback(async (): Promise<CapabilityResponse | null> => {
    try {
      return await apiFetch('/api/capabilities') as CapabilityResponse;
    } catch {
      // Fail closed to "unknown". A failed read must never be coerced into
      // an empty tool list, which would read as maximum restriction.
      return null;
    }
  }, [apiFetch]);

  const updateContext = useCallback(async (content: string): Promise<boolean> => {
    try {
      const data = await apiFetch(`/api/context${authParams}`, {
        method: 'PUT',
        body: { content },
      }) as { ok?: boolean };
      return data.ok === true;
    } catch {
      return false;
    }
  }, [apiFetch, authParams]);

  return useMemo(
    () => ({
      fetchSessions, fetchSession, deleteSession, renameSession, fetchModels, fetchFiles,
      fetchGitStatus, forkSession, healthCheck,
      fetchCheckpoints, restoreCheckpoint, dropCheckpoint, getCheckpointDiff,
      fetchPlugins, installPlugin, uninstallPlugin, togglePlugin, searchMarketplace,
      fetchMCPServers, addMCPServer, removeMCPServer, testMCPServer,
      fetchHooks, addHook, removeHook, testHook, fetchHookLogs,
      fetchContext, updateContext, fetchCapabilities,
    }),
    [
      fetchSessions, fetchSession, deleteSession, renameSession, fetchModels, fetchFiles,
      fetchGitStatus, forkSession, healthCheck,
      fetchCheckpoints, restoreCheckpoint, dropCheckpoint, getCheckpointDiff,
      fetchPlugins, installPlugin, uninstallPlugin, togglePlugin, searchMarketplace,
      fetchMCPServers, addMCPServer, removeMCPServer, testMCPServer,
      fetchHooks, addHook, removeHook, testHook, fetchHookLogs,
      fetchContext, updateContext, fetchCapabilities,
    ],
  );
}
