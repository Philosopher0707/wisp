import React, { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi, type GitStatus } from '../../hooks/useApi.js';
import { Sparkles, User, Folder, Square, Code2, Download, GitBranch } from '../../icons/index.js';
import { PillButton } from '../common/PillButton.js';
import { IconButton } from '../common/IconButton.js';
import './TopBar.css';

const STATUS_LABELS: Record<string, string> = {
  disconnected: 'Disconnected',
  connecting: 'Connecting...',
  connected: 'Connected',
  error: 'Connection Error',
};

function messagesToMarkdown(messages: { role: string; content: string; thinking?: string }[]): string {
  const lines: string[] = ['# Wisp Conversation\n'];
  for (const msg of messages) {
    if (msg.role === 'user') {
      lines.push(`## You\n\n${msg.content}\n`);
    } else if (msg.role === 'assistant') {
      if (msg.thinking) {
        lines.push(`<details>\n<summary>Thinking...</summary>\n\n${msg.thinking}\n\n</details>\n`);
      }
      lines.push(`## Assistant\n\n${msg.content}\n`);
    }
  }
  return lines.join('\n');
}

export const TopBar: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [git, setGit] = useState<GitStatus | null>(null);
  const [sandboxType, setSandboxType] = useState<string>('host');

  // Fetch git status when workspace changes
  useEffect(() => {
    if (!state.workspacePath) return;
    api.fetchGitStatus().then(setGit).catch(() => setGit(null));
  }, [state.workspacePath, api]);

  // Fetch sandbox status
  useEffect(() => {
    if (state.connection !== 'connected') return;
    const base = state.serverUrl.replace(/\/$/, '');
    const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';
    fetch(`${base}/api/sandbox/status${params}`, {
      headers: state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : undefined,
    })
      .then((r) => r.json())
      .then((data: { type?: string }) => {
        if (data.type) setSandboxType(data.type);
      })
      .catch(() => {});
  }, [state.serverUrl, state.apiKey, state.connection]);

  const openVSCode = useCallback(async () => {
    try {
      const base = state.serverUrl.replace(/\/$/, '');
      const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';
      const resp = await fetch(`${base}/api/workspace${params}`, {
        headers: state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : undefined,
      });
      const data = await resp.json() as { path?: string };
      const workspacePath = data.path;
      if (!workspacePath) return;

      if (window.wisp?.openInVSCode) {
        await window.wisp.openInVSCode(workspacePath);
      } else {
        window.open(`vscode://file/${workspacePath}`, '_blank');
      }
    } catch { /* ignore */ }
  }, [state.serverUrl, state.apiKey]);

  const handleExport = useCallback(() => {
    const md = messagesToMarkdown(state.messages);
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ts = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
    a.download = `wisp-chat-${ts}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [state.messages]);

  const wsLabel = state.workspacePath
    ? state.workspacePath.split('/').pop() || state.workspacePath
    : '';

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button
          className="topbar-new-chat-btn"
          onClick={() => dispatch({ type: 'NEW_CHAT' })}
        >
          New chat
        </button>
        <PillButton variant="filled" icon={Sparkles} color="purple">
          Get Plus
        </PillButton>
      </div>
      <div className="topbar-center">
        {wsLabel && (
          <span className="topbar-ws-label" title={state.workspacePath}>
            <Folder size={12} />
            <span>{wsLabel}</span>
          </span>
        )}
        {git?.git && git.branch && (
          <span className="topbar-git" title={`Branch: ${git.branch}${git.dirty ? ' (uncommitted changes)' : ''}`}>
            <GitBranch size={12} />
            <span className="topbar-git-branch">{git.branch}</span>
            {git.dirty && <span className="topbar-git-dirty" />}
          </span>
        )}
        <span
          className={`topbar-sandbox topbar-sandbox--${sandboxType}`}
          title={`Sandbox: ${sandboxType === 'docker' ? 'Docker container' : 'Host machine'}`}
        >
          <span className={`topbar-sandbox-dot topbar-sandbox-dot--${sandboxType}`} />
          {sandboxType === 'docker' ? 'Docker' : 'Host'}
        </span>
        <span
          className={`topbar-status topbar-status--${state.connection}`}
          title={STATUS_LABELS[state.connection] || state.connection}
        >
          <span className="topbar-status-dot" />
          {state.connection !== 'connected' && (
            <span className="topbar-status-label">
              {STATUS_LABELS[state.connection]}
            </span>
          )}
        </span>
      </div>
      <div className="topbar-right">
        <IconButton
          icon={Code2}
          size={18}
          title="Open in VS Code"
          onClick={openVSCode}
        />
        <IconButton
          icon={Download}
          size={18}
          title="Export conversation"
          onClick={handleExport}
        />
        <IconButton icon={User} size={18} title="Account" />
        <IconButton
          icon={Folder}
          size={18}
          title="Files"
          active={state.rightPanelOpen}
          onClick={() => dispatch({ type: 'TOGGLE_RIGHT_PANEL' })}
        />
        <IconButton icon={Square} size={18} title="New Window" />
      </div>
    </header>
  );
};
