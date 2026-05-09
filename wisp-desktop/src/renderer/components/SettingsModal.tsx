import React, { useState, useEffect, useCallback } from 'react';
import { useAppState } from '../state/context.js';
import { useApi } from '../hooks/useApi.js';
import type { PluginInfo, MCPServerInfo, HookInfo, HookLogEntry, MarketplaceItem, ContextResponse } from '../hooks/useApi.js';
import { ThemeSelector } from './chat/ThemeSelector.js';
import { Search, RefreshCw, Download, Trash2, Plus, FileText } from '../icons/index.js';
import './SettingsModal.css';

const STORAGE_KEY_SERVER = 'wisp_server_url';
const STORAGE_KEY_APIKEY = 'wisp_api_key';

const DEFAULT_SYSTEM_PROMPT = 'You are Wisp, an expert software engineer AI assistant. You write clean, secure, well-tested code. You think step-by-step before implementing. You use tools to read, edit, and run code. You explain your reasoning briefly and focus on delivering working solutions.';

type SettingsTab = 'general' | 'appearance' | 'permissions' | 'context' | 'plugins' | 'mcp' | 'hooks';

const TABS: { id: SettingsTab; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'permissions', label: 'Permissions' },
  { id: 'context', label: 'Context' },
  { id: 'plugins', label: 'Plugins' },
  { id: 'mcp', label: 'MCP' },
  { id: 'hooks', label: 'Hooks' },
];

const PERMISSION_MODES = [
  { value: 'full', label: 'Full Access', desc: 'All tools run automatically without prompting' },
  { value: 'ask_all', label: 'Ask All', desc: 'Prompt before every tool invocation' },
  { value: 'auto_edit', label: 'Auto Edit', desc: 'Auto-approve file edits; prompt for destructive ops' },
  { value: 'read_only', label: 'Read Only', desc: 'Only read operations are allowed; no file writes' },
] as const;

const HOOK_EVENTS = [
  'pre_tool_use',
  'post_tool_use',
  'pre_message',
  'post_message',
  'on_session_start',
  'on_session_end',
  'on_error',
];

