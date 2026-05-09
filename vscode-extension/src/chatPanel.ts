import * as vscode from 'vscode';
import { WispClient, WispPromptOptions, ToolApprovalRequest } from './wispClient';

export class ChatPanel implements vscode.WebviewViewProvider {
  private _view?: vscode.WebviewView;
  private _client: WispClient;
  private _messageHistory: Array<{
    role: 'user' | 'assistant';
    content: string;
    thinking?: string;
    toolCalls?: Array<{ name: string; args: string; result?: string }>;
  }> = [];

  constructor(client: WispClient) {
    this._client = client;
    this._setupClientListeners();
  }

  private _setupClientListeners(): void {
    this._client.on('connected', () => this._postToWebview({ type: 'connection', status: 'connected' }));
    this._client.on('disconnected', () => this._postToWebview({ type: 'connection', status: 'disconnected' }));
    this._client.on('error', () => this._postToWebview({ type: 'connection', status: 'error' }));

    this._client.on('token', (text: string, phase: string) =>
      this._postToWebview({ type: 'token', text, phase }),
    );

    this._client.on('tool_call', (tc: { name: string; arguments: Record<string, unknown> }) =>
      this._postToWebview({
        type: 'tool_call',
        name: tc.name,
        args: JSON.stringify(tc.arguments, null, 2),
      }),
    );

    this._client.on('tool_result', (name: string, result: string, durationMs: number) =>
      this._postToWebview({
        type: 'tool_result',
        name,
        result,
        duration_ms: durationMs,
      }),
    );

    this._client.on('approval_request', (req: ToolApprovalRequest) =>
      this._postToWebview({
        type: 'tool_approval_request',
        callId: req.callId,
        name: req.name,
        args: JSON.stringify(req.arguments, null, 2),
        reason: req.reason,
      }),
    );

    this._client.on('status', (message: string) =>
      this._postToWebview({ type: 'status', message }),
    );

    this._client.on('agent_error', (message: string) =>
      this._postToWebview({ type: 'error', message }),
    );

    this._client.on('done', () => this._postToWebview({ type: 'done' }));
    this._client.on('complete', () => this._postToWebview({ type: 'complete' }));

    this._client.on('steering_paused', () =>
      this._postToWebview({ type: 'steering_paused' }),
    );

    this._client.on('steering_resumed', () =>
      this._postToWebview({ type: 'steering_resumed' }),
    );
  }

  private _postToWebview(msg: Record<string, unknown>): void {
    this._view?.webview.postMessage(msg);
  }

