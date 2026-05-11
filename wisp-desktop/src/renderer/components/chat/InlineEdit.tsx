import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useAppState } from '../../state/context.js';
import { DiffPreview } from './DiffPreview.js';
import './InlineEdit.css';

interface InlineEditProps {
  visible: boolean;
  onClose: () => void;
}

export const InlineEdit: React.FC<InlineEditProps> = ({ visible, onClose }) => {
  const { state, dispatch } = useAppState();
  const [path, setPath] = useState('');
  const [selection, setSelection] = useState('');
  const [instruction, setInstruction] = useState('');
  const [processing, setProcessing] = useState(false);
  const [result, setResult] = useState<{ newText: string; diff: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const instRef = useRef<HTMLTextAreaElement>(null);

  // Pre-fill path from selected file
  useEffect(() => {
    if (visible && state.selectedFilePath) {
      setPath(state.selectedFilePath);
    }
  }, [visible, state.selectedFilePath]);

  // Focus instruction input on open
  useEffect(() => {
    if (visible && instRef.current) {
      instRef.current.focus();
    }
  }, [visible]);

  // Escape to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!visible) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        handleClose();
      }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && instruction.trim() && !processing) {
        e.preventDefault();
        submitEdit();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [visible, instruction, processing, path, selection]);

  const handleClose = useCallback(() => {
    setResult(null);
    setError(null);
    setProcessing(false);
    setInstruction('');
    setSelection('');
    dispatch({ type: 'CANCEL_INLINE_EDIT' });
    onClose();
  }, [dispatch, onClose]);

  const submitEdit = useCallback(async () => {
    if (!instruction.trim() || !selection.trim() || !path.trim() || processing) return;
    setProcessing(true);
    setError(null);
    setResult(null);

    try {
      const base = state.serverUrl.replace(/\/$/, '');
      const resp = await fetch(`${base}/api/edit/inline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: path.trim(),
          selection: selection.trim(),
          instruction: instruction.trim(),
          model: state.selectedModel,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      setResult({ newText: data.new_text, diff: data.diff });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setError(msg);
    } finally {
      setProcessing(false);
    }
  }, [instruction, selection, path, processing, state.serverUrl, state.selectedModel]);

  const applyEdit = useCallback(async () => {
    if (!result) return;
    setApplying(true);

    try {
      const base = state.serverUrl.replace(/\/$/, '');
      const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';
      const resp = await fetch(`${base}/api/files${params}&path=${encodeURIComponent(path.trim())}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : {}),
        },
        body: JSON.stringify({ content: result.newText }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      handleClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setError(msg);
    } finally {
      setApplying(false);
    }
  }, [result, state.serverUrl, state.apiKey, path, handleClose]);

  if (!visible) return null;

  const showForm = !result;
  const showResult = result !== null;

  return (
    <div className="inline-edit-overlay" onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}>
      <div className="inline-edit-panel">
        <div className="inline-edit-header">
          <span className="inline-edit-title">Inline Edit</span>
          <button className="inline-edit-close" onClick={handleClose}>x</button>
        </div>

        {showForm && (
          <>
            <div className="inline-edit-field">
              <label className="inline-edit-label">File path</label>
              <input
                className="inline-edit-text-input"
                placeholder="src/app.ts"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                disabled={processing}
              />
            </div>

            <div className="inline-edit-field">
              <label className="inline-edit-label">Code to edit</label>
              <textarea
                className="inline-edit-code-input"
                placeholder="Paste the code you want to change..."
                value={selection}
                onChange={(e) => setSelection(e.target.value)}
                rows={6}
                disabled={processing}
              />
            </div>

            <div className="inline-edit-field">
              <label className="inline-edit-label">Instruction</label>
              <textarea
                ref={instRef}
                className="inline-edit-instruction-input"
                placeholder="Describe the change... (Cmd+Enter to submit)"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                rows={3}
                disabled={processing}
              />
            </div>

            {processing && (
              <div className="inline-edit-processing">
                <span className="inline-edit-spinner" />
                Generating edit...
              </div>
            )}

            {error && <div className="inline-edit-error">{error}</div>}

            <div className="inline-edit-submit-row">
              <span className="inline-edit-hint">Cmd+Enter to generate</span>
              <button
                className="inline-edit-generate-btn"
                onClick={submitEdit}
                disabled={!instruction.trim() || !selection.trim() || !path.trim() || processing}
              >
                Generate
              </button>
            </div>
          </>
        )}

        {showResult && (
          <div className="inline-edit-result">
            <div className="inline-edit-section-label">Preview — {path}</div>
            <pre className="inline-edit-code inline-edit-new">
              {result.newText.slice(0, 800)}{result.newText.length > 800 ? '...' : ''}
            </pre>
            <DiffPreview
              diff={result.diff}
              path={path}
              isNew={false}
              compact
            />
            {error && <div className="inline-edit-error">{error}</div>}
            <div className="inline-edit-actions">
              <button className="inline-edit-cancel-btn" onClick={handleClose}>Discard</button>
              <button className="inline-edit-apply-btn" onClick={applyEdit} disabled={applying}>
                {applying ? 'Applying...' : 'Apply'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