export const SettingsModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [activeTab, setActiveTab] = useState<SettingsTab>('general');
  const [saved, setSaved] = useState(false);

  // General
  const [serverUrl, setServerUrl] = useState(localStorage.getItem(STORAGE_KEY_SERVER) || state.serverUrl);
  const [apiKey, setApiKey] = useState(localStorage.getItem(STORAGE_KEY_APIKEY) || state.apiKey);
  const [systemPrompt, setSystemPrompt] = useState(state.systemPrompt || DEFAULT_SYSTEM_PROMPT);
  const [temperature, setTemperature] = useState(() => {
    const stored = localStorage.getItem('wisp_temperature');
    return stored ? parseFloat(stored) : 0.7;
  });

  // Appearance
  const [vimMode, setVimMode] = useState(state.vimMode);
  const [rebindingKey, setRebindingKey] = useState<string | null>(null);

  // Permissions
  const [permissionMode, setPermissionMode] = useState(state.permissionMode);

  // Plugins
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [marketQuery, setMarketQuery] = useState('');
  const [marketResults, setMarketResults] = useState<MarketplaceItem[]>([]);
  const [pluginsLoading, setPluginsLoading] = useState(false);
  const [pluginError, setPluginError] = useState<string | null>(null);
  const [showInstallForm, setShowInstallForm] = useState<'marketplace' | 'directory' | null>(null);
  const [installPath, setInstallPath] = useState('');

  // MCP
  const [mcpServers, setMcpServers] = useState<MCPServerInfo[]>([]);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [showAddMcp, setShowAddMcp] = useState(false);
  const [mcpForm, setMcpForm] = useState({ name: '', transport: 'stdio', command: '', url: '', auth: 'none', alwaysLoad: false });
  const [testingMcp, setTestingMcp] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; latency_ms: number }>>({});

  // Hooks
  const [hooks, setHooks] = useState<HookInfo[]>([]);
  const [hookLogs, setHookLogs] = useState<HookLogEntry[]>([]);
  const [hooksLoading, setHooksLoading] = useState(false);
  const [showAddHook, setShowAddHook] = useState(false);
  const [hookForm, setHookForm] = useState({ name: '', event: 'pre_tool_use', command: '', timeout: 30, matcher: '', enabled: true });

  // Context
  const [contextData, setContextData] = useState<ContextResponse | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
  const [expandedContextFile, setExpandedContextFile] = useState<string | null>(null);

  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  const handleSave = () => {
    const url = serverUrl.replace(/\/$/, '');
    localStorage.setItem(STORAGE_KEY_SERVER, url);
    if (apiKey) localStorage.setItem(STORAGE_KEY_APIKEY, apiKey);
    else localStorage.removeItem(STORAGE_KEY_APIKEY);
    localStorage.setItem('wisp_system_prompt', systemPrompt);
    localStorage.setItem('wisp_temperature', String(temperature));
    localStorage.setItem('wisp_permission_mode', permissionMode);
    localStorage.setItem('wisp_vim_mode', String(vimMode));
    dispatch({ type: 'SET_SYSTEM_PROMPT', prompt: systemPrompt });
    dispatch({ type: 'SET_PERMISSION_MODE', mode: permissionMode as 'full' | 'ask_all' | 'auto_edit' | 'read_only' });
    if (vimMode !== state.vimMode) dispatch({ type: 'TOGGLE_VIM_MODE' });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const resetSystemPrompt = () => setSystemPrompt(DEFAULT_SYSTEM_PROMPT);

  // Loaders
  const loadPlugins = useCallback(async () => {
    setPluginsLoading(true); setPluginError(null);
    try { setPlugins(await api.fetchPlugins()); } catch { setPluginError('Failed to load plugins'); }
    finally { setPluginsLoading(false); }
  }, [api]);

  const loadMCPServers = useCallback(async () => {
    setMcpLoading(true); setMcpError(null);
    try { setMcpServers(await api.fetchMCPServers()); } catch { setMcpError('Failed to load MCP servers'); }
    finally { setMcpLoading(false); }
  }, [api]);

  const loadHooks = useCallback(async () => {
    setHooksLoading(true);
    try {
      const [h, l] = await Promise.all([api.fetchHooks(), api.fetchHookLogs()]);
      setHooks(h); setHookLogs(l);
    } catch { /* silent */ }
    finally { setHooksLoading(false); }
  }, [api]);

  const loadContext = useCallback(async () => {
    setContextLoading(true); setContextError(null);
    try { setContextData(await api.fetchContext()); } catch { setContextError('Failed to load context'); }
    finally { setContextLoading(false); }
  }, [api]);

  // Jump to requested tab from search modal
  useEffect(() => {
    const target = window.__settingsTab;
    if (target && TABS.some((t) => t.id === target)) {
      setActiveTab(target as SettingsTab);
      delete window.__settingsTab;
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'plugins') loadPlugins();
    else if (activeTab === 'mcp') loadMCPServers();
    else if (activeTab === 'hooks') loadHooks();
    else if (activeTab === 'context') loadContext();
  }, [activeTab, loadPlugins, loadMCPServers, loadHooks, loadContext]);

  // Plugin actions
  const handleTogglePlugin = async (name: string, enabled: boolean) => {
    if (await api.togglePlugin(name, !enabled)) {
      setPlugins((p) => p.map((x) => x.name === name ? { ...x, enabled: !enabled } : x));
    }
  };
  const handleUninstallPlugin = async (name: string) => {
    if (await api.uninstallPlugin(name)) setPlugins((p) => p.filter((x) => x.name !== name));
  };
  const handleInstallFromDir = async () => {
    if (!installPath.trim()) return;
    if (await api.installPlugin(installPath.trim())) { setInstallPath(''); setShowInstallForm(null); loadPlugins(); }
    else setPluginError('Installation failed. Check the path.');
  };
  const handleMarketSearch = async () => {
    if (marketQuery.length < 2) return;
    setMarketResults(await api.searchMarketplace(marketQuery));
  };

  // MCP actions
  const handleAddMCPServer = async () => {
    if (!mcpForm.name.trim()) return;
    if (await api.addMCPServer({
      name: mcpForm.name.trim(), transport: mcpForm.transport,
      command: mcpForm.transport === 'stdio' ? mcpForm.command : undefined,
      url: mcpForm.transport !== 'stdio' ? mcpForm.url : undefined,
      auth: mcpForm.auth, always_load: mcpForm.alwaysLoad,
    })) {
      setShowAddMcp(false);
      setMcpForm({ name: '', transport: 'stdio', command: '', url: '', auth: 'none', alwaysLoad: false });
      loadMCPServers();
    } else setMcpError('Failed to add MCP server');
  };
  const handleRemoveMCPServer = async (name: string) => { if (await api.removeMCPServer(name)) loadMCPServers(); };
  const handleTestMCPServer = async (name: string) => {
    setTestingMcp(name);
    const result = await api.testMCPServer(name);
    setTestResults((p) => ({ ...p, [name]: result }));
    setTestingMcp(null);
  };

  // Hook actions
  const handleAddHook = async () => {
    if (!hookForm.name.trim() || !hookForm.command.trim()) return;
    if (await api.addHook({
      name: hookForm.name.trim(), event: hookForm.event, command: hookForm.command.trim(),
      timeout: hookForm.timeout, matcher: hookForm.matcher || undefined, enabled: hookForm.enabled,
    })) {
      setShowAddHook(false);
      setHookForm({ name: '', event: 'pre_tool_use', command: '', timeout: 30, matcher: '', enabled: true });
      loadHooks();
    }
  };
  const handleRemoveHook = async (name: string) => { if (await api.removeHook(name)) loadHooks(); };
  const handleToggleHook = async (name: string, enabled: boolean) => {
    await api.addHook({ name, enabled: !enabled });
    loadHooks();
  };
  const handleTestHook = async (name: string) => {
    const r = await api.testHook(name, { sample: true, timestamp: new Date().toISOString() });
    setHookLogs((p) => [{ hook_name: name, event: 'test', result: r.result, timestamp: new Date().toISOString() }, ...p.slice(0, 19)]);
  };

  // Context
  const handleReloadContext = () => { loadContext(); dispatch({ type: 'CONTEXT_LOADED', path: '__all__' }); };

  // Keybindings
  const defaultKeybindings: Record<string, string> = {
    'New Chat': 'Cmd+N', 'Command Palette': 'Cmd+K', 'Quick File': 'Cmd+P',
    'Search Conversation': 'Cmd+F', 'Toggle Thinking': 'Cmd+T', 'Toggle File Explorer': 'Cmd+B',
    'Clear Chat': 'Cmd+L', 'Show Shortcuts': 'Cmd+/', 'Increase Font': 'Cmd+=',
    'Decrease Font': 'Cmd+-', 'Reset Font': 'Cmd+0', 'Stop Generation': 'Cmd+C',
  };
  const [keybindings, setKeybindings] = useState<Record<string, string>>(defaultKeybindings);

  const startRebind = (action: string) => {
    setRebindingKey(action);
    const handler = (e: KeyboardEvent) => {
      e.preventDefault();
      const parts: string[] = [];
      if (e.metaKey) parts.push('Cmd');
      if (e.ctrlKey) parts.push('Ctrl');
      if (e.altKey) parts.push('Alt');
      if (e.shiftKey) parts.push('Shift');
      const key = e.key === ' ' ? 'Space' : e.key.length === 1 ? e.key.toUpperCase() : e.key;
      if (!['Meta', 'Control', 'Alt', 'Shift'].includes(e.key)) parts.push(key);
      setKeybindings((p) => ({ ...p, [action]: parts.join('+') }));
      setRebindingKey(null);
      window.removeEventListener('keydown', handler);
    };
    setTimeout(() => window.addEventListener('keydown', handler), 100);
  };

  const statusColor = (s: string) => s === 'connected' ? '#22c55e' : s === 'error' ? '#ef4444' : '#eab308';

  return (
    <div className="overlay" onClick={close}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-layout">
          <nav className="settings-tabs">
            <div className="settings-tabs-header"><h2>Settings</h2></div>
            {TABS.map((t) => (
              <button key={t.id} className={`settings-tab ${activeTab === t.id ? 'settings-tab--active' : ''}`} onClick={() => setActiveTab(t.id)}>
                {t.label}
              </button>
            ))}
            <div className="settings-tabs-footer"><p className="settings-version">Wisp Desktop v0.2.0</p></div>
          </nav>

          <div className="settings-content">
            <div className="settings-content-header">
              <h3>{TABS.find((t) => t.id === activeTab)?.label}</h3>
              <button className="panel-close" onClick={close}>x</button>
            </div>
            <div className="settings-content-body">

              {/* General */}
              {activeTab === 'general' && (
                <div className="settings-section">
                  <label className="settings-field"><span className="settings-label">Server URL</span>
                    <input className="settings-input" value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} placeholder="http://localhost:8000" />
                  </label>
                  <label className="settings-field"><span className="settings-label">API Key</span>
                    <input className="settings-input" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="Enter API key..." />
                  </label>
                  <label className="settings-field"><span className="settings-label">Model</span>
                    <select className="settings-select" value={state.selectedModel} onChange={(e) => dispatch({ type: 'SET_MODEL', model: e.target.value })}>
                      {state.availableModels.length > 0 ? state.availableModels.map((m) => <option key={m} value={m}>{m}</option>) : <option value={state.selectedModel}>{state.selectedModel}</option>}
                    </select>
                  </label>
                  <label className="settings-field"><span className="settings-label">Temperature ({temperature.toFixed(1)})</span>
                    <input className="settings-slider" type="range" min="0" max="2" step="0.1" value={temperature} onChange={(e) => setTemperature(parseFloat(e.target.value))} />
                  </label>
                  <label className="settings-field settings-field--row">
                    <span className="settings-label">Show Thinking</span>
                    <button className={`settings-toggle ${state.showThinking ? 'settings-toggle--on' : ''}`} onClick={() => dispatch({ type: 'TOGGLE_THINKING' })}><span className="settings-toggle-knob" /></button>
                  </label>
                  <label className="settings-field settings-field--row">
                    <span className="settings-label">Plan Mode</span>
                    <button className={`settings-toggle ${state.planMode ? 'settings-toggle--on' : ''}`} onClick={() => dispatch({ type: 'TOGGLE_PLAN_MODE' })}><span className="settings-toggle-knob" /></button>
                  </label>
                  <label className="settings-field">
                    <div className="settings-label-row"><span className="settings-label">System Prompt</span><button className="settings-reset-btn" onClick={resetSystemPrompt}>Reset to default</button></div>
                    <textarea className="settings-textarea" value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} rows={6} placeholder="Custom system prompt..." />
                  </label>
                  <button className="settings-save-btn" onClick={handleSave} disabled={saved}>{saved ? 'Saved!' : 'Save'}</button>
                </div>
              )}

              {/* Appearance */}
              {activeTab === 'appearance' && (
                <div className="settings-section">
                  <label className="settings-field"><span className="settings-label">Theme</span><ThemeSelector /></label>
                  <label className="settings-field settings-field--row">
                    <span className="settings-label">Vim Mode</span>
                    <button className={`settings-toggle ${vimMode ? 'settings-toggle--on' : ''}`} onClick={() => setVimMode(!vimMode)}><span className="settings-toggle-knob" /></button>
                  </label>
                  <div className="settings-field">
                    <span className="settings-label">Keybindings</span>
                    <div className="settings-keybindings-table">
                      {Object.entries(keybindings).map(([action, binding]) => (
                        <div key={action} className="settings-kb-row">
                          <span className="settings-kb-action">{action}</span>
                          <button className={`settings-kb-binding ${rebindingKey === action ? 'settings-kb-binding--rebinding' : ''}`} onClick={() => startRebind(action)}>
                            {rebindingKey === action ? 'Press keys...' : binding}
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                  <button className="settings-save-btn" onClick={handleSave} disabled={saved}>{saved ? 'Saved!' : 'Save'}</button>
                </div>
              )}

              {/* Permissions */}
              {activeTab === 'permissions' && (
                <div className="settings-section">
                  <div className="settings-field">
                    <span className="settings-label">Permission Mode</span>
                    <div className="settings-permission-list">
                      {PERMISSION_MODES.map((mode) => (
                        <label key={mode.value} className={`settings-permission-option ${permissionMode === mode.value ? 'settings-permission-option--active' : ''}`}>
                          <input type="radio" name="permission" value={mode.value} checked={permissionMode === mode.value} onChange={() => setPermissionMode(mode.value as typeof state.permissionMode)} />
                          <div className="settings-permission-info">
                            <span className="settings-permission-label">{mode.label}</span>
                            <span className="settings-permission-desc">{mode.desc}</span>
                          </div>
                        </label>
                      ))}
                    </div>
                  </div>
                  <label className="settings-field settings-field--row">
                    <span className="settings-label">Auto-Approve Edits</span>
                    <button className={`settings-toggle ${permissionMode === 'auto_edit' ? 'settings-toggle--on' : ''}`} onClick={() => setPermissionMode(permissionMode === 'auto_edit' ? 'ask_all' : 'auto_edit')}><span className="settings-toggle-knob" /></button>
                  </label>
                  <div className="settings-permission-desc-block">Current: {PERMISSION_MODES.find((m) => m.value === permissionMode)?.desc}</div>
                  <button className="settings-save-btn" onClick={handleSave} disabled={saved}>{saved ? 'Saved!' : 'Save'}</button>
                </div>
              )}

              {/* Context */}
              {activeTab === 'context' && (
                <div className="settings-section">
                  <div className="settings-context-actions">
                    <button className="settings-btn-secondary" onClick={handleReloadContext} disabled={contextLoading}><RefreshCw size={14} /> Reload</button>
                    <button className="settings-btn-secondary" onClick={() => dispatch({ type: 'OPEN_OVERLAY', overlay: 'quickfile' })}><FileText size={14} /> Edit rules.md</button>
                  </div>
                  {contextLoading && <p className="settings-loading">Loading context...</p>}
                  {contextError && <p className="settings-error">{contextError}</p>}
                  {contextData && (<>
                    <div className="settings-context-files">
                      <h4 className="settings-subheading">Context Files ({contextData.files_found.length})</h4>
                      {contextData.files_found.length === 0 && <p className="settings-muted">No context files found in workspace.</p>}
                      {contextData.files_found.map((file) => (
                        <div key={file} className="settings-context-file">
                          <div className="settings-context-file-header" onClick={() => setExpandedContextFile(expandedContextFile === file ? null : file)}>
                            <FileText size={14} />
                            <span className="settings-context-file-path">{file}</span>
                            <span className="settings-context-file-badge">loaded</span>
                            <span className="settings-context-file-chevron">{expandedContextFile === file ? '▲' : '▼'}</span>
                          </div>
                          {expandedContextFile === file && (
                            <pre className="settings-context-preview">{contextData.content.slice(0, 500)}{contextData.content.length > 500 ? '\n... (truncated)' : ''}</pre>
                          )}
                        </div>
                      ))}
                    </div>
                    <div className="settings-project-info">
                      <h4 className="settings-subheading">Project Info</h4>
                      <div className="settings-info-row"><span className="settings-info-label">Workspace:</span><span className="settings-info-value">{state.workspacePath}</span></div>
                    </div>
                  </>)}
                </div>
              )}

              {/* Plugins */}
              {activeTab === 'plugins' && (
                <div className="settings-section">
                  <div className="settings-plugin-actions">
                    <button className="settings-btn-primary" onClick={() => setShowInstallForm(showInstallForm === 'marketplace' ? null : 'marketplace')}><Download size={14} /> From Marketplace</button>
                    <button className="settings-btn-secondary" onClick={() => setShowInstallForm(showInstallForm === 'directory' ? null : 'directory')}><Plus size={14} /> From Directory</button>
                  </div>
                  {showInstallForm === 'marketplace' && (
                    <div className="settings-marketplace">
                      <div className="settings-market-search">
                        <Search size={14} />
                        <input className="settings-input" value={marketQuery} onChange={(e) => setMarketQuery(e.target.value)} placeholder="Search marketplace..." onKeyDown={(e) => e.key === 'Enter' && handleMarketSearch()} />
                        <button className="settings-btn-secondary" onClick={handleMarketSearch}>Search</button>
                      </div>
                      {marketResults.map((item) => (
                        <div key={item.name} className="settings-market-item">
                          <div className="settings-market-meta">
                            <span className="settings-market-name">{item.name}</span>
                            <span className="settings-market-version">v{item.version} by {item.author}</span>
                            <span className="settings-market-desc">{item.description}</span>
                          </div>
                          <button className="settings-btn-primary" onClick={async () => { await api.installPlugin(item.name); loadPlugins(); }}>Install</button>
                        </div>
                      ))}
                      {marketResults.length === 0 && marketQuery.length >= 2 && <p className="settings-muted">No results for "{marketQuery}"</p>}
                    </div>
                  )}
                  {showInstallForm === 'directory' && (
                    <div className="settings-install-form">
                      <label className="settings-field"><span className="settings-label">Plugin Directory Path</span>
                        <div className="settings-install-row">
                          <input className="settings-input" value={installPath} onChange={(e) => setInstallPath(e.target.value)} placeholder="/path/to/plugin" />
                          <button className="settings-btn-primary" onClick={handleInstallFromDir}>Install</button>
                        </div>
                      </label>
                    </div>
                  )}
                  {pluginsLoading && <p className="settings-loading">Loading plugins...</p>}
                  {pluginError && <p className="settings-error">{pluginError}</p>}
                  <div className="settings-plugin-list">
                    {plugins.map((p) => (
                      <div key={p.name} className="settings-plugin-item">
                        <div className="settings-plugin-meta">
                          <span className="settings-plugin-name">{p.name}</span>
                          <span className="settings-plugin-version">v{p.version} by {p.author}</span>
                          {p.description && <span className="settings-plugin-desc">{p.description}</span>}
                        </div>
                        <div className="settings-plugin-controls">
                          <button className={`settings-toggle ${p.enabled ? 'settings-toggle--on' : ''}`} onClick={() => handleTogglePlugin(p.name, p.enabled)}><span className="settings-toggle-knob" /></button>
                          <button className="settings-btn-danger" onClick={() => handleUninstallPlugin(p.name)}><Trash2 size={14} /></button>
                        </div>
                      </div>
                    ))}
                    {!pluginsLoading && plugins.length === 0 && <p className="settings-muted">No plugins installed.</p>}
                  </div>
                </div>
              )}

              {/* MCP */}
              {activeTab === 'mcp' && (
                <div className="settings-section">
                  <div className="settings-mcp-actions">
                    <button className="settings-btn-primary" onClick={() => setShowAddMcp(!showAddMcp)}><Plus size={14} /> Add Server</button>
                  </div>
                  {showAddMcp && (
                    <div className="settings-mcp-form">
                      <h4 className="settings-subheading">Add MCP Server</h4>
                      <label className="settings-field"><span className="settings-label">Name</span><input className="settings-input" value={mcpForm.name} onChange={(e) => setMcpForm({ ...mcpForm, name: e.target.value })} placeholder="my-server" /></label>
                      <label className="settings-field"><span className="settings-label">Transport</span>
                        <select className="settings-select" value={mcpForm.transport} onChange={(e) => setMcpForm({ ...mcpForm, transport: e.target.value })}>
                          <option value="stdio">stdio</option><option value="sse">sse</option><option value="streamable-http">streamable-http</option>
                        </select>
                      </label>
                      {mcpForm.transport === 'stdio'
                        ? <label className="settings-field"><span className="settings-label">Command</span><input className="settings-input" value={mcpForm.command} onChange={(e) => setMcpForm({ ...mcpForm, command: e.target.value })} placeholder="npx @acme/mcp-server" /></label>
                        : <label className="settings-field"><span className="settings-label">URL</span><input className="settings-input" value={mcpForm.url} onChange={(e) => setMcpForm({ ...mcpForm, url: e.target.value })} placeholder="https://example.com/mcp" /></label>
                      }
                      <label className="settings-field"><span className="settings-label">Auth Method</span>
                        <select className="settings-select" value={mcpForm.auth} onChange={(e) => setMcpForm({ ...mcpForm, auth: e.target.value })}>
                          <option value="none">None</option><option value="bearer">Bearer Token</option><option value="oauth">OAuth</option><option value="x509">X.509</option>
                        </select>
                      </label>
                      <label className="settings-field settings-field--row">
                        <span className="settings-label">Always Load</span>
                        <button className={`settings-toggle ${mcpForm.alwaysLoad ? 'settings-toggle--on' : ''}`} onClick={() => setMcpForm({ ...mcpForm, alwaysLoad: !mcpForm.alwaysLoad })}><span className="settings-toggle-knob" /></button>
                      </label>
                      <div className="settings-form-actions">
                        <button className="settings-btn-primary" onClick={handleAddMCPServer}>Add Server</button>
                        <button className="settings-btn-secondary" onClick={() => setShowAddMcp(false)}>Cancel</button>
                      </div>
                    </div>
                  )}
                  {mcpLoading && <p className="settings-loading">Loading MCP servers...</p>}
                  {mcpError && <p className="settings-error">{mcpError}</p>}
                  <div className="settings-mcp-list">
                    {mcpServers.map((server) => {
                      const tr = testResults[server.name];
                      return (
                        <div key={server.name} className="settings-mcp-item">
                          <div className="settings-mcp-status">
                            <span className="settings-mcp-dot" style={{ backgroundColor: statusColor(server.status) }} />
                            <div className="settings-mcp-meta">
                              <span className="settings-mcp-name">{server.name}</span>
                              <span className="settings-mcp-details">{server.transport} · {server.tool_count} tools{server.latency_ms != null ? ` · ${server.latency_ms}ms` : ''}</span>
                            </div>
                          </div>
                          <div className="settings-mcp-controls">
                            {tr && <span className={`settings-mcp-test-result ${tr.ok ? 'settings-mcp-test--ok' : 'settings-mcp-test--fail'}`}>{tr.ok ? `${tr.latency_ms}ms` : 'Fail'}</span>}
                            <button className="settings-btn-secondary" onClick={() => handleTestMCPServer(server.name)} disabled={testingMcp === server.name}>{testingMcp === server.name ? '...' : 'Test'}</button>
                            <button className="settings-btn-danger" onClick={() => handleRemoveMCPServer(server.name)}><Trash2 size={14} /></button>
                          </div>
                        </div>
                      );
                    })}
                    {!mcpLoading && mcpServers.length === 0 && <p className="settings-muted">No MCP servers configured.</p>}
                  </div>
                  <div className="settings-mcp-health">
                    <h4 className="settings-subheading">Server Health</h4>
                    <p className="settings-muted">Servers auto-retry up to 3 times with exponential backoff. Disconnected servers do not block startup.</p>
                  </div>
                </div>
              )}

              {/* Hooks */}
              {activeTab === 'hooks' && (
                <div className="settings-section">
                  <div className="settings-hook-actions">
                    <button className="settings-btn-primary" onClick={() => setShowAddHook(!showAddHook)}><Plus size={14} /> Add Hook</button>
                  </div>
                  {showAddHook && (
                    <div className="settings-hook-form">
                      <h4 className="settings-subheading">Add Hook</h4>
                      <label className="settings-field"><span className="settings-label">Name</span><input className="settings-input" value={hookForm.name} onChange={(e) => setHookForm({ ...hookForm, name: e.target.value })} placeholder="my-hook" /></label>
                      <label className="settings-field"><span className="settings-label">Event</span>
                        <select className="settings-select" value={hookForm.event} onChange={(e) => setHookForm({ ...hookForm, event: e.target.value })}>
                          {HOOK_EVENTS.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
                        </select>
                      </label>
                      <label className="settings-field"><span className="settings-label">Command / Path</span><input className="settings-input" value={hookForm.command} onChange={(e) => setHookForm({ ...hookForm, command: e.target.value })} placeholder="./hooks/my-hook.sh" /></label>
                      <label className="settings-field"><span className="settings-label">Timeout (seconds)</span><input className="settings-input" type="number" value={hookForm.timeout} onChange={(e) => setHookForm({ ...hookForm, timeout: parseInt(e.target.value) || 30 })} min={1} max={300} /></label>
                      <label className="settings-field"><span className="settings-label">Matcher (regex)</span><input className="settings-input" value={hookForm.matcher} onChange={(e) => setHookForm({ ...hookForm, matcher: e.target.value })} placeholder=".*" /></label>
                      <label className="settings-field settings-field--row">
                        <span className="settings-label">Enabled</span>
                        <button className={`settings-toggle ${hookForm.enabled ? 'settings-toggle--on' : ''}`} onClick={() => setHookForm({ ...hookForm, enabled: !hookForm.enabled })}><span className="settings-toggle-knob" /></button>
                      </label>
                      <div className="settings-form-actions">
                        <button className="settings-btn-primary" onClick={handleAddHook}>Add Hook</button>
                        <button className="settings-btn-secondary" onClick={() => setShowAddHook(false)}>Cancel</button>
                      </div>
                    </div>
                  )}
                  {hooksLoading && <p className="settings-loading">Loading hooks...</p>}
                  <div className="settings-hook-list">
                    {hooks.map((hook) => (
                      <div key={hook.name} className="settings-hook-item">
                        <div className="settings-hook-meta">
                          <span className="settings-hook-name">{hook.name}</span>
                          <span className="settings-hook-event">{hook.event}</span>
                          <span className="settings-hook-command">{hook.command}</span>
                          {hook.matcher && <span className="settings-hook-matcher">matches: {hook.matcher}</span>}
                        </div>
                        <div className="settings-hook-controls">
                          <button className={`settings-toggle ${hook.enabled ? 'settings-toggle--on' : ''}`} onClick={() => handleToggleHook(hook.name, hook.enabled)}><span className="settings-toggle-knob" /></button>
                          <button className="settings-btn-secondary" onClick={() => handleTestHook(hook.name)}>Test</button>
                          <button className="settings-btn-danger" onClick={() => handleRemoveHook(hook.name)}><Trash2 size={14} /></button>
                        </div>
                      </div>
                    ))}
                    {!hooksLoading && hooks.length === 0 && <p className="settings-muted">No hooks configured.</p>}
                  </div>
                  <div className="settings-hook-logs">
                    <h4 className="settings-subheading">Execution Log (last {hookLogs.length})</h4>
                    {hookLogs.length === 0 ? <p className="settings-muted">No hook executions yet.</p> : (
                      <div className="settings-hook-log-list">
                        {hookLogs.map((log, i) => (
                          <div key={i} className="settings-hook-log-entry">
                            <span className="settings-hook-log-time">{new Date(log.timestamp).toLocaleTimeString()}</span>
                            <span className="settings-hook-log-hook">{log.hook_name}</span>
                            <span className="settings-hook-log-event">{log.event}</span>
                            <span className="settings-hook-log-result">{log.result}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
