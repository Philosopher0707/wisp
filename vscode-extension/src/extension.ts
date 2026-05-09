import * as vscode from 'vscode';
import { WispClient } from './wispClient';
import { ChatPanel } from './chatPanel';

let client: WispClient;
let chatPanel: ChatPanel;
let statusBarItem: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext): void {
  // ── Client ──────────────────────────────────────────────────────
  client = new WispClient();
  context.subscriptions.push(client);

  // ── Chat panel ──────────────────────────────────────────────────
  chatPanel = new ChatPanel(client);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('wisp.chatPanel', chatPanel),
  );

  // ── Status bar ──────────────────────────────────────────────────
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  statusBarItem.command = 'wisp.focusChat';
  statusBarItem.text = '$(sparkle) Wisp';
  statusBarItem.tooltip = 'Wisp AI — disconnected';
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  client.on('connected', () => {
    statusBarItem.text = '$(sparkle-filled) Wisp';
    statusBarItem.tooltip = 'Wisp AI — connected';
  });
  client.on('disconnected', () => {
    statusBarItem.text = '$(sparkle) Wisp';
    statusBarItem.tooltip = 'Wisp AI — disconnected';
  });

  // ── Auto-connect ────────────────────────────────────────────────
  const config = vscode.workspace.getConfiguration('wisp');
  if (config.get<boolean>('autoConnect', true)) {
    connectFromConfig();
  }

  // ── Config change listener ──────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('wisp')) {
        connectFromConfig();
      }
    }),
  );

  // ── Commands ────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('wisp.focusChat', () =>
      vscode.commands.executeCommand('wisp.chatPanel.focus'),
    ),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('wisp.explainCode', () => {
      vscode.commands.executeCommand('wisp.chatPanel.focus');
      chatPanel.explainCode();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('wisp.fixCode', () => {
      vscode.commands.executeCommand('wisp.chatPanel.focus');
      chatPanel.fixCode();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('wisp.addTests', () => {
      vscode.commands.executeCommand('wisp.chatPanel.focus');
      chatPanel.addTests();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('wisp.reviewCode', () => {
      vscode.commands.executeCommand('wisp.chatPanel.focus');
      chatPanel.reviewCode();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('wisp.customPrompt', async () => {
      const prompt = await vscode.window.showInputBox({
        prompt: 'What should Wisp do?',
        placeHolder: 'e.g., "Refactor this to use async/await"',
      });
      if (prompt) {
        vscode.commands.executeCommand('wisp.chatPanel.focus');
        chatPanel.customPrompt(prompt);
      }
    }),
  );
}

function connectFromConfig(): void {
  const config = vscode.workspace.getConfiguration('wisp');
  const serverUrl = config.get<string>('serverUrl', 'http://localhost:8000');
  const apiKey = config.get<string>('apiKey', '');
  client.connect(serverUrl, apiKey);
}

export function deactivate(): void {
  if (client) {
    client.dispose();
  }
}
