import * as vscode from 'vscode';
import { EventEmitter } from 'events';
import WebSocket from 'ws';

export interface WispPromptOptions {
  model?: string;
  sessionId?: string;
  showThinking?: boolean;
  permissionMode?: 'full' | 'ask_all' | 'auto_edit' | 'read_only';
  planMode?: boolean;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolApprovalRequest {
  callId: string;
  name: string;
  arguments: Record<string, unknown>;
  reason?: string;
}

export class WispClient extends EventEmitter {
  private _ws: WebSocket | null = null;
  private _serverUrl: string = '';
  private _apiKey: string = '';
  private _connected: boolean = false;
  private _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _outputChannel: vscode.OutputChannel;

  constructor() {
    super();
    this._outputChannel = vscode.window.createOutputChannel('Wisp AI');
  }

  // ── Connection ────────────────────────────────────────────────────

  connect(serverUrl: string, apiKey: string): void {
    this._serverUrl = serverUrl.replace(/\/$/, '');
    this._apiKey = apiKey;

    if (this._ws) {
      this._ws.close();
      this._ws = null;
    }

    const wsUrl = this._serverUrl
      .replace(/^http/, 'ws')
      + '/ws/agent'
      + (this._apiKey ? `?api_key=${encodeURIComponent(this._apiKey)}` : '');

    this._outputChannel.appendLine(`Wisp: connecting to ${wsUrl}`);

    try {
      this._ws = new WebSocket(wsUrl);
    } catch (e) {
      this._outputChannel.appendLine(`Wisp: WebSocket create failed: ${e}`);
      this.emit('error', 'Failed to create WebSocket connection');
      return;
    }

    this._ws.on('open', () => {
      this._connected = true;
      this._outputChannel.appendLine('Wisp: connected');
      this.emit('connected');
    });

    this._ws.on('close', (code, reason) => {
      this._connected = false;
      this._outputChannel.appendLine(`Wisp: disconnected (code=${code}: ${reason})`);
      this.emit('disconnected', code, reason.toString());
    });

    this._ws.on('error', (err) => {
      this._outputChannel.appendLine(`Wisp: socket error: ${err.message}`);
      this.emit('error', err.message);
    });

    this._ws.on('message', (data) => {
      try {
        const msg = JSON.parse(data.toString());
        this._handleMessage(msg);
      } catch {
        this._outputChannel.appendLine('Wisp: failed to parse server message');
      }
    });
  }

  disconnect(): void {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    if (this._ws) {
      this._ws.close();
      this._ws = null;
    }
    this._connected = false;
  }

  isConnected(): boolean {
    return this._connected;
  }

  // ── Message dispatch ──────────────────────────────────────────────

  private _handleMessage(msg: Record<string, unknown>): void {
    const type = msg.type as string;
    switch (type) {
      case 'token':
        this.emit('token', msg.text as string, msg.phase as string || 'content');
        break;
      case 'tool_call':
        this.emit('tool_call', {
          name: msg.name as string,
          arguments: msg.arguments as Record<string, unknown> || {},
        } as ToolCall);
        break;
      case 'tool_result':
        this.emit('tool_result', msg.name as string, msg.result as string, msg.duration_ms as number);
        break;
      case 'tool_approval_request':
        this.emit('approval_request', {
          callId: msg.call_id as string,
          name: msg.name as string,
          arguments: msg.arguments as Record<string, unknown> || {},
          reason: msg.reason as string | undefined,
        } as ToolApprovalRequest);
        break;
      case 'status':
        this.emit('status', msg.message as string, msg.level as string || 'info');
        break;
      case 'error':
        this.emit('agent_error', msg.message as string, msg.recoverable as boolean);
        break;
      case 'done':
        this.emit('done', msg.session_id as string);
        break;
      case 'complete':
        this.emit('complete', msg.session_id as string);
        break;
      case 'plan_ready':
        this.emit('plan_ready', msg.session_id as string, msg.content as string);
        break;
      case 'checkpoint_created':
        this.emit('checkpoint_created', msg.checkpoint_id as string, msg.description as string);
        break;
      case 'steering_paused':
        this.emit('steering_paused', msg.reason as string || '');
        break;
      case 'steering_resumed':
        this.emit('steering_resumed');
        break;
      case 'steering_inject':
        this.emit('steering_inject', msg.text as string || '');
        break;
      case 'pong':
        break;
      default:
        this._outputChannel.appendLine(`Wisp: unknown message type: ${type}`);
    }
  }

  // ── Outgoing messages ─────────────────────────────────────────────

  private _send(data: Record<string, unknown>): void {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
      this._outputChannel.appendLine('Wisp: cannot send — not connected');
      return;
    }
    this._ws.send(JSON.stringify(data));
  }

  sendPrompt(content: string, options: WispPromptOptions = {}): void {
    this._send({
      type: 'prompt',
      content,
      model: options.model,
      session_id: options.sessionId,
      show_thinking: options.showThinking ?? true,
      permission_mode: options.permissionMode || 'ask_all',
      plan_mode: options.planMode || false,
    });
  }

  approveTool(callId: string, approved: boolean, reason?: string): void {
    this._send({
      type: 'tool_approval',
      id: callId,
      approved,
      reason,
    });
  }

  interrupt(): void {
    this._send({ type: 'interrupt' });
  }

  pause(): void {
    this._send({ type: 'pause' });
  }

  resume(injectedText?: string): void {
    this._send({ type: 'resume', injected_text: injectedText });
  }

  // ── HTTP helpers ──────────────────────────────────────────────────

  private _apiBase(): string {
    return `${this._serverUrl}/api`;
  }

  private _headers(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this._apiKey) {
      h['X-API-Key'] = this._apiKey;
    }
    return h;
  }

  private async _httpGet<T = unknown>(path: string): Promise<T> {
    const url = `${this._apiBase()}${path}${path.includes('?') ? '&' : '?'}api-key=${encodeURIComponent(this._apiKey)}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    return resp.json() as T;
  }

  private async _httpPost<T = unknown>(path: string, body: Record<string, unknown>): Promise<T> {
    const url = `${this._apiBase()}${path}${path.includes('?') ? '&' : '?'}api-key=${encodeURIComponent(this._apiKey)}`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: this._headers(),
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${text}`);
    }
    return resp.json() as T;
  }

  async editInline(
    path: string,
    selection: string,
    instruction: string,
    model?: string,
  ): Promise<{ ok: boolean; new_text: string; diff: string }> {
    return this._httpPost('/edit/inline', { path, selection, instruction, model });
  }

  async getWorkspace(): Promise<{ path: string }> {
    return this._httpGet('/workspace');
  }

  async getGitStatus(): Promise<{
    git: boolean;
    branch?: string;
    dirty?: boolean;
    changed_files?: string[];
  }> {
    return this._httpGet('/git');
  }

  async getFiles(path?: string): Promise<unknown> {
    const q = path ? `?path=${encodeURIComponent(path)}` : '';
    return this._httpGet(`/files${q}`);
  }

  async getFileTree(): Promise<{ files: { name: string; path: string; size: number }[] }> {
    return this._httpGet('/files/tree');
  }

  async getDiagnostics(filePath: string): Promise<{ path: string; diagnostics: unknown[]; count: number }> {
    return this._httpGet(`/diagnostics?path=${encodeURIComponent(filePath)}`);
  }

  async getSuggestions(): Promise<{ suggestions: unknown[] }> {
    return this._httpGet('/suggestions');
  }

  // ── Cleanup ───────────────────────────────────────────────────────

  dispose(): void {
    this.disconnect();
    this.removeAllListeners();
    this._outputChannel.dispose();
  }
}
