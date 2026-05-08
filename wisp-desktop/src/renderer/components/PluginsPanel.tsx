import React from 'react';
import { useAppState } from '../state/context.js';
import { Grid3x3, Bot, Shield, Folder, ExternalLink } from '../icons/index.js';
import './PluginsPanel.css';

interface PluginItem {
  icon: React.FC<{ size?: number }>;
  name: string;
  description: string;
  enabled: boolean;
}

const plugins: PluginItem[] = [
  { icon: Bot, name: 'Automations', description: 'Run automated tool pipelines', enabled: true },
  { icon: Shield, name: 'Security Scanner', description: 'Review code for vulnerabilities', enabled: true },
  { icon: Folder, name: 'File Manager', description: 'Browse and edit workspace files', enabled: true },
  { icon: ExternalLink, name: 'Web Fetch', description: 'Fetch and parse web pages', enabled: false },
  { icon: Grid3x3, name: 'Coming Soon...', description: 'More plugins in development', enabled: false },
];

export const PluginsPanel: React.FC = () => {
  const { dispatch } = useAppState();
  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  return (
    <div className="overlay" onClick={close}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <h2>Plugins</h2>
          <button className="panel-close" onClick={close}>×</button>
        </div>
        <div className="panel-body">
          {plugins.map((p) => (
            <div key={p.name} className="plugin-item">
              <p.icon size={20} />
              <div className="plugin-meta">
                <span className="plugin-name">{p.name}</span>
                <span className="plugin-desc">{p.description}</span>
              </div>
              <span className={`plugin-badge ${p.enabled ? 'plugin-badge--on' : 'plugin-badge--off'}`}>
                {p.enabled ? 'ON' : 'OFF'}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
