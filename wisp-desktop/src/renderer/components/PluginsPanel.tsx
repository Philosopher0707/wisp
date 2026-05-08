import React, { useState } from 'react';
import { useAppState } from '../state/context.js';
import { Grid3x3, Bot, Shield, Folder, ExternalLink } from '../icons/index.js';
import './PluginsPanel.css';

interface PluginItem {
  icon: React.FC<{ size?: number }>;
  name: string;
  description: string;
}

const plugins: PluginItem[] = [
  { icon: Bot, name: 'Automations', description: 'Run automated tool pipelines' },
  { icon: Shield, name: 'Security Scanner', description: 'Review code for vulnerabilities' },
  { icon: Folder, name: 'File Manager', description: 'Browse and edit workspace files' },
  { icon: ExternalLink, name: 'Web Fetch', description: 'Fetch and parse web pages' },
  { icon: Grid3x3, name: 'Coming Soon...', description: 'More plugins in development' },
];

export const PluginsPanel: React.FC = () => {
  const { dispatch } = useAppState();
  const [toggled, setToggled] = useState<Set<string>>(
    new Set(plugins.filter((_, i) => i < 3).map((p) => p.name)),
  );

  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  const toggle = (name: string) => {
    const next = new Set(toggled);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setToggled(next);
  };

  return (
    <div className="overlay" onClick={close}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <h2>Plugins</h2>
          <button className="panel-close" onClick={close}>×</button>
        </div>
        <div className="panel-body">
          {plugins.map((p) => {
            const on = toggled.has(p.name);
            return (
              <div
                key={p.name}
                className={`plugin-item ${on ? 'plugin-item--on' : ''}`}
                onClick={() => toggle(p.name)}
              >
                <p.icon size={20} />
                <div className="plugin-meta">
                  <span className="plugin-name">{p.name}</span>
                  <span className="plugin-desc">{p.description}</span>
                </div>
                <span className={`plugin-badge ${on ? 'plugin-badge--on' : 'plugin-badge--off'}`}>
                  {on ? 'ON' : 'OFF'}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