  // ── WebviewViewProvider ────────────────────────────────────────────

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [],
    };

    webviewView.webview.html = this._getHtml();

    webviewView.webview.onDidReceiveMessage((msg) => {
      this._handleWebviewMessage(msg);
    });

    // Send initial state
    if (this._client.isConnected()) {
      this._postToWebview({ type: 'connection', status: 'connected' });
    }
  }

  private _handleWebviewMessage(msg: Record<string, unknown>): void {
    switch (msg.type) {
      case 'sendPrompt': {
        const content = msg.content as string;
        const options: WispPromptOptions = {
          model: msg.model as string | undefined,
          showThinking: msg.showThinking as boolean | undefined ?? true,
          permissionMode: (msg.permissionMode as WispPromptOptions['permissionMode']) || 'ask_all',
        };
        this._messageHistory.push({ role: 'user', content });
        this._client.sendPrompt(content, options);
        break;
      }
      case 'interrupt':
        this._client.interrupt();
        break;
      case 'pause':
        this._client.pause();
        break;
      case 'resume': {
        const injectedText = msg.injectedText as string | undefined;
        this._client.resume(injectedText || undefined);
        break;
      }
      case 'approveTool':
        this._client.approveTool(
          msg.callId as string,
          msg.approved as boolean,
          msg.reason as string | undefined,
        );
        break;
      case 'getContext': {
        const editor = vscode.window.activeTextEditor;
        const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
        this._postToWebview({
          type: 'context',
          workspacePath: workspaceFolder?.uri.fsPath || '',
          workspaceName: workspaceFolder?.name || '',
          activeFile: editor?.document.uri.fsPath || '',
          activeFileLanguage: editor?.document.languageId || '',
          selection: editor?.document.getText(editor.selection) || '',
          hasSelection: !editor?.selection.isEmpty,
        });
        break;
      }
      case 'explainCode':
      case 'fixCode':
      case 'addTests':
      case 'reviewCode':
        this._handleCodeCommand(msg.type as string);
        break;
      case 'editInline': {
        this._client.editInline(
          msg.path as string,
          msg.selection as string,
          msg.instruction as string,
        ).then(r => this._postToWebview({ type: 'inlineEditResult', ...r }))
          .catch(e => this._postToWebview({ type: 'error', message: e.message }));
        break;
      }
    }
  }

  private _handleCodeCommand(command: string): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;

    const selection = editor.document.getText(editor.selection) || editor.document.getText();
    const language = editor.document.languageId;
    const filePath = editor.document.uri.fsPath;
    const filename = filePath.split('/').pop() || filePath;
    const selectionBlock = selection ? `\n\nFile: ${filename} (${language})\n\`\`\`${language}\n${selection}\n\`\`\`` : '';

    const prompts: Record<string, string> = {
      explainCode: `Explain this code. What does it do and how does it work?${selectionBlock}`,
      fixCode: `Fix any bugs or issues in this code. Apply the fixes directly to ${filename}.${selectionBlock}`,
      addTests: `Write thorough unit tests for this code.${selectionBlock}`,
      reviewCode: `Review this code for bugs, security issues, performance problems, and style improvements. Provide specific, actionable feedback.${selectionBlock}`,
    };

    const content = prompts[command] || '';
    if (!content) return;

    this._messageHistory.push({ role: 'user', content });
    this._client.sendPrompt(content, {
      showThinking: true,
      permissionMode: command === 'fixCode' || command === 'addTests' ? 'auto_edit' : 'ask_all',
    });

    // Focus the chat panel
    this._postToWebview({ type: 'focusInput' });
  }

  // ── External API (called from extension.ts commands) ──────────────

  explainCode(): void {
    this._handleCodeCommand('explainCode');
  }

  fixCode(): void {
    this._handleCodeCommand('fixCode');
  }

  addTests(): void {
    this._handleCodeCommand('addTests');
  }

  reviewCode(): void {
    this._handleCodeCommand('reviewCode');
  }

  customPrompt(prompt: string): void {
    const editor = vscode.window.activeTextEditor;
    let content = prompt;
    if (editor) {
      const selection = editor.document.getText(editor.selection);
      const language = editor.document.languageId;
      const filePath = editor.document.uri.fsPath;
      if (selection) {
        content = `${prompt}\n\nSelection from ${filePath.split('/').pop()} (${language}):\n\`\`\`${language}\n${selection}\n\`\`\``;
      }
    }
    this._messageHistory.push({ role: 'user', content });
    this._client.sendPrompt(content, { showThinking: true, permissionMode: 'ask_all' });
  }

  // ── HTML ──────────────────────────────────────────────────────────

  private _getHtml(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wisp Chat</title>
<style>
  :root {
    --bg: var(--vscode-sideBar-background, #1e1e2e);
    --fg: var(--vscode-sideBar-foreground, #cdd6f4);
    --border: var(--vscode-sideBar-border, #313244);
    --accent: #89b4fa;
    --accent-green: #22c55e;
    --accent-red: #ef4444;
    --accent-orange: #f59e0b;
    --bg-hover: var(--vscode-list-hoverBackground, #313244);
    --bg-input: var(--vscode-input-background, #181825);
    --text-muted: var(--vscode-descriptionForeground, #6c7086);
    --radius: 8px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--vscode-font-family, -apple-system, sans-serif);
    font-size: 13px;
    color: var(--fg);
    background: var(--bg);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Header */
  .header {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .header-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
    flex-shrink: 0;
  }
  .header-dot.connected { background: var(--accent-green); }
  .header-dot.disconnected { background: var(--text-muted); }
  .header-dot.error { background: var(--accent-red); }
  .header-label {
    font-size: 12px;
    color: var(--text-muted);
    flex: 1;
  }
  .header-context {
    font-size: 11px;
    color: var(--text-muted);
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Messages */
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .msg {
    max-width: 100%;
    animation: fadeIn 0.15s ease;
  }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  .msg.user .bubble {
    background: var(--accent);
    color: #1e1e2e;
    padding: 8px 12px;
    border-radius: var(--radius);
    margin-left: auto;
    max-width: 85%;
    width: fit-content;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.assistant .content {
    padding: 8px 0;
    line-height: 1.5;
  }
  .msg.assistant pre {
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    overflow-x: auto;
    font-size: 12px;
    margin: 6px 0;
  }
  .msg.assistant code {
    font-family: var(--vscode-editor-font-family, 'SF Mono', monospace);
    font-size: 12px;
  }
  .msg.assistant p { margin: 4px 0; }
  .msg.assistant ul, .msg.assistant ol { padding-left: 20px; margin: 4px 0; }
  .thinking-block {
    margin: 6px 0;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .thinking-summary {
    padding: 6px 10px;
    cursor: pointer;
    font-size: 11px;
    color: var(--text-muted);
    background: var(--bg-input);
    user-select: none;
  }
  .thinking-summary:hover { background: var(--bg-hover); }
  .thinking-content {
    padding: 8px 10px;
    font-size: 12px;
    color: var(--text-muted);
    border-top: 1px solid var(--border);
    max-height: 200px;
    overflow-y: auto;
    white-space: pre-wrap;
    display: none;
  }
  .thinking-block.open .thinking-content { display: block; }

  /* Tool cards */
  .tool-card {
    margin: 6px 0;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .tool-card-header {
    padding: 6px 10px;
    font-size: 12px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--bg-input);
  }
  .tool-card-header:hover { background: var(--bg-hover); }
  .tool-card-icon { font-size: 14px; }
  .tool-card-name { font-weight: 600; color: var(--accent); }
  .tool-card-args {
    font-size: 11px;
    color: var(--text-muted);
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tool-card-body {
    padding: 8px 10px;
    border-top: 1px solid var(--border);
    font-size: 12px;
    max-height: 200px;
    overflow-y: auto;
    display: none;
  }
  .tool-card-body pre {
    margin: 0;
    padding: 6px;
    background: var(--bg-input);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .tool-card.open .tool-card-body { display: block; }

  /* Approval cards */
  .approval-card {
    margin: 6px 0;
    border: 1px solid var(--accent-orange);
    border-radius: 6px;
    padding: 10px;
    background: var(--bg-input);
  }
  .approval-card .tool-name {
    font-weight: 600;
    color: var(--accent-orange);
    margin-bottom: 4px;
  }
  .approval-card .tool-args {
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 8px;
    max-height: 80px;
    overflow-y: auto;
  }
  .approval-card .tool-args pre { margin: 0; }
  .approval-actions {
    display: flex;
    gap: 6px;
  }
  .approval-actions button {
    padding: 4px 14px;
    border-radius: 4px;
    border: none;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
  }
  .btn-approve { background: var(--accent-green); color: #000; }
  .btn-deny { background: var(--accent-red); color: #fff; }
  .btn-approve:hover { opacity: 0.85; }
  .btn-deny:hover { opacity: 0.85; }

  /* Input area */
  .input-area {
    border-top: 1px solid var(--border);
    padding: 8px 10px;
    flex-shrink: 0;
  }
  .context-actions {
    display: flex;
    gap: 4px;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }
  .context-btn {
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: var(--bg-input);
    color: var(--text-muted);
    cursor: pointer;
  }
  .context-btn:hover {
    background: var(--bg-hover);
    color: var(--fg);
  }
  .input-row {
    display: flex;
    gap: 6px;
    align-items: flex-end;
  }
  textarea {
    flex: 1;
    resize: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px;
    font-family: inherit;
    font-size: 13px;
    background: var(--bg-input);
    color: var(--fg);
    min-height: 34px;
    max-height: 120px;
    outline: none;
  }
  textarea:focus { border-color: var(--accent); }
  button {
    padding: 6px 12px;
    border-radius: 6px;
    border: none;
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
    transition: opacity 0.1s;
  }
  button:disabled { opacity: 0.4; cursor: default; }
  .btn-send { background: var(--accent); color: #000; }
  .btn-stop { background: var(--accent-red); color: #fff; }
  .btn-pause { background: var(--accent-orange); color: #000; }
  .btn-resume { background: var(--accent-green); color: #000; }

  .inject-row {
    display: none;
    margin-top: 6px;
    gap: 6px;
    align-items: flex-end;
  }
  .inject-row.visible { display: flex; }
  .inject-row textarea { flex: 1; min-height: 28px; max-height: 60px; font-size: 12px; }

  .streaming-indicator {
    display: none;
    font-size: 11px;
    color: var(--text-muted);
    padding: 4px 0;
    gap: 6px;
    align-items: center;
  }
  .streaming-indicator.visible { display: flex; }
  .typing-dot {
    width: 5px; height: 5px;
    border-radius: 50%;
    background: var(--accent);
    animation: bounce 0.6s infinite alternate;
  }
  .typing-dot:nth-child(2) { animation-delay: 0.2s; }
  .typing-dot:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce {
    0% { transform: translateY(0); }
    100% { transform: translateY(-4px); }
  }
</style>
</head>
<body>

<div class="header">
  <span class="header-dot" id="statusDot"></span>
  <span class="header-label" id="statusLabel">Disconnected</span>
  <span class="header-context" id="contextLabel"></span>
</div>

<div class="messages" id="messages"></div>

<div id="streamingBar" class="streaming-indicator">
  <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
  Wisp is thinking...
</div>

<div class="input-area">
  <div class="context-actions">
    <button class="context-btn" data-action="explainCode">Explain</button>
    <button class="context-btn" data-action="fixCode">Fix</button>
    <button class="context-btn" data-action="addTests">Test</button>
    <button class="context-btn" data-action="reviewCode">Review</button>
  </div>
  <div class="input-row">
    <textarea id="input" placeholder="Ask Wisp..." rows="1"></textarea>
    <button class="btn-send" id="btnSend" style="display:none">Send</button>
    <button class="btn-stop" id="btnStop" style="display:none">Stop</button>
    <button class="btn-pause" id="btnPause" style="display:none">Pause</button>
    <button class="btn-resume" id="btnResume" style="display:none">Resume</button>
  </div>
  <div class="inject-row" id="injectRow">
    <textarea id="injectInput" placeholder="Steering hint (optional)..." rows="1"></textarea>
    <button class="btn-resume" id="btnResumeWithInject">Resume</button>
  </div>
</div>

<script>
(function() {
  const vscode = acquireVsCodeApi();
  const messagesEl = document.getElementById('messages');
  const inputEl = document.getElementById('input');
  const injectInputEl = document.getElementById('injectInput');
  const injectRow = document.getElementById('injectRow');
  const streamingBar = document.getElementById('streamingBar');
  const statusDot = document.getElementById('statusDot');
  const statusLabel = document.getElementById('statusLabel');
  const contextLabel = document.getElementById('contextLabel');

  const btnSend = document.getElementById('btnSend');
  const btnStop = document.getElementById('btnStop');
  const btnPause = document.getElementById('btnPause');
  const btnResumeBtn = document.getElementById('btnResume');
  const btnResumeWithInject = document.getElementById('btnResumeWithInject');

  let currentMsgDiv = null;
  let currentThinkingBlock = null;
  let state = 'idle'; // idle | streaming | paused
  let lastToolCard = null;

  function setState(s) {
    state = s;
    btnSend.style.display = (s === 'idle') ? '' : 'none';
    btnStop.style.display = (s === 'streaming' || s === 'paused') ? '' : 'none';
    btnPause.style.display = (s === 'streaming') ? '' : 'none';
    btnResumeBtn.style.display = (s === 'paused') ? '' : 'none';
    injectRow.classList.toggle('visible', s === 'paused');
    streamingBar.classList.toggle('visible', s === 'streaming');
    inputEl.disabled = (s !== 'idle');
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function ensureCurrentMsg() {
    if (!currentMsgDiv) {
      currentMsgDiv = document.createElement('div');
      currentMsgDiv.className = 'msg assistant';
      currentMsgDiv.innerHTML = '<div class="content"></div>';
      messagesEl.appendChild(currentMsgDiv);
      lastToolCard = null;
    }
  }

  function finalizeMsg() {
    currentMsgDiv = null;
    currentThinkingBlock = null;
    lastToolCard = null;
  }

  // ── Markdown render (lightweight) ────────────────────────────
  function renderMarkdown(text) {
    // Backtick code blocks
    text = text.replace(/\\\`\\\`\\\`(\\w*)\\n([\\s\\S]*?)\\\`\\\`\\\`/g, function(_, lang, code) {
      return '<pre><code class="language-' + (lang || '') + '">' + escapeHtml(code) + '</code></pre>';
    });
    // Inline code
    text = text.replace(/\\\`([^\\\`]+)\\\`/g, '<code>$1</code>');
    // Bold / italic
    text = text.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
    text = text.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
    // Headers
    text = text.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    text = text.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    text = text.replace(/^# (.+)$/gm, '<h2>$1</h2>');
    // Lists
    text = text.replace(/^- (.+)$/gm, '<li>$1</li>');
    text = text.replace(/((?:<li>.*<\\/li>\\n?)+)/g, '<ul>$1</ul>');
    // Paragraphs
    text = text.replace(/\\n\\n/g, '</p><p>');
    text = '<p>' + text + '</p>';
    // Clean empty paragraphs
    text = text.replace(/<p><\\/p>/g, '');
    return text;
  }

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function appendContent(html) {
    ensureCurrentMsg();
    const contentEl = currentMsgDiv.querySelector('.content');
    contentEl.innerHTML += html;
    scrollBottom();
  }

  function appendText(text, phase) {
    if (phase === 'thinking') {
      ensureCurrentMsg();
      if (!currentThinkingBlock) {
        currentThinkingBlock = document.createElement('div');
        currentThinkingBlock.className = 'thinking-block';
        currentThinkingBlock.innerHTML =
          '<div class="thinking-summary">Thinking...</div>' +
          '<div class="thinking-content"></div>';
        currentThinkingBlock.querySelector('.thinking-summary').onclick = function() {
          currentThinkingBlock.classList.toggle('open');
          scrollBottom();
        };
        currentMsgDiv.querySelector('.content').appendChild(currentThinkingBlock);
      }
      const tc = currentThinkingBlock.querySelector('.thinking-content');
      tc.textContent += text;
      scrollBottom();
    } else {
      ensureCurrentMsg();
      currentThinkingBlock = null;
      const contentEl = currentMsgDiv.querySelector('.content');
      // Accumulate, then render on done
      if (!contentEl._rawText) contentEl._rawText = '';
      contentEl._rawText += text;
      contentEl.innerHTML = renderMarkdown(contentEl._rawText);
      scrollBottom();
    }
  }

  function addToolCard(name, args) {
    ensureCurrentMsg();
    const card = document.createElement('div');
    card.className = 'tool-card';
    card.innerHTML =
      '<div class="tool-card-header">' +
        '<span class="tool-card-icon">&#9881;</span>' +
        '<span class="tool-card-name">' + escapeHtml(name) + '</span>' +
        '<span class="tool-card-args">' + escapeHtml(args.slice(0, 80)) + '</span>' +
      '</div>' +
      '<div class="tool-card-body"><pre>' + escapeHtml(args) + '</pre></div>';
    card.querySelector('.tool-card-header').onclick = function() {
      card.classList.toggle('open');
      scrollBottom();
    };
    currentMsgDiv.querySelector('.content').appendChild(card);
    lastToolCard = card;
    scrollBottom();
  }

  function appendToolResult(name, result, durationMs) {
    if (lastToolCard) {
      const body = lastToolCard.querySelector('.tool-card-body');
      const durStr = durationMs ? ' (' + (durationMs / 1000).toFixed(1) + 's)' : '';
      body.innerHTML = '<pre>Result' + durStr + ':\\n' + escapeHtml(result || '(empty)') + '</pre>';
      lastToolCard.classList.add('open');
    }
    scrollBottom();
  }

  function addApprovalCard(callId, name, args, reason) {
    ensureCurrentMsg();
    const card = document.createElement('div');
    card.className = 'approval-card';
    card.id = 'approval-' + callId;
    card.innerHTML =
      '<div class="tool-name">Approve: ' + escapeHtml(name) + '</div>' +
      (reason ? '<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">' + escapeHtml(reason) + '</div>' : '') +
      '<div class="tool-args"><pre>' + escapeHtml(args) + '</pre></div>' +
      '<div class="approval-actions">' +
        '<button class="btn-approve">Approve</button>' +
        '<button class="btn-deny">Deny</button>' +
      '</div>';
    card.querySelector('.btn-approve').onclick = function() {
      vscode.postMessage({ type: 'approveTool', callId: callId, approved: true });
      card.remove();
    };
    card.querySelector('.btn-deny').onclick = function() {
      vscode.postMessage({ type: 'approveTool', callId: callId, approved: false });
      card.remove();
    };
    currentMsgDiv.querySelector('.content').appendChild(card);
    scrollBottom();
  }

  function addUserMessage(text) {
    finalizeMsg();
    const div = document.createElement('div');
    div.className = 'msg user';
    div.innerHTML = '<div class="bubble">' + escapeHtml(text) + '</div>';
    messagesEl.appendChild(div);
    scrollBottom();
  }

  // ── IPC from extension ───────────────────────────────────────
  window.addEventListener('message', function(e) {
    const msg = e.data;
    switch (msg.type) {
      case 'connection':
        statusDot.className = 'header-dot ' + msg.status;
        statusLabel.textContent = msg.status === 'connected' ? 'Connected' :
                                  msg.status === 'error' ? 'Error' : 'Disconnected';
        break;
      case 'context':
        contextLabel.textContent = msg.workspaceName || '';
        break;
      case 'token':
        appendText(msg.text, msg.phase);
        break;
      case 'tool_call':
        addToolCard(msg.name, msg.args);
        break;
      case 'tool_result':
        appendToolResult(msg.name, msg.result, msg.duration_ms);
        break;
      case 'tool_approval_request':
        addApprovalCard(msg.callId, msg.name, msg.args, msg.reason);
        break;
      case 'done':
        finalizeMsg();
        setState('idle');
        break;
      case 'complete':
        finalizeMsg();
        setState('idle');
        break;
      case 'steering_paused':
        setState('paused');
        break;
      case 'steering_resumed':
        setState('streaming');
        break;
      case 'status':
        // Status messages show as subtle text
        break;
      case 'error':
        appendContent('<div style="color:var(--accent-red);padding:4px 0">Error: ' + escapeHtml(msg.message) + '</div>');
        break;
      case 'inlineEditResult':
        if (msg.ok) {
          appendContent('<div style="color:var(--accent-green);padding:4px 0">Edit applied.</div>');
        }
        break;
      case 'focusInput':
        inputEl.focus();
        break;
    }
  });

  // ── Send ─────────────────────────────────────────────────────
  function sendPrompt(text) {
    if (!text.trim()) return;
    addUserMessage(text);
    vscode.postMessage({ type: 'sendPrompt', content: text, showThinking: true });
    inputEl.value = '';
    inputEl.style.height = 'auto';
    setState('streaming');
  }

  // ── Event listeners ──────────────────────────────────────────
  btnSend.onclick = function() { sendPrompt(inputEl.value); };
  btnStop.onclick = function() {
    vscode.postMessage({ type: 'interrupt' });
    setState('idle');
  };
  btnPause.onclick = function() { vscode.postMessage({ type: 'pause' }); };
  btnResumeBtn.onclick = function() { vscode.postMessage({ type: 'resume' }); };
  btnResumeWithInject.onclick = function() {
    vscode.postMessage({ type: 'resume', injectedText: injectInputEl.value });
    injectInputEl.value = '';
    setState('streaming');
  };

  inputEl.onkeydown = function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (state === 'idle') sendPrompt(inputEl.value);
    }
  };
  inputEl.oninput = function() {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
  };

  document.querySelectorAll('.context-btn').forEach(function(btn) {
    btn.onclick = function() {
      vscode.postMessage({ type: btn.dataset.action });
    };
  });

  // Request context on load
  vscode.postMessage({ type: 'getContext' });
  setState('idle');
  inputEl.focus();
})();
</script>
</body>
</html>`;
  }
}
