import React, { useState, useCallback, useEffect } from 'react';
import { useAppState } from '../state/context.js';
import { X } from '../icons/index.js';
import { relativeTime } from '../utils/time.js';
import type { Checkpoint } from '../state/types.js';
import './CheckpointPanel.css';

interface ConfirmState {
  id: string;
  action: 'restore' | 'drop';
}

function highlightDiffLine(line: string): { cls: string; text: string } {
  if (line.startsWith('+')) return { cls: 'cp-diff-add', text: line };
  if (line.startsWith('-')) return { cls: 'cp-diff-rem', text: line };
  if (line.startsWith('@@')) return { cls: 'cp-diff-hunk', text: line };
  return { cls: '', text: line };
}

function shortHash(id: string): string {
  return id.length > 7 ? id.slice(0, 7) : id;
}

export const CheckpointPanel: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [expandedDiff, setExpandedDiff] = useState<string | null>(null);
  const [diffContent, setDiffContent] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [dropping, setDropping] = useState(false);

  const base = state.serverUrl.replace(/\/$/, '');
  const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';

  const fetchDiff = useCallback(async (id: string) => {
    setDiffLoading(true);
    setDiffContent(null);
    try {
      const resp = await fetch(`${base}/api/checkpoints/${encodeURIComponent(id)}/diff${params}`);
      if (resp.ok) {
        const text = await resp.text();
        setDiffContent(text);
      }
    } catch {
      setDiffContent('Failed to load diff.');
    }
    setDiffLoading(false);
  }, [base, params]);

  const handleDiff = (id: string) => {
    if (expandedDiff === id) {
      setExpandedDiff(null);
      setDiffContent(null);
      return;
    }
    setExpandedDiff(id);
    setConfirm(null);
    fetchDiff(id);
  };

  const handleRestore = async (id: string) => {
    setConfirm(null);
    setRestoring(true);
    try {
      const resp = await fetch(`${base}/api/checkpoints/${encodeURIComponent(id)}/restore${params}`, {
        method: 'POST',
      });
      if (resp.ok) {
        dispatch({ type: 'SET_CHECKPOINTS', checkpoints: [] });
      }
    } catch { /* ignore */ }
    setRestoring(false);
  };

  const handleDrop = async (id: string) => {
    setConfirm(null);
    setDropping(true);
    try {
      const resp = await fetch(`${base}/api/checkpoints/${encodeURIComponent(id)}${params}`, {
        method: 'DELETE',
      });
      if (resp.ok) {
        dispatch({ type: 'REMOVE_CHECKPOINT', id });
      }
    } catch { /* ignore */ }
    setDropping(false);
  };

  const handleClose = () => {
    dispatch({ type: 'TOGGLE_CHECKPOINT_PANEL' });
  };

  const showConfirm = (id: string, action: 'restore' | 'drop') => {
    setConfirm({ id, action });
    setExpandedDiff(null);
    setDiffContent(null);
  };

  const renderCheckpoint = (cp: Checkpoint) => {
    const ts = new Date(cp.timestamp).getTime();
    const isRestoreConfirm = confirm?.id === cp.id && confirm.action === 'restore';
    const isDropConfirm = confirm?.id === cp.id && confirm.action === 'drop';
    const isDiffOpen = expandedDiff === cp.id;

    return (
      <div key={cp.id} className="cp-item">
        <div className="cp-item-top">
          <span className="cp-item-desc">{cp.description || cp.toolName}</span>
          <span className="cp-item-time">{relativeTime(ts)}</span>
        </div>
        <div className="cp-item-meta">
          <span className="cp-item-id">{shortHash(cp.id)}</span>
          {cp.fileCount > 0 && (
            <span className="cp-file-count">{cp.fileCount} file{cp.fileCount !== 1 ? 's' : ''}</span>
          )}
        </div>
        <div className="cp-item-actions">
          <button
            className="cp-btn cp-btn--restore"
            onClick={() => showConfirm(cp.id, 'restore')}
            disabled={restoring}
          >
            Restore
          </button>
          <button
            className="cp-btn cp-btn--diff"
            onClick={() => handleDiff(cp.id)}
          >
            {isDiffOpen ? 'Hide diff' : 'Diff'}
          </button>
          <button
            className="cp-btn cp-btn--drop"
            onClick={() => showConfirm(cp.id, 'drop')}
            disabled={dropping}
          >
            Drop
          </button>
        </div>

        {isRestoreConfirm && (
          <div className="cp-restore-warning">
            This will revert all files to this checkpoint state. Current changes will be lost.
            <div className="cp-restore-warning-actions">
              <button
                className="cp-restore-confirm-btn"
                onClick={() => handleRestore(cp.id)}
                disabled={restoring}
              >
                {restoring ? 'Restoring...' : 'Confirm Restore'}
              </button>
              <button className="cp-restore-cancel-btn" onClick={() => setConfirm(null)}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {isDropConfirm && (
          <div className="cp-drop-warning">
            Delete this checkpoint? This cannot be undone.
            <div className="cp-drop-warning-actions">
              <button
                className="cp-drop-confirm-btn"
                onClick={() => handleDrop(cp.id)}
                disabled={dropping}
              >
                {dropping ? 'Dropping...' : 'Delete'}
              </button>
              <button className="cp-drop-cancel-btn" onClick={() => setConfirm(null)}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {isDiffOpen && (
          <div className="cp-diff">
            <div className="cp-diff-header">
              <span className="cp-diff-title">Changes</span>
              <button className="cp-diff-collapse-btn" onClick={() => handleDiff(cp.id)}>
                Collapse
              </button>
            </div>
            {diffLoading ? (
              <div className="cp-diff-loading">Loading diff...</div>
            ) : diffContent ? (
              <div className="cp-diff-content">
                {diffContent.split('\n').map((line, i) => {
                  const { cls, text } = highlightDiffLine(line);
                  return (
                    <div key={i} className={`cp-diff-line ${cls}`}>
                      <span className="cp-diff-ln">{i + 1}</span>
                      <span className="cp-diff-text">{text || ' '}</span>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        )}
      </div>
    );
  };

  const sorted = [...state.checkpoints].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  );

  return (
    <aside className="checkpoint-panel">
      <div className="cp-header">
        <span className="cp-title">Checkpoints</span>
        <button className="cp-close-btn" onClick={handleClose} title="Close">
          <X size={14} />
        </button>
      </div>
      <div className="cp-body">
        {sorted.length === 0 ? (
          <p className="cp-empty">
            No checkpoints yet. Checkpoints are created automatically before file modifications.
          </p>
        ) : (
          sorted.map(renderCheckpoint)
        )}
      </div>
    </aside>
  );
};
