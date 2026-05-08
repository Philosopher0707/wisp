import React, { useState } from 'react';
import { useAppState } from '../state/context.js';
import './SettingsModal.css';

export const SettingsModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [serverUrl, setServerUrl] = useState(state.serverUrl);
  const [apiKey, setApiKey] = useState(state.apiKey);

  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

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
          <div className="settings-info">
            <p className="settings-version">Wisp Desktop v0.1.0</p>
            <p className="settings-platform">
              Platform: {window.wisp?.platform || 'unknown'}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
