import React, { useCallback } from 'react';
import { useAppState } from '../../state/context.js';
import { Folder, ChevronDown } from '../../icons/index.js';
import './ProjectContextBar.css';

export const ProjectContextBar: React.FC = () => {
  const { state, dispatch } = useAppState();

  const handleSwitchProject = useCallback(async () => {
    if (window.wisp?.selectDirectory) {
      const dirPath = await window.wisp.selectDirectory();
      if (!dirPath) return;
      const baseUrl = state.serverUrl.replace(/\/$/, '');
      const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';
      try {
        const resp = await fetch(`${baseUrl}/api/workspace${params}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : {}),
          },
          body: JSON.stringify({ path: dirPath }),
        });
        if (resp.ok) {
          const data = await resp.json() as { path?: string };
          if (data.path) dispatch({ type: 'SET_WORKSPACE', path: data.path });
        }
      } catch { /* ignore */ }
    }
  }, [state.serverUrl, state.apiKey, dispatch]);

  const label = state.workspacePath
    ? state.workspacePath.split('/').pop() || state.workspacePath
    : 'Select project...';

  return (
    <div className="project-context-bar">
      <button className="project-context-btn" onClick={handleSwitchProject}>
        <Folder size={13} />
        <span>{label}</span>
        <ChevronDown size={10} />
      </button>
    </div>
  );
};
