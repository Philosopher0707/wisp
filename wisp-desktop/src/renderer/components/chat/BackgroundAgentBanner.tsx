import React, { useState, useEffect, useCallback } from 'react';
import { useAppState } from '../../state/context.js';
import './BackgroundAgentBanner.css';

interface BgRun {
  id: string;
  prompt: string;
  model: string;
  status: string;
  content: string;
  files_changed: string[];
  error: string | null;
  iterations: number;
  duration_ms: number;
  created_at: number;
  finished_at: number | null;
}

export const BackgroundAgentBanner: React.FC = () => {
  const { state } = useAppState();
  const [runs, setRuns] = useState<BgRun[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [notified, setNotified] = useState<Set<string>>(new Set());

  const fetchRuns = useCallback(async () => {
    try {
      const base = state.serverUrl.replace(/\/$/, '');
      const resp = await fetch(`${base}/api/runs`);
      if (!resp.ok) return;
      const data = await resp.json();
      const active = (data.runs || []).filter(
        (r: BgRun) => r.status === 'running' || r.status === 'done' || r.status === 'failed'
      );
      setRuns(active);

      // Notify for newly completed runs
      for (const r of active) {
        if ((r.status === 'done' || r.status === 'failed') && !notified.has(r.id)) {
          setNotified((prev) => new Set(prev).add(r.id));
          // Desktop notification
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification(`Agent ${r.status === 'done' ? 'complete' : 'failed'}`, {
              body: r.prompt.slice(0, 100),
            });
          }
        }
      }
    } catch { /* ignore */ }
  }, [state.serverUrl, notified]);

  useEffect(() => {
    fetchRuns();
    const interval = setInterval(fetchRuns, 3000);
    return () => clearInterval(interval);
  }, [fetchRuns]);

  // Request notification permission
  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  const cancelRun = async (runId: string) => {
    try {
      const base = state.serverUrl.replace(/\/$/, '');
      await fetch(`${base}/api/run/${encodeURIComponent(runId)}`, { method: 'DELETE' });
      fetchRuns();
    } catch { /* ignore */ }
  };

  const activeRuns = runs.filter((r) => r.status === 'running');
  const completedRuns = runs.filter((r) => r.status === 'done' || r.status === 'failed');
  if (runs.length === 0) return null;

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60000).toFixed(1)}m`;
  };

  return (
    <div className="bg-agent-banner">
      {activeRuns.map((run) => (
        <div key={run.id} className="bg-run-item bg-run-item--running">
          <div className="bg-run-status">
            <span className="bg-run-spinner" />
            <span className="bg-run-label">Running: {run.prompt.slice(0, 60)}...</span>
          </div>
          <div className="bg-run-meta">
            <span>{run.model}</span>
            <span>· {run.iterations} turns</span>
            <button className="bg-run-cancel" onClick={() => cancelRun(run.id)}>Cancel</button>
          </div>
        </div>
      ))}
      {completedRuns.slice(0, 5).map((run) => (
        <div
          key={run.id}
          className={`bg-run-item bg-run-item--${run.status}`}
          onClick={() => setExpandedId(expandedId === run.id ? null : run.id)}
        >
          <div className="bg-run-status">
            <span className={`bg-run-dot bg-run-dot--${run.status}`} />
            <span className="bg-run-label">
              {run.status === 'done' ? 'Done' : 'Failed'}: {run.prompt.slice(0, 60)}...
            </span>
          </div>
          <div className="bg-run-meta">
            <span>{run.model}</span>
            <span>· {formatDuration(run.duration_ms)}</span>
            {run.files_changed.length > 0 && (
              <span>· {run.files_changed.length} files</span>
            )}
            <button className="bg-run-dismiss" onClick={(e) => {
              e.stopPropagation();
              setRuns((prev) => prev.filter((r) => r.id !== run.id));
            }}>Dismiss</button>
          </div>
          {expandedId === run.id && (
            <div className="bg-run-detail">
              {run.error && <div className="bg-run-error">{run.error}</div>}
              {run.files_changed.length > 0 && (
                <div className="bg-run-files">
                  Files: {run.files_changed.join(', ')}
                </div>
              )}
              {run.content && (
                <pre className="bg-run-content">{run.content.slice(0, 500)}</pre>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
