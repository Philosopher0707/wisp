import React, { useState } from 'react';
import { useAppState } from '../state/context.js';
import './SettingsModal.css';

const STORAGE_KEY_SERVER = 'wisp_server_url';
const STORAGE_KEY_APIKEY = 'wisp_api_key';

const DEFAULT_SYSTEM_PROMPT = 'You are Wisp, an expert software engineer AI assistant. You write clean, secure, well-tested code. You think step-by-step before implementing. You use tools to read, edit, and run code. You explain your reasoning briefly and focus on delivering working solutions.';

export const SettingsModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [serverUrl, setServerUrl] = useState(
    localStorage.getItem(STORAGE_KEY_SERVER) || state.serverUrl,
  );
  const [apiKey, setApiKey] = useState(
    localStorage.getItem(STORAGE_KEY_APIKEY) || state.apiKey,
  );
  const [systemPrompt, setSystemPrompt] = useState(
    state.systemPrompt || DEFAULT_SYSTEM_PROMPT,
  );
  const [temperature, setTemperature] = useState(() => {
    const stored = localStorage.getItem('wisp_temperature');
    return stored ? parseFloat(stored) : 0.7;
  });
  const [saved, setSaved] = useState(false);

  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  const handleSave = () => {
    const url = serverUrl.replace(/\/$/, '');
    localStorage.setItem(STORAGE_KEY_SERVER, url);
    if (apiKey) {
      localStorage.setItem(STORAGE_KEY_APIKEY, apiKey);
    } else {
      localStorage.removeItem(STORAGE_KEY_APIKEY);
    }
    localStorage.setItem('wisp_system_prompt', systemPrompt);
    localStorage.setItem('wisp_temperature', String(temperature));
    dispatch({ type: 'SET_SYSTEM_PROMPT', prompt: systemPrompt });
    setSaved(true);
    setTimeout(() => window.location.reload(), 300);
  };

  const resetSystemPrompt = () => {
    setSystemPrompt(DEFAULT_SYSTEM_PROMPT);
  };

  return (
    <div className="overlay" onClick={close}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <h2>Settings</h2>
          <button className="panel-close" onClick={close}>×</button>
        </div>
        <div className="settings-body">
          <label className="settings-field">
            <span className="settings-label">Server URL</span>
            <input
              className="settings-input"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="http://localhost:8000"
            />
          </label>
          <label className="settings-field">
            <span className="settings-label">API Key</span>
            <input
              className="settings-input"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter API key..."
            />
          </label>
          <label className="settings-field">
            <span className="settings-label">
              Temperature ({temperature.toFixed(1)})
            </span>
            <input
              className="settings-slider"
              type="range"
              min="0"
              max="2"
              step="0.1"
              value={temperature}
              onChange={(e) => setTemperature(parseFloat(e.target.value))}
            />
          </label>
          <label className="settings-field">
            <div className="settings-label-row">
              <span className="settings-label">System Prompt</span>
              <button className="settings-reset-btn" onClick={resetSystemPrompt}>
                Reset to default
              </button>
            </div>
            <textarea
              className="settings-textarea"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              placeholder="Custom system prompt..."
            />
          </label>
          <button className="settings-save-btn" onClick={handleSave} disabled={saved}>
            {saved ? 'Saved — Reloading...' : 'Save & Reload'}
          </button>
          <div className="settings-info">
            <p className="settings-version">Wisp Desktop v0.1.0</p>
            <p className="settings-platform">
              Platform: {window.wisp?.platform || 'browser'}
            </p>
            <p className="settings-ws">Workspace: {state.workspacePath}</p>
          </div>
        </div>
      </div>
    </div>
  );
};
