import React, { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi, type GitStatus } from '../../hooks/useApi.js';
import { Folder, Code2, Download, GitBranch, PanelLeft } from '../../icons/index.js';
import { IconButton } from '../common/IconButton.js';
import { PrincipalCapabilityChip } from '../PrincipalCapabilityChip.js';
import type { CapabilityResponse } from '../../hooks/useApi.js';
import { DOCK_TABS } from '../dock/Dock.js';
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
  // `null` is a real third state, not a loading placeholder: it means the
  // server could not be read, and the chip renders that as unknown.
  const [caps, setCaps] = useState<CapabilityResponse | null | undefined>(undefined);

  // Re-read the surface when the server or credential changes: a chip
  // that survives a re-login would keep showing the PREVIOUS principal's
  // authority, which is the most dangerous thing this component could do.
  useEffect(() => {
    let cancelled = false;
    setCaps(undefined);
    api.fetchCapabilities()
      .then((d) => { if (!cancelled) setCaps(d); })
      .catch(() => { if (!cancelled) setCaps(null); });
    return () => { cancelled = true; };
  }, [api]);

  // Fetch git status when workspace changes
  useEffect(() => {
    if (!state.workspacePath) return;
    api.fetchGitStatus().then(setGit).catch(() => setGit(null));
  }, [state.workspacePath, api]);

  // Fetch sandbox status
  useEffect(() => {
    if (state.connection !== 'connected') return;
    const base = state.serverUrl.replace(/\/$/, '');

    fetch(`${base}/api/sandbox/status`, {
      headers: state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : undefined,
    })
      .then((r) => r.json())
      .then((data: { type?: string }) => {
        if (data.type) setSandboxType(data.type);
      })
      .catch(() => {});
  }, [state.serverUrl, state.apiKey, state.connection]);

  const openVSCode = useCallback(async () => {
    const workspacePath = state.workspacePath;
    if (!workspacePath) return;

    if (window.wisp?.openInVSCode) {
      try {
        await window.wisp.openInVSCode(workspacePath);
        return;
      } catch {
        // Fallback to vscode:// protocol
      }
    }
    // Protocol fallback (browser / other platforms)
    window.open(`vscode://file/${workspacePath}`, '_blank');
  }, [state.workspacePath]);

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

  const title = state.sessions.find((x) => x.id === state.sessionId)?.title || (state.messages.length > 0 ? 'Current session' : 'New session');

  return (
    <header className={`topbar${state.sidebarCollapsed ? ' topbar--sidebar-hidden' : ''}`}>
      <div className="topbar-title">
        {state.sidebarCollapsed && (
          <IconButton icon={PanelLeft} size={16} title="Show sidebar" onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })} />
        )}
        <span className="topbar-title-text" title={title}>{title}</span>
      </div>
      <div className="topbar-meta">
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
        <PrincipalCapabilityChip data={caps ?? null} loading={caps === undefined} />
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
        <span className="topbar-tools">
          <IconButton icon={Code2} size={17} title="Open in VS Code" onClick={openVSCode} />
          <IconButton icon={Download} size={17} title="Export conversation" onClick={handleExport} />
          <span className="topbar-divider" />
        </span>
        {DOCK_TABS.map(({ id, label, icon }) => (
          <IconButton
            key={id}
            icon={icon}
            size={17}
            title={`${label}${state.rightPanelOpen && state.dockTab === id ? ' (click to close)' : ''}`}
            active={state.rightPanelOpen && state.dockTab === id}
            onClick={() => dispatch({ type: 'SET_DOCK_TAB', tab: id })}
          />
        ))}
      </div>
    </header>
  );
};
