import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';
import { useAppState } from '../state/context.js';
import { useApi } from '../hooks/useApi.js';
import { Search, Settings, SlidersHorizontal, Grid3x3, Code2, FileText, ClipboardList, CornerDownLeft } from '../icons/index.js';
import type { SessionSummary } from '../state/types.js';
import './SearchModal.css';

interface CommandItem {
  id: string;
  label: string;
  description: string;
  icon: React.FC<{ size?: number }>;
  action: () => void;
  category?: string;
}

interface SettingJumpItem {
  id: string;
  label: string;
  description: string;
}

export const SearchModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [sessionResults, setSessionResults] = useState<SessionSummary[]>([]);
  const [fileResults, setFileResults] = useState<string[]>([]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Determine mode from query prefix
  const mode = useMemo(() => {
    if (query.startsWith('/')) return 'commands';
    if (query.startsWith('>')) return 'settings';
    return 'files';
  }, [query]);

  // Command list
  const commandItems: CommandItem[] = useMemo(() => [
    { id: 'checkpoints', label: 'Checkpoints', description: 'View and restore checkpoints', icon: ClipboardList, category: 'Panels',
      action: () => { close(); dispatch({ type: 'TOGGLE_CHECKPOINT_PANEL' }); } },
    { id: 'plugins', label: 'Plugins', description: 'Manage installed plugins', icon: Grid3x3, category: 'Settings',
      action: () => { close(); dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' }); window.__settingsTab = 'plugins'; } },
    { id: 'mcp', label: 'MCP Servers', description: 'Manage MCP connections', icon: Code2, category: 'Settings',
      action: () => { close(); dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' }); window.__settingsTab = 'mcp'; } },
    { id: 'hooks', label: 'Hooks', description: 'Configure event hooks', icon: SlidersHorizontal, category: 'Settings',
      action: () => { close(); dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' }); window.__settingsTab = 'hooks'; } },
    { id: 'settings', label: 'Settings', description: 'Open settings', icon: Settings, category: 'Navigation',
      action: () => { close(); dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' }); } },
    { id: 'new-chat', label: 'New Chat', description: 'Start a new conversation', icon: FileText, category: 'Navigation',
      action: () => { close(); dispatch({ type: 'NEW_CHAT' }); } },
    { id: 'clear-chat', label: 'Clear Chat', description: 'Clear current conversation', icon: CornerDownLeft, category: 'Navigation',
      action: () => { close(); dispatch({ type: 'CLEAR_CHAT' }); } },
    { id: 'shortcuts', label: 'Keyboard Shortcuts', description: 'View all keyboard shortcuts', icon: Search, category: 'Help',
      action: () => { close(); dispatch({ type: 'OPEN_OVERLAY', overlay: 'shortcuts' }); } },
  ], [dispatch]);

  // Setting jumps
  const settingJumps: SettingJumpItem[] = [
    { id: 'theme', label: 'Settings > Appearance', description: 'Change theme, vim mode, keybindings' },
    { id: 'general', label: 'Settings > General', description: 'Server URL, API key, model, temperature' },
    { id: 'permissions', label: 'Settings > Permissions', description: 'Permission mode configuration' },
    { id: 'context', label: 'Settings > Context', description: 'Context files and project info' },
    { id: 'plugins', label: 'Settings > Plugins', description: 'Install and manage plugins' },
    { id: 'mcp', label: 'Settings > MCP', description: 'MCP server management' },
    { id: 'hooks', label: 'Settings > Hooks', description: 'Hook configuration and logs' },
  ];

  const filteredCommands = useMemo(() => {
    if (mode !== 'commands') return [];
    const q = query.slice(1).toLowerCase().trim();
    if (!q) return commandItems;
    return commandItems.filter(
      (c) => c.label.toLowerCase().includes(q) || c.description.toLowerCase().includes(q) || (c.category || '').toLowerCase().includes(q),
    );
  }, [query, mode, commandItems]);

  const filteredSettings = useMemo(() => {
    if (mode !== 'settings') return [];
    const q = query.slice(1).toLowerCase().trim();
    if (!q) return settingJumps;
    return settingJumps.filter(
      (s) => s.label.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) || s.id.toLowerCase().includes(q),
    );
  }, [query, mode, settingJumps]);

  const combinedResults = useMemo(() => {
    if (mode === 'commands') return filteredCommands;
    if (mode === 'settings') return filteredSettings;
    // File mode: combine session results + file results
    const items: Array<SessionSummary | string> = [...sessionResults, ...fileResults];
    return items;
  }, [mode, filteredCommands, filteredSettings, sessionResults, fileResults]);

  const handleSearch = (value: string) => {
    setQuery(value);
    setSelectedIndex(0);

    if (value.startsWith('/') || value.startsWith('>')) {
      setSessionResults([]);
      setFileResults([]);
      return;
    }

    // File/search mode
    if (value.length < 2) {
      setSessionResults([]);
      setFileResults([]);
      return;
    }
    const matches = state.sessions.filter(
      (s) => s.title?.toLowerCase().includes(value.toLowerCase()),
    );
    setSessionResults(matches.slice(0, 6));

    // Search workspace files via /api/files/search
    const base = state.serverUrl.replace(/\/$/, '');
    const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}&q=${encodeURIComponent(value)}` : `?q=${encodeURIComponent(value)}`;
    fetch(`${base}/api/files/search`, {
      headers: state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : undefined,
    })
      .then((r) => r.json())
      .then((data: { files?: string[] }) => {
        setFileResults((data.files || []).slice(0, 6));
      })
      .catch(() => {});
  };

  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  const handleSelectItem = useCallback((index: number) => {
    if (mode === 'commands') {
      const cmd = filteredCommands[index];
      if (cmd) cmd.action();
      return;
    }
    if (mode === 'settings') {
      const setting = filteredSettings[index];
      if (setting) {
        close();
        dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' });
        window.__settingsTab = setting.id;
      }
      return;
    }
    // File mode - select session
    const sessionItems = sessionResults;
    if (index < sessionItems.length) {
      const session = sessionItems[index];
      close();
      api.fetchSession(session.id).then((messages) => {
        dispatch({ type: 'SET_SESSION_ID', id: session.id });
        dispatch({ type: 'SET_MESSAGES', messages });
      }).catch(() => {
        dispatch({ type: 'RECEIVE_ERROR', message: 'Failed to load session' });
      });
    }
  }, [mode, filteredCommands, filteredSettings, sessionResults, dispatch, api, close]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      close();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const max = mode === 'commands' ? filteredCommands.length
        : mode === 'settings' ? filteredSettings.length
        : sessionResults.length + fileResults.length;
      setSelectedIndex((prev) => Math.min(prev + 1, Math.max(0, max - 1)));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (mode === 'commands' && filteredCommands.length > 0) {
        handleSelectItem(selectedIndex);
      } else if (mode === 'settings' && filteredSettings.length > 0) {
        handleSelectItem(selectedIndex);
      } else if (sessionResults.length + fileResults.length > 0) {
        handleSelectItem(selectedIndex);
      }
    }
  };

  const hasResults = mode === 'commands' ? filteredCommands.length > 0
    : mode === 'settings' ? filteredSettings.length > 0
    : sessionResults.length + fileResults.length > 0;

  return (
    <div className="overlay" onClick={close}>
      <div className="search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="search-input-row">
          <Search size={16} />
          <input
            ref={inputRef}
            className="search-field"
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder={
              mode === 'commands' ? 'Type a command...'
              : mode === 'settings' ? 'Jump to settings section...'
              : 'Search sessions and files... (/ for commands, > for settings)'
            }
            onKeyDown={handleKeyDown}
          />
        </div>
        <div className="search-mode-hint">
          {mode === 'commands' && <span className="search-mode-tag">Commands</span>}
          {mode === 'settings' && <span className="search-mode-tag">Settings</span>}
          {mode === 'files' && <span className="search-mode-tag">Search</span>}
          <span className="search-mode-help">Type {mode === 'files' ? '/ for commands, > for settings' : mode === 'commands' ? '> for settings, type normally to search' : '/ for commands, type normally to search'}</span>
        </div>

        {hasResults && (
          <div className="search-results">
            {mode === 'commands' && filteredCommands.map((cmd, i) => {
              const Icon = cmd.icon;
              return (
                <button
                  key={cmd.id}
                  className={`search-result-item ${i === selectedIndex ? 'search-result-item--selected' : ''}`}
                  onClick={() => handleSelectItem(i)}
                  onMouseEnter={() => setSelectedIndex(i)}
                >
                  <div className="search-result-icon"><Icon size={15} /></div>
                  <div className="search-result-info">
                    <span className="search-result-title">
                      {cmd.label}
                      {cmd.category && <span className="search-result-category">{cmd.category}</span>}
                    </span>
                    <span className="search-result-meta">{cmd.description}</span>
                  </div>
                </button>
              );
            })}

            {mode === 'settings' && filteredSettings.map((item, i) => (
              <button
                key={item.id}
                className={`search-result-item ${i === selectedIndex ? 'search-result-item--selected' : ''}`}
                onClick={() => handleSelectItem(i)}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                <div className="search-result-icon"><Settings size={15} /></div>
                <div className="search-result-info">
                  <span className="search-result-title">{item.label}</span>
                  <span className="search-result-meta">{item.description}</span>
                </div>
              </button>
            ))}

            {mode === 'files' && (
              <>
                {sessionResults.map((s, i) => (
                  <button
                    key={s.id}
                    className={`search-result-item ${i === selectedIndex ? 'search-result-item--selected' : ''}`}
                    onClick={() => handleSelectItem(i)}
                    onMouseEnter={() => setSelectedIndex(i)}
                  >
                    <div className="search-result-icon"><FileText size={15} /></div>
                    <div className="search-result-info">
                      <span className="search-result-title">{s.title || s.id.slice(0, 12)}</span>
                      <span className="search-result-meta">{s.msg_count} msgs · {s.model?.slice(0, 20)}</span>
                    </div>
                  </button>
                ))}
                {fileResults.map((file, i) => {
                  const idx = sessionResults.length + i;
                  return (
                    <button
                      key={file}
                      className={`search-result-item ${idx === selectedIndex ? 'search-result-item--selected' : ''}`}
                      onClick={() => { close(); dispatch({ type: 'SELECT_FILE', path: file }); }}
                      onMouseEnter={() => setSelectedIndex(idx)}
                    >
                      <div className="search-result-icon"><FileText size={15} /></div>
                      <div className="search-result-info">
                        <span className="search-result-title">{file}</span>
                        <span className="search-result-meta">File</span>
                      </div>
                    </button>
                  );
                })}
              </>
            )}
          </div>
        )}

        {mode === 'files' && query.length >= 2 && !hasResults && (
          <p className="search-empty">No results found</p>
        )}
        {mode === 'commands' && query.startsWith('/') && query.length > 1 && !hasResults && (
          <p className="search-empty">No matching commands</p>
        )}
        {mode === 'settings' && query.startsWith('>') && query.length > 1 && !hasResults && (
          <p className="search-empty">No matching settings sections</p>
        )}
      </div>
    </div>
  );
};
