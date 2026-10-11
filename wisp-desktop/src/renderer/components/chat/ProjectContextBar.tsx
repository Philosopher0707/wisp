import React, { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi } from '../../hooks/useApi.js';
import { Folder, ChevronDown } from '../../icons/index.js';
import './ProjectContextBar.css';

export const ProjectContextBar: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!error) return undefined;
    const t = setTimeout(() => setError(null), 8000);
    return () => clearTimeout(t);
  }, [error]);

  const handleSwitchProject = useCallback(async () => {
    if (!window.wisp?.selectDirectory || busy) return;
    const dirPath = await window.wisp.selectDirectory();
    if (!dirPath) return;
    setBusy(true);
    try {
      const applied = await api.setWorkspace(dirPath);
      dispatch({ type: 'SET_WORKSPACE', path: applied });
      setError(null);
      void window.wisp?.setSavedWorkspace?.(applied);
    } catch (e) {
      // A refused switch used to do nothing at all, which looked like a dead button.
      setError(e instanceof Error ? e.message : 'Could not switch the project folder.');
    } finally {
      setBusy(false);
    }
  }, [api, busy, dispatch]);

  const label = state.workspacePath
    ? state.workspacePath.split('/').pop() || state.workspacePath
    : 'Select project...';

  return (
    <div className="project-context-bar">
      <button className="project-context-btn" onClick={handleSwitchProject} disabled={busy} title={state.workspacePath || 'Choose the project folder Wisp works in'}>
        <Folder size={13} />
        <span>{label}</span>
        <ChevronDown size={10} />
      </button>
      {error && <p className="project-context-error" role="alert">{error}</p>}
    </div>
  );
};
